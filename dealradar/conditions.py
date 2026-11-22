"""Lädt die Original-Teilnahmebedingungen des Anbieters (HTML oder PDF).

Reihenfolge: (1) direkte Anbieter-Links aus dem Fund, (2) optional Websuche über das
LLM (nur Fundort-Suche, die Seite selbst laden wir mit robots.txt-Prüfung).
Kann nichts geladen werden, wird das gemeldet – es wird nicht geraten."""
from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from dealradar.clauses import focus
from dealradar.http import Fetcher, RobotsDisallowed
from dealradar.models import RawDeal

if TYPE_CHECKING:
    from dealradar.llm import LLM

log = logging.getLogger(__name__)

AGGREGATORS = ("modern-banking.de", "mydealz.de", "finanztip.de", "brokerexperte.de",
               "depot-vergleichen.de", "praemien-programme.de", "chip.de", "pepper.com",
               "google.", "bing.", "facebook.", "youtube.", "reddit.")
COND_HINT = re.compile(r"(?i)(teilnahme|bedingung|aktionsbeding|praemienbeding|prämienbeding|agb|"
                       r"terms|konditionen|neukunden|aktion|bonus|praemie|prämie|\.pdf)")

Status = Literal["ok", "nicht_gefunden", "nicht_ladbar", "robots", "zu_lang"]


@dataclass
class Conditions:
    status: Status
    url: str | None = None
    text: str = ""          # was das LLM liest (ggf. auf relevante Klauseln gekürzt)
    note: str = ""
    full: str = ""          # vollständiger Text (Klausel-Scanner, Änderungserkennung)

    @property
    def hash(self) -> str | None:
        if self.status != "ok":
            return None
        body = re.sub(r"\s+", " ", self.full or self.text).strip().lower()
        return hashlib.sha256(body.encode()).hexdigest()


def is_aggregator(url: str) -> bool:
    host = urlsplit(url).netloc.lower()
    return any(a in host for a in AGGREGATORS)


def candidate_urls(d: RawDeal) -> list[str]:
    c = [u for u in d.links if u.startswith("https://") and not is_aggregator(u)]
    c.sort(key=lambda u: (not u.lower().endswith(".pdf"), not COND_HINT.search(u)))
    return c[:3]


def extract_text(content: bytes, content_type: str, url: str) -> str:
    if "pdf" in content_type.lower() or url.lower().endswith(".pdf") or content[:5] == b"%PDF-":
        reader = PdfReader(io.BytesIO(content))
        pages = []
        for i, p in enumerate(reader.pages, 1):
            pages.append(f"[Seite {i}]\n{p.extract_text() or ''}")
        return "\n".join(pages)
    s = BeautifulSoup(content, "lxml")
    for t in s(["script", "style", "noscript", "nav", "header", "footer", "form", "svg"]):
        t.decompose()
    main = s.find("main") or s.find("article") or s.body or s
    return re.sub(r"\n\s*\n+", "\n\n", main.get_text("\n", strip=True))


def fetch_url(fetcher: Fetcher, url: str, max_chars: int) -> Conditions:
    try:
        r = fetcher.get(url, cache=True)
    except RobotsDisallowed:
        return Conditions("robots", url, note="robots.txt des Anbieters erlaubt den Abruf nicht")
    except (httpx.HTTPError, OSError) as e:
        return Conditions("nicht_ladbar", url, note=f"Abruf fehlgeschlagen: {e}")
    try:
        text = extract_text(r.content, r.content_type, r.url)
    except Exception as e:  # defekte PDFs u. Ä.
        return Conditions("nicht_ladbar", r.url, note=f"Text nicht extrahierbar: {e}")
    if len(text.strip()) < 300:
        return Conditions("nicht_ladbar", r.url, note="kaum Text (evtl. per JavaScript geladen)")
    if len(text) > max_chars * 20:      # z. B. komplette AGB-Sammlung – keine Aktionsbedingungen
        return Conditions("zu_lang", r.url, note=f"{len(text)} Zeichen – vermutlich keine Aktionsbedingungen")
    short, cut = focus(text, max_chars)
    note = f"auf relevante Klauseln gekürzt ({len(short)} von {len(text)} Zeichen)" if cut else ""
    return Conditions("ok", r.url, text=short, note=note, full=text)


def load_conditions(fetcher: Fetcher, d: RawDeal, llm: "LLM | None", max_chars: int,
                    use_search: bool, known_url: str | None = None) -> Conditions:
    tried: list[Conditions] = []
    urls = ([known_url] if known_url else []) + [u for u in candidate_urls(d) if u != known_url]
    for u in urls:
        c = fetch_url(fetcher, u, max_chars)
        if c.status == "ok":
            return c
        tried.append(c)
    if use_search and llm is not None and not known_url:
        look = llm.find_conditions(d)
        if look and look.bedingungen_url and look.bedingungen_url.startswith("https://") \
                and not is_aggregator(look.bedingungen_url):
            c = fetch_url(fetcher, look.bedingungen_url, max_chars)
            if c.status == "ok":
                return c
            tried.append(c)
        elif look:
            tried.append(Conditions("nicht_gefunden", note=f"Websuche: {look.hinweis}"))
    if tried:
        last = tried[-1]
        last.note = "; ".join(f"{t.url or '-'}: {t.note}" for t in tried)
        return last
    return Conditions("nicht_gefunden", note="kein Link zu Originalbedingungen im Fund")
