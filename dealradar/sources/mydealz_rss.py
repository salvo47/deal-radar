"""mydealz.de – nur der öffentliche RSS-Feed (robots.txt sperrt KI-Crawler,
der Feed ist das vorgesehene maschinenlesbare Angebot)."""
from __future__ import annotations

import feedparser
from bs4 import BeautifulSoup

from dealradar.models import RawDeal


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
        out.append(RawDeal(source=source, source_key=guid, title=e.get("title", ""),
                           url=e.get("link", ""), text=b.get_text(" ", strip=True),
                           provider_hint=merchant, links=links,
                           published=e.get("published", "")))
    return out
