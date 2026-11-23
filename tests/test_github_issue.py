from __future__ import annotations

import json

import httpx
import pytest

from dealradar.notify.github_issue import GitHubIssue

ENV = {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "me/deal-radar"}


def test_ohne_token_nicht_eingerichtet():
    with pytest.raises(RuntimeError):
        GitHubIssue({})


def test_warnung_als_issue():
    seen = {}

    def h(req: httpx.Request):
        seen["path"], seen["body"] = req.url.path, json.loads(req.content)
        return httpx.Response(201, json={})

    gh = GitHubIssue(ENV, client=httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(h)))
    gh.send_warning("Quelle x", "seit 3 Tagen down")
    assert seen["path"] == "/repos/me/deal-radar/issues"
    assert seen["body"]["labels"] == ["warnung"] and "Quelle x" in seen["body"]["title"]


def test_keine_treffer_kein_issue():
    def h(req):
        raise AssertionError("darf nicht aufgerufen werden")
    gh = GitHubIssue(ENV, client=httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(h)))
    gh.send_alerts([], "2026-11-22")


def test_tagesbericht_erwaehnt_und_schliesst():
    calls = []

    def h(req: httpx.Request):
        calls.append((req.method, req.url.path, json.loads(req.content)))
        return httpx.Response(201 if req.method == "POST" else 200, json={"number": 7})

    gh = GitHubIssue({**ENV, "DEALRADAR_GITHUB_NOTIFY": "@me"},
                     client=httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(h)))
    gh.send_summary("2026-11-24", "nichts dabei")
    (m1, p1, b1), (m2, p2, b2) = calls
    assert m1 == "POST" and b1["labels"] == ["bericht"] and "keine neuen Treffer" in b1["title"]
    assert b1["body"].endswith("cc @me")
    assert m2 == "PATCH" and p2 == "/repos/me/deal-radar/issues/7" and b2 == {"state": "closed"}


def test_ohne_notify_keine_erwaehnung():
    seen = {}

    def h(req):
        seen["body"] = json.loads(req.content)["body"]
        return httpx.Response(201, json={"number": 1})
    gh = GitHubIssue(ENV, client=httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(h)))
    gh.send_warning("x", "y")
    assert "@" not in seen["body"]
