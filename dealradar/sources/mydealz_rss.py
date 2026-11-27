"""mydealz.de – nur der öffentliche RSS-Feed (robots.txt sperrt KI-Crawler,
der Feed ist das vorgesehene maschinenlesbare Angebot)."""
from __future__ import annotations

import re

import feedparser
from bs4 import BeautifulSoup

from dealradar.models import RawDeal

# Der Trending-Feed (Hot/Top-Deals) besteht fast nur aus Produktrabatten. Daraus nur Einträge mit
# Geld-zurück-Anteil im Titel übernehmen: Cashback/Portal, Geld zurück, Guthaben, Prämie/Bonus, Preisfehler, Punkte,
# oder stark reduziert (mind. 50 % Rabatt im Titel).
_MONEY_BACK = re.compile(
    r"\[cb\b|\bcb\b|cashback|shoop|topcashback|igraal|geld[- ]zur(?:ü|ue)ck|startguthaben|guthaben|"
    r"prämie|praemie|bonus|preisfehler|payback|punkte", re.I)


_PCT = re.compile(r"(?:-|minus\s*|bis zu\s*)?(\d{2})\s?%")


def _wanted(page_url: str, title: str, text: str) -> bool:
    if "/rss/trending" not in page_url and "/rss/hot" not in page_url:
        return True
    if _MONEY_BACK.search(title):
        return True
    # stark reduziert: mind. 50 % Rabatt im Titel
    return any(int(m.group(1)) >= 50 for m in _PCT.finditer(title))


def parse(xml: str, page_url: str, source: str) -> list[RawDeal]:
    feed = feedparser.parse(xml)
    out: list[RawDeal] = []
    for e in feed.entries:
        html = e.get("summary", "") or e.get("description", "")
        b = BeautifulSoup(html, "lxml")
        links = [a["href"] for a in b.find_all("a", href=True)]
        merchant = ""
        for k in ("pepper_merchant", "merchant"):
            if isinstance(e.get(k), dict):
                merchant = e[k].get("name", "")
        guid = e.get("id") or e.get("link")
        if not _wanted(page_url, e.get("title", ""), b.get_text(" ", strip=True)):
            continue
        out.append(RawDeal(source=source, source_key=guid, title=e.get("title", ""),
                           url=e.get("link", ""), text=b.get_text(" ", strip=True),
                           provider_hint=merchant, links=links,
                           published=e.get("published", "")))
    return out
