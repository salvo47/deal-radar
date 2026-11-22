"""Quellen-Registry. Jede Quelle ist ein Modul mit
    parse(content: str, page_url: str, source: str) -> list[RawDeal]
und optional fetch(fetcher, source_cfg, source) -> list[RawDeal].
Fehlt fetch, werden alle `pages`/`feeds` geladen und an parse übergeben."""
from __future__ import annotations

import importlib
import logging
from urllib.parse import urlsplit
from types import ModuleType
from typing import Any

from dealradar.http import Fetcher
from dealradar.models import RawDeal

log = logging.getLogger(__name__)


def load_module(name: str) -> ModuleType:
    return importlib.import_module(f"dealradar.sources.{name}")


def fetch_source(fetcher: Fetcher, source: str, scfg: dict[str, Any]) -> list[RawDeal]:
    mod = load_module(scfg["module"])
    if hasattr(mod, "fetch"):
        return mod.fetch(fetcher, scfg, source)
    deals: list[RawDeal] = []
    urls = scfg.get("pages", []) + scfg.get("feeds", [])
    failed: list[str] = []
    for url in urls:
        try:
            resp = fetcher.get(url, cache=scfg.get("cache", True), delay=scfg.get("min_delay_seconds"))
            if urlsplit(resp.url).path.rstrip("/") != urlsplit(url).path.rstrip("/"):
                raise RuntimeError(f"{url} leitet auf {resp.url} um – Seite verschoben? URL in sources.yaml prüfen")
        except Exception as e:
            # eine kaputte Unterseite darf die anderen Seiten derselben Quelle nicht mitreißen
            if len(urls) == 1:
                raise
            log.warning("%s: %s übersprungen: %s", source, url, e)
            failed.append(f"{url}: {e}")
            continue
        found = mod.parse(resp.text, resp.url, source)
        log.info("%s: %d Einträge von %s", source, len(found), url)
        deals.extend(found)
    if failed and len(failed) == len(urls):
        raise RuntimeError("alle Seiten fehlgeschlagen: " + " | ".join(failed))
    # doppelte Schlüssel (gleiche Aktion auf mehreren Unterseiten) zusammenführen
    uniq: dict[str, RawDeal] = {}
    for d in deals:
        uniq.setdefault(d.source_key, d)
    return list(uniq.values())
