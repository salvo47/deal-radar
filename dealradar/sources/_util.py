from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag


def soup(html: str) -> BeautifulSoup:
    s = BeautifulSoup(html, "lxml")
    for t in s(["script", "style", "noscript"]):
        t.decompose()
    return s


def text_of(el: Tag) -> str:
    return el.get_text(" ", strip=True)


SOCIAL = ("facebook.com", "twitter.com", "x.com/", "whatsapp.com", "linkedin.com", "pinterest.")


def links_of(el: Tag, base: str) -> list[str]:
    out = []
    for a in el.find_all("a", href=True):
        h = a["href"]
        if h.startswith(("#", "mailto:", "javascript:")):
            continue
        u = urljoin(base, h)
        if any(s in u for s in SOCIAL) or u in out:
            continue
        out.append(u)
    return out
