"""Gemini-Backend ohne Netz: httpx.MockTransport spielt die API."""
from __future__ import annotations

import json

import httpx
import pytest

from dealradar.conditions import Conditions
from dealradar.llm import BudgetExceeded, FatalAPIError
from dealradar.llm_gemini import BASE, GeminiLLM, inline_schema
from dealradar.models import DealAnalysis, RawDeal
from tests.conftest import base_analysis

CFG = {"gemini": {"model": "gemini-test", "min_interval_seconds": 0}}
DEAL = RawDeal("x", "k", "Bank X: 100 € Prämie", "https://example.org/x", "100 € für Kontowechsel")


def _llm(handler) -> GeminiLLM:
    client = httpx.Client(base_url=BASE, transport=httpx.MockTransport(handler))
    return GeminiLLM(CFG, "2026-11-22", client=client)


def _ok(analysis: DealAnalysis) -> httpx.Response:
    return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"text": analysis.model_dump_json()}]}}], "usageMetadata": {"promptTokenCount": 10}})


def test_schema_ohne_refs():
    s = json.dumps(inline_schema(DealAnalysis.model_json_schema()))
    assert "$ref" not in s and "$defs" not in s


def test_analyse_und_schema_im_request():
    seen = {}

    def h(req: httpx.Request):
        seen["body"] = json.loads(req.content)
        assert req.url.path.endswith("/models/gemini-test:generateContent")
        assert "key=" not in str(req.url)          # Key nie in der URL
        return _ok(base_analysis(bedingungen_gelesen=True))

    a = _llm(h).analyze(DEAL, Conditions("nicht_gefunden", note="kein Link"))
    assert "responseJsonSchema" in seen["body"]["generationConfig"]
    assert a.bedingungen_gelesen is False           # ohne Bedingungen korrigiert
    assert a.unsicher


def test_tageslimit_beendet_lauf():
    def h(req):
        return httpx.Response(429, text='{"error": {"details": "GenerateRequestsPerDayPerProjectPerModel"}}')
    with pytest.raises(BudgetExceeded):
        _llm(h).analyze(DEAL, Conditions("nicht_gefunden"))


def test_ungueltiger_key_ist_fatal():
    with pytest.raises(FatalAPIError):
        _llm(lambda req: httpx.Response(403, text="denied")).analyze(DEAL, Conditions("nicht_gefunden"))


def test_schema_abgelehnt_rueckfall_prompt():
    calls = []

    def h(req):
        body = json.loads(req.content)
        calls.append(body)
        if "responseJsonSchema" in body["generationConfig"]:
            return httpx.Response(400, text="Invalid JSON schema field")
        return _ok(base_analysis())

    _llm(h).analyze(DEAL, Conditions("nicht_gefunden"))
    assert len(calls) == 2 and "JSON-Schema der Antwort" in calls[1]["systemInstruction"]["parts"][0]["text"]


def test_ungueltiges_json_einmal_wiederholt():
    n = []

    def h(req):
        n.append(1)
        if len(n) == 1:
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{kaputt"}]}}]})
        return _ok(base_analysis())

    _llm(h).analyze(DEAL, Conditions("nicht_gefunden"))
    assert len(n) == 2


def test_ueberlastung_weicht_auf_ausweichmodell_aus(monkeypatch):
    monkeypatch.setattr("dealradar.llm_gemini.time.sleep", lambda s: None)
    seen = []

    def h(req):
        seen.append(req.url.path)
        if "gemini-test:" in req.url.path:
            return httpx.Response(503, text="high demand")
        return _ok(base_analysis())

    client = httpx.Client(base_url=BASE, transport=httpx.MockTransport(h))
    llm = GeminiLLM({"gemini": {"model": "gemini-test", "fallback_models": ["gemini-alt"],
                                "min_interval_seconds": 0}}, "2026-11-22", client=client)
    llm.analyze(DEAL, Conditions("nicht_gefunden"))
    assert seen[-1].endswith("/gemini-alt:generateContent") and len(seen) == 3
    assert llm.model == "gemini-alt"


def test_tageslimit_eines_modells_weicht_aus():
    seen = []

    def h(req):
        seen.append(req.url.path)
        if "gemini-test:" in req.url.path:
            return httpx.Response(429, text="GenerateRequestsPerDayPerProjectPerModel")
        return _ok(base_analysis())

    client = httpx.Client(base_url=BASE, transport=httpx.MockTransport(h))
    llm = GeminiLLM({"gemini": {"model": "gemini-test", "fallback_models": ["gemini-alt"],
                                "min_interval_seconds": 0}}, "2026-11-22", client=client)
    llm.analyze(DEAL, Conditions("nicht_gefunden"))
    llm.analyze(DEAL, Conditions("nicht_gefunden"))
    assert [p.split("/")[-1] for p in seen] == ["gemini-test:generateContent", "gemini-alt:generateContent",
                                                 "gemini-alt:generateContent"]


def _search_resp(text: str, domains: list[str]) -> httpx.Response:
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]},
        "groundingMetadata": {"groundingChunks": [{"web": {"uri": "https://vertexaisearch.example/x", "title": d}}
                                                  for d in domains]}}]})


def test_suche_url_durch_treffer_belegt():
    llm = _llm(lambda req: _search_resp(
        "ANBIETER: Bank X\nDOMAIN: bank-x.de\nBEDINGUNGEN_URL: https://www.bank-x.de/aktion/tnb.pdf\nHINWEIS: PDF",
        ["bank-x.de"]))
    look = llm.find_conditions(DEAL)
    assert look.bedingungen_url == "https://www.bank-x.de/aktion/tnb.pdf"


def test_suche_erfundene_url_wird_verworfen():
    llm = _llm(lambda req: _search_resp(
        "ANBIETER: Bank X\nDOMAIN: bank-x.de\nBEDINGUNGEN_URL: https://bank-x.de/tnb\nHINWEIS: x",
        ["mydealz.de"]))
    look = llm.find_conditions(DEAL)
    assert look.bedingungen_url is None and "nicht durch Suchtreffer belegt" in look.hinweis


def test_suche_sendet_google_search_tool():
    seen = {}

    def h(req):
        seen["body"] = json.loads(req.content)
        return _search_resp("BEDINGUNGEN_URL: KEINE\nHINWEIS: nichts", [])

    _llm(h).find_conditions(DEAL)
    assert seen["body"]["tools"] == [{"google_search": {}}]


def test_quota_info_unterscheidet_tag_und_minute():
    from dealradar.llm_gemini import quota_info
    day = json.dumps({"error": {"details": [
        {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
        {"retryDelay": "78393s"}]}})
    minute = json.dumps({"error": {"details": [
        {"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
        {"retryDelay": "12s"}]}})
    assert quota_info(day)[0] == "day"
    assert quota_info(minute) == ("minute", 12.0)


def test_minutenlimit_wartet_statt_aufzugeben(monkeypatch):
    monkeypatch.setattr("dealradar.llm_gemini.time.sleep", lambda s: None)
    n = []
    minute = json.dumps({"error": {"details": [{"violations": [{"quotaId": "PerMinute"}]}, {"retryDelay": "3s"}]}})

    def h(req):
        n.append(1)
        return httpx.Response(429, text=minute) if len(n) == 1 else _ok(base_analysis())

    llm = _llm(h)
    llm.analyze(DEAL, Conditions("nicht_gefunden"))
    assert len(n) == 2 and not llm.exhausted


def test_404_weicht_aus_nur_alle_fehlend_ist_fatal(monkeypatch):
    def h(req):
        if "gemini-test:" in req.url.path:
            return httpx.Response(404, text="no longer available")
        return _ok(base_analysis())

    client = httpx.Client(base_url=BASE, transport=httpx.MockTransport(h))
    llm = GeminiLLM({"gemini": {"model": "gemini-test", "fallback_models": ["gemini-alt"],
                                "min_interval_seconds": 0}}, "2026-11-22", client=client)
    llm.analyze(DEAL, Conditions("nicht_gefunden"))
    assert llm.model == "gemini-alt"
    gone = _llm(lambda req: httpx.Response(404, text="gone"))
    with pytest.raises(FatalAPIError):
        gone.analyze(DEAL, Conditions("nicht_gefunden"))


def test_timeout_weicht_auf_naechstes_modell_aus(monkeypatch):
    import httpx
    from dealradar.llm_gemini import GeminiLLM
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if "erst" in req.url.path:
            raise httpx.ReadTimeout("timed out", request=req)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})

    c = httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler))
    llm = GeminiLLM({"gemini": {"model": "erst", "fallback_models": ["zweit"], "min_interval_seconds": 0}},
                    "2026-11-22", client=c)
    llm._post_any({"contents": []})
    assert len(calls) == 2 and llm.model == "zweit"


def test_5xx_mit_einem_versuch_weicht_aus(monkeypatch):
    import httpx
    from dealradar.llm_gemini import GeminiLLM
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if "erst" in req.url.path:
            return httpx.Response(503, json={"error": {"message": "overloaded"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})

    c = httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler))
    llm = GeminiLLM({"gemini": {"model": "erst", "fallback_models": ["zweit"], "attempts": 1,
                                "min_interval_seconds": 0}}, "2026-11-22", client=c)
    llm._post_any({"contents": []})
    assert len(calls) == 2 and llm.model == "zweit"
