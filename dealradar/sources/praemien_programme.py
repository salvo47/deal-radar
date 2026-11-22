"""praemien-programme.de – Bonus-Tabellen (table#bonusangebote) auf den Detailseiten.
Zeilen mit einer Zelle sind Abschnittsüberschriften, Zeilen mit zwei Zellen Angebote."""
from __future__ import annotations

import re

from dealradar.models import RawDeal
from dealradar.sources._util import links_of, soup, text_of

SKIP = re.compile(r"(?i)auf der website nachsehen|derzeit kein|keine prämie")


def parse(html: str, page_url: str, source: str) -> list[RawDeal]:
    s = soup(html)
    table = s.find("table", id="bonusangebote")
    if not table:
        return []
    section = ""
    out: list[RawDeal] = []
    page = page_url.rsplit("/", 1)[-1]
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        if len(cells) == 1:
            t = text_of(cells[0])
            if t:
                section = t
            continue
        if len(cells) < 2:
            continue
        name, offer = text_of(cells[0]), text_of(cells[1])
        if not name or not offer or SKIP.search(offer):
            continue
        out.append(RawDeal(
            source=source, source_key=f"{page}|{section}|{name}".lower(),
            title=f"{name}: {offer[:110]}", url=page_url,
            text=f"Rubrik: {section}. Anbieter: {name}. Angebot: {offer}",
            provider_hint=name, links=links_of(tr, page_url)))
    return out
