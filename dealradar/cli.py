"""Kommandozeile:
  python -m dealradar run [--dry-run] [--source NAME] [--limit N] [-v]
  python -m dealradar report [--limit N]
  python -m dealradar sources
"""
from __future__ import annotations

import argparse
import json
import sys

from dealradar.config import load_config
from dealradar.db import DB
from dealradar.pipeline import run, setup_logging


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dealradar", description="Deal Radar – Aktionen finden und bewerten")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="voller Lauf")
    r.add_argument("--dry-run", action="store_true", help="nichts senden/schreiben, nur Ausgabe")
    r.add_argument("--source", help="nur diese Quelle (Name aus sources.yaml)")
    r.add_argument("--limit", type=int, help="höchstens N Deals per LLM analysieren")
    r.add_argument("-v", "--verbose", action="store_true")
    rep = sub.add_parser("report", help="letzte Treffer anzeigen")
    rep.add_argument("--limit", type=int, default=20)
    sub.add_parser("sources", help="konfigurierte Quellen und ihren Zustand zeigen")
    args = p.parse_args(argv)

    cfg = load_config()
    setup_logging(cfg, getattr(args, "verbose", False))

    if args.cmd == "run":
        if args.source and args.source not in cfg.sources:
            print(f"Unbekannte Quelle '{args.source}'. Verfügbar: {', '.join(cfg.sources)}", file=sys.stderr)
            return 2
        try:
            s = run(cfg, dry_run=args.dry_run, only_source=args.source, limit=args.limit)
        except RuntimeError as e:          # z. B. fehlender API-Key oder SMTP-Daten
            print(f"Abbruch: {e}", file=sys.stderr)
            return 2
        mode = "DRY-RUN – nichts gesendet/geschrieben" if args.dry_run else "Lauf"
        print(f"{mode}: {s.fetched} Einträge geholt, {s.new} neu, {s.changed} geändert, "
              f"{s.duplicates} Duplikate, {s.prefiltered} vorgefiltert, {s.analyzed} analysiert, "
              f"{len(s.alerts)} gemeldet. LLM-Kosten ca. {s.llm_cost_eur:.2f} €"
              + (" (Budget erreicht)" if s.budget_hit else ""))
        if s.below and args.verbose:
            print("\nUnter Schwelle / nicht gemeldet:")
            for t, g in s.below:
                print(f"  - {t[:70]} → {g}")
        if s.errors:
            print("\nFehler:")
            for e in s.errors:
                print(f"  - {e}")
        return 3 if s.fatal else 0     # Key/Modell kaputt -> Lauf als fehlgeschlagen sichtbar

    db = DB(cfg.path("db"))
    if args.cmd == "report":
        rows = db.recent_alerts(args.limit)
        if not rows:
            print("Noch keine Meldungen.")
        for x in rows:
            a = json.loads(x["aj"]) if x["aj"] else {}
            print(f"{x['sent_at'][:16]}  [{x['kind']}]  {x['net_gain_eur']:.0f} €  "
                  f"{a.get('anbieter', '?')}  Frist {a.get('frist') or a.get('frist_text', '?')}  "
                  f"Risiko {a.get('risiko', {}).get('rating', '?')}\n    {x['title']}\n    {x['url']}")
        return 0
    if args.cmd == "sources":
        for name, scfg in cfg.sources.items():
            last = db.conn.execute("SELECT run_at, ok, n_items, error FROM source_runs WHERE source=? "
                                   "ORDER BY id DESC LIMIT 1", (name,)).fetchone()
            st = (f"zuletzt {last['run_at'][:16]}: {'ok' if last['ok'] else 'FEHLER'}, {last['n_items']} Einträge"
                  if last else "noch nie gelaufen")
            print(f"{name:20} {'aktiv' if scfg.get('enabled', True) else 'aus  '}  {st}")
        return 0
    return 1
