"""Höflicher HTTP-Client: robots.txt (mit Wildcards), Rate-Limit pro Host, ehrlicher
User-Agent, optionaler Platten-Cache."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)
UA_TOKEN = "dealradar"


class RobotsDisallowed(Exception):
    pass


@dataclass
class Response:
    url: str
    status: int
    content_type: str
    content: bytes

    @property
    def text(self) -> str:
        enc = "utf-8"
        m = re.search(r"charset=([\w-]+)", self.content_type or "")
        if m:
            enc = m.group(1)
        return self.content.decode(enc, errors="replace")


# ---------- robots.txt ----------

def parse_robots(txt: str, ua_token: str = UA_TOKEN) -> list[tuple[str, str]]:
    """Gibt die Regeln (allow/disallow, Muster) der passenden Gruppe zurück.
    Eine spezifische Gruppe für unseren UA gewinnt, sonst gilt '*'."""
    groups: list[tuple[list[str], list[tuple[str, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[str, str]] = []
    last_was_agent = False
    for line in txt.splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, val = (p.strip() for p in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if not last_was_agent and agents:
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(val.lower())
            last_was_agent = True
        elif key in ("allow", "disallow"):
            rules.append((key, val))
            last_was_agent = False
        else:
            last_was_agent = False
    if agents:
        groups.append((agents, rules))
    specific = [r for a, r in groups if any(ua_token in x for x in a if x != "*")]
    if specific:
        return [x for r in specific for x in r]
    return [x for a, r in groups if "*" in a for x in r]


def _pattern_to_regex(p: str) -> re.Pattern[str]:
    anchored = p.endswith("$")
    if anchored:
        p = p[:-1]
    rx = "".join(".*" if c == "*" else re.escape(c) for c in p)
    return re.compile("^" + rx + ("$" if anchored else ""))


def robots_allowed(rules: list[tuple[str, str]], path: str) -> bool:
    """Längste passende Regel gewinnt; bei Gleichstand gewinnt allow (RFC 9309)."""
    best_len, verdict = -1, True
    for kind, pat in rules:
        if kind == "disallow" and pat == "":
            continue
        if _pattern_to_regex(pat).match(path):
            ln = len(pat)
            if ln > best_len or (ln == best_len and kind == "allow"):
                best_len, verdict = ln, kind == "allow"
    return verdict


# ---------- Client ----------

class Fetcher:
    def __init__(self, user_agent: str, min_delay: float = 2.0, timeout: float = 30.0,
                 cache_dir: Path | None = None, cache_ttl_hours: float = 12.0,
                 client: httpx.Client | None = None) -> None:
        self.min_delay = max(2.0, min_delay)   # harte Untergrenze laut Vorgabe
        self.cache_dir = cache_dir
        self.cache_ttl = cache_ttl_hours * 3600
        self._last: dict[str, float] = {}
        self._robots: dict[str, list[tuple[str, str]]] = {}
        self.client = client or httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "de-DE,de;q=0.9"},
            timeout=timeout, follow_redirects=True)

    def _wait(self, host: str, delay: float) -> None:
        last = self._last.get(host)
        if last is not None:
            remaining = delay - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining)
        self._last[host] = time.monotonic()

    def _rules(self, scheme: str, host: str) -> list[tuple[str, str]]:
        if host not in self._robots:
            self._wait(host, self.min_delay)
            try:
                r = self.client.get(f"{scheme}://{host}/robots.txt")
                if r.status_code >= 500:
                    # Server-Fehler: vorsichtshalber alles sperren (RFC 9309)
                    self._robots[host] = [("disallow", "/")]
                elif r.status_code >= 400:
                    self._robots[host] = []
                else:
                    self._robots[host] = parse_robots(r.text)
            except httpx.HTTPError as e:
                log.warning("robots.txt für %s nicht ladbar (%s) – Host wird übersprungen", host, e)
                self._robots[host] = [("disallow", "/")]
        return self._robots[host]

    def allowed(self, url: str) -> bool:
        u = urlsplit(url)
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        return robots_allowed(self._rules(u.scheme, u.netloc), path)

    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")

    def get(self, url: str, *, cache: bool = True, delay: float | None = None) -> Response:
        cp = self._cache_path(url) if cache else None
        if cp and cp.exists() and time.time() - cp.stat().st_mtime < self.cache_ttl:
            d = json.loads(cp.read_text())
            return Response(d["url"], d["status"], d["content_type"], bytes.fromhex(d["content"]))
        if not self.allowed(url):
            raise RobotsDisallowed(url)
        host = urlsplit(url).netloc
        self._wait(host, max(self.min_delay, delay or 0))
        r = self.client.get(url)
        resp = Response(str(r.url), r.status_code, r.headers.get("content-type", ""), r.content)
        if r.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {r.status_code} für {url}", request=r.request, response=r)
        if cp:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps({"url": resp.url, "status": resp.status,
                                      "content_type": resp.content_type,
                                      "content": resp.content.hex()}))
        return resp
