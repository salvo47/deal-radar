"""modern-banking.de – Übersichtsseiten der Neukundenaktionen.
Struktur: <hr id="anker"><article><h2>Anbieter - Titel</h2><p>…</p></article>"""
from __future__ import annotations

from dealradar.models import RawDeal
from dealradar.sources._util import links_of, soup, text_of


def parse(html: str, page_url: str, source: str) -> list[RawDeal]:
    s = soup(html)
    out: list[RawDeal] = []
    for art in s.find_all("article"):
        h2 = art.find("h2")
        if not h2:
            continue
        title = text_of(h2)
        hr = art.find_previous_sibling("hr")
        anchor = hr.get("id") if hr and hr.get("id") else title
        provider = title.split(" - ", 1)[0].strip() if " - " in title else ""
        page = page_url.rsplit("/", 1)[-1].split("#")[0]
        out.append(RawDeal(
            source=source, source_key=f"{page}#{anchor}|{provider}", title=title,
            url=f"{page_url.split('#')[0]}#{anchor}", text=text_of(art),
            provider_hint=provider, links=links_of(art, page_url)))
    return out
