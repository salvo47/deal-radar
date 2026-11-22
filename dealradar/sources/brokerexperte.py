"""brokerexperte.de – Tabelle "Neukundenprämien" (Anker #depotwechselpraemien)."""
from __future__ import annotations

from dealradar.models import RawDeal
from dealradar.sources._util import links_of, soup, text_of


def parse(html: str, page_url: str, source: str) -> list[RawDeal]:
    s = soup(html)
    head = s.find(id="depotwechselpraemien")
    if not head:
        return []
    table = head.find_next("table")
    if not table:
        return []
    heading = text_of(head)
    out: list[RawDeal] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        name_el = tds[0].find("a") or tds[0].find("strong") or tds[0]
        broker = text_of(name_el)
        if not broker:
            continue
        cond, praemie = text_of(tds[1]), text_of(tds[2]).replace("zur Prämie", "").strip()
        out.append(RawDeal(
            source=source, source_key=broker.lower(), title=f"{broker}: {praemie}",
            url=page_url.split("#")[0] + "#depotwechselpraemien",
            text=f"{heading}. Broker: {text_of(tds[0])}. Bedingung: {cond}. Prämie: {praemie}",
            provider_hint=broker, links=links_of(tr, page_url)))
    return out
