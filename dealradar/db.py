"""SQLite-Speicher: Deals, Analysen, Meldungen, Quellen-Gesundheit, LLM-Kosten.

Es werden keine Rohtexte der Quellen gespeichert (nur Hashes und eigene Analysen),
und niemals Bank-, Konto- oder Ausweisdaten."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from dealradar.models import RawDeal

SCHEMA = """
CREATE TABLE IF NOT EXISTS deals (
  deal_id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  source_key TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  conditions_url TEXT,
  conditions_hash TEXT,
  conditions_checked_at TEXT,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  -- pending: noch nicht analysiert | analyzed | alerted | skipped | duplicate | error
  status TEXT NOT NULL,
  status_reason TEXT,
  offer_key TEXT,
  alerted_content_hash TEXT,
  alerted_conditions_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_deals_offer ON deals(offer_key);
CREATE TABLE IF NOT EXISTS analyses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  deal_id TEXT NOT NULL REFERENCES deals(deal_id),
  created_at TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  conditions_hash TEXT,
  model TEXT,
  analysis_json TEXT NOT NULL,
  calc_json TEXT NOT NULL,
  decision_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  deal_id TEXT NOT NULL,
  sent_at TEXT NOT NULL,
  kind TEXT NOT NULL,          -- neu | update
  net_gain_eur REAL,
  channels TEXT
);
CREATE TABLE IF NOT EXISTS source_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  run_at TEXT NOT NULL,
  ok INTEGER NOT NULL,
  n_items INTEGER NOT NULL,
  error TEXT
);
CREATE TABLE IF NOT EXISTS warnings_sent (
  key TEXT PRIMARY KEY,
  sent_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_at TEXT NOT NULL,
  purpose TEXT NOT NULL,
  input_tokens INTEGER, output_tokens INTEGER, web_searches INTEGER,
  cost_eur REAL
);
"""

Change = Literal["neu", "geaendert", "unveraendert"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path | str, in_memory_copy: bool = False) -> None:
        if in_memory_copy:
            # --dry-run: aktuellen Stand lesen, aber nichts auf Platte schreiben
            self.conn = sqlite3.connect(":memory:")
            if Path(path).exists():
                src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                src.backup(self.conn)
                src.close()
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------- Deals ----------
    def upsert(self, d: RawDeal, offer_key: str) -> Change:
        """Speichert einen Fund. Neu oder geändert -> wird weiterverarbeitet.
        Ein noch nicht analysierter Fund (pending/error) gilt weiterhin als neu."""
        ts = now()
        row = self.conn.execute("SELECT * FROM deals WHERE deal_id=?", (d.deal_id,)).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO deals(deal_id,source,source_key,title,url,content_hash,first_seen,"
                "last_seen,status,offer_key) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (d.deal_id, d.source, d.source_key, d.title, d.url, d.content_hash, ts, ts,
                 "pending", offer_key))
            self.conn.commit()
            return "neu"
        if row["content_hash"] != d.content_hash:
            self.conn.execute(
                "UPDATE deals SET title=?, url=?, content_hash=?, last_seen=?, status='pending', "
                "offer_key=? WHERE deal_id=?",
                (d.title, d.url, d.content_hash, ts, offer_key, d.deal_id))
            self.conn.commit()
            return "geaendert"
        self.conn.execute("UPDATE deals SET last_seen=? WHERE deal_id=?", (ts, d.deal_id))
        self.conn.commit()
        if row["status"] in ("pending", "error"):
            return "neu" if row["alerted_content_hash"] is None else "geaendert"
        return "unveraendert"

    def get(self, deal_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM deals WHERE deal_id=?", (deal_id,)).fetchone()

    def set_status(self, deal_id: str, status: str, reason: str = "") -> None:
        self.conn.execute("UPDATE deals SET status=?, status_reason=? WHERE deal_id=?",
                          (status, reason, deal_id))
        self.conn.commit()

    def set_conditions(self, deal_id: str, url: str | None, chash: str | None) -> None:
        self.conn.execute("UPDATE deals SET conditions_url=?, conditions_hash=?, "
                          "conditions_checked_at=? WHERE deal_id=?", (url, chash, now(), deal_id))
        self.conn.commit()

    def offer_alerted_elsewhere(self, offer_key: str, deal_id: str) -> sqlite3.Row | None:
        """Gleiche Aktion schon über eine andere Quelle gemeldet/analysiert?"""
        return self.conn.execute(
            "SELECT * FROM deals WHERE offer_key=? AND deal_id<>? AND status IN "
            "('alerted','analyzed','skipped') AND last_seen >= ? ORDER BY status='alerted' DESC LIMIT 1",
            (offer_key, deal_id, (datetime.now(timezone.utc) - timedelta(days=45)).isoformat())
        ).fetchone()

    def due_for_recheck(self, days: int) -> list[sqlite3.Row]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        return self.conn.execute(
            "SELECT * FROM deals WHERE status='alerted' AND conditions_url IS NOT NULL AND "
            "(conditions_checked_at IS NULL OR conditions_checked_at < ?)", (cutoff,)).fetchall()

    def was_alerted(self, deal_id: str) -> bool:
        return self.get(deal_id)["alerted_content_hash"] is not None

    # ---------- Analysen & Meldungen ----------
    def save_analysis(self, deal_id: str, content_hash: str, conditions_hash: str | None,
                      model: str, analysis: dict, calc: dict, decision: dict) -> None:
        self.conn.execute(
            "INSERT INTO analyses(deal_id,created_at,content_hash,conditions_hash,model,"
            "analysis_json,calc_json,decision_json) VALUES (?,?,?,?,?,?,?,?)",
            (deal_id, now(), content_hash, conditions_hash, model,
             json.dumps(analysis, ensure_ascii=False), json.dumps(calc, ensure_ascii=False),
             json.dumps(decision, ensure_ascii=False)))
        self.conn.commit()

    def record_alert(self, deal_id: str, kind: str, net: float, channels: list[str]) -> None:
        row = self.get(deal_id)
        self.conn.execute("INSERT INTO alerts(deal_id,sent_at,kind,net_gain_eur,channels) "
                          "VALUES (?,?,?,?,?)", (deal_id, now(), kind, net, ",".join(channels)))
        self.conn.execute("UPDATE deals SET status='alerted', alerted_content_hash=?, "
                          "alerted_conditions_hash=? WHERE deal_id=?",
                          (row["content_hash"], row["conditions_hash"], deal_id))
        self.conn.commit()

    def alerts_today(self) -> int:
        day = datetime.now(timezone.utc).date().isoformat()
        return self.conn.execute("SELECT COUNT(*) FROM alerts WHERE sent_at >= ?",
                                 (day,)).fetchone()[0]

    def recent_alerts(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT a.sent_at, a.kind, a.net_gain_eur, d.title, d.url, d.deal_id, "
            "(SELECT analysis_json FROM analyses x WHERE x.deal_id=d.deal_id ORDER BY id DESC LIMIT 1) AS aj, "
            "(SELECT decision_json FROM analyses x WHERE x.deal_id=d.deal_id ORDER BY id DESC LIMIT 1) AS dj "
            "FROM alerts a JOIN deals d USING(deal_id) ORDER BY a.id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---------- Quellen-Gesundheit ----------
    def record_source_run(self, source: str, ok: bool, n: int, error: str = "") -> None:
        self.conn.execute("INSERT INTO source_runs(source,run_at,ok,n_items,error) VALUES (?,?,?,?,?)",
                          (source, now(), int(ok), n, error[:500]))
        self.conn.commit()

    def source_failing_since(self, source: str) -> datetime | None:
        """Beginn der aktuellen Fehlerserie (Fehler oder 0 Ergebnisse), sonst None."""
        rows = self.conn.execute("SELECT run_at, ok, n_items FROM source_runs WHERE source=? "
                                 "ORDER BY id DESC LIMIT 200", (source,)).fetchall()
        start = None
        for r in rows:
            if r["ok"] and r["n_items"] > 0:
                break
            start = r["run_at"]
        return datetime.fromisoformat(start) if start else None

    def warning_sent_recently(self, key: str, hours: int = 24) -> bool:
        r = self.conn.execute("SELECT sent_at FROM warnings_sent WHERE key=?", (key,)).fetchone()
        return bool(r) and datetime.fromisoformat(r["sent_at"]) > datetime.now(timezone.utc) - timedelta(hours=hours)

    def mark_warning(self, key: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO warnings_sent(key,sent_at) VALUES (?,?)", (key, now()))
        self.conn.commit()

    def record_usage(self, purpose: str, inp: int, out: int, searches: int, cost: float) -> None:
        self.conn.execute("INSERT INTO llm_usage(run_at,purpose,input_tokens,output_tokens,"
                          "web_searches,cost_eur) VALUES (?,?,?,?,?,?)",
                          (now(), purpose, inp, out, searches, cost))
        self.conn.commit()
