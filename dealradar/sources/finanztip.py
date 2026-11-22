"""finanztip.de/deals – Deal-Karten: div.recommendation-box[data-track=deal]."""
from __future__ import annotations

from dealradar.models import RawDeal
from dealradar.sources._util import links_of, soup, text_of


def parse(html: str, page_url: str, source: str) -> list[RawDeal]:
    s = soup(html)
    out: list[RawDeal] = []
    for box in s.select('div.recommendation-box[data-track="deal"]'):
        h2 = box.find("h2", class_="deal-title")
        if not h2 or not box.get("id"):
            continue
        title = text_of(h2)
        cat = box.find(class_="deal-category")
        status = box.find(class_="deal-type")
        text = text_of(box)
        if status and "abgelaufen" in text_of(status).lower():
            continue
        out.append(RawDeal(
            source=source, source_key=box["id"], title=title,
            url=f"https://www.finanztip.de/deals/#{box['id']}",
            text=(f"[Kategorie: {text_of(cat)}] " if cat else "") + text,
            provider_hint=title.split(":", 1)[0].strip() if ":" in title else "",
            links=links_of(box, page_url), published=box.get("data-start", "")))
    return out
