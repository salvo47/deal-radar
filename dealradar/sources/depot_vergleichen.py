"""depot-vergleichen.de/neukundenaktionen – Vergleichstabelle table.hometable,
Spalte column-5 enthält div.promotion mit dem Neukundenangebot."""
from __future__ import annotations

import re

from dealradar.models import RawDeal
from dealradar.sources._util import links_of, soup, text_of


def _clean(name: str) -> str:
    name = re.sub(r"(?i)\b(erfahrungsbericht|erfahrungen|logo)\b", "", name)
    name = re.sub(r"(?i)\s+depot\s*$", "", name.strip())
    return re.sub(r"\s+", " ", name).strip()


def _broker_name(tr) -> str:
    c1 = tr.find("td", class_="column-1")
    if not c1:
        return ""
    img = c1.find("img", alt=True)
    if img and img["alt"].strip():
        return _clean(img["alt"])
    for el in c1.find_all(["strong", "a", "span"]):
        t = text_of(el)
        if t and "Erfahrung" not in t:
            return _clean(t)
    return ""


def parse(html: str, page_url: str, source: str) -> list[RawDeal]:
    s = soup(html)
    out: list[RawDeal] = []
    seen: set[str] = set()
    for tr in s.select("table.hometable tr.content-row"):
        promo = tr.select_one("td.column-5 div.promotion")
        if not promo:
            continue
        ptxt = text_of(promo)
        if not ptxt or "keine Neukundenpr" in ptxt:
            continue
        broker = _broker_name(tr) or "unbekannt"
        key = broker.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(RawDeal(
            source=source, source_key=key, title=f"{broker}: {ptxt[:120]}",
            url=page_url, text=f"Broker: {broker}. Neukundenangebot: {ptxt}",
            provider_hint=broker, links=links_of(promo, page_url)))
    return out
