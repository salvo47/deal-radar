"""Gemeinsame Textbausteine für Mail und Vault-Notiz."""
from __future__ import annotations

from dealradar.notify import DISCLAIMER, AlertItem


def deal_markdown(it: AlertItem, heading: str = "##") -> str:
    a, c, d = it.analysis, it.calc, it.decision
    lines = [f"{heading} {'🔄 Update: ' if it.kind == 'update' else ''}{a.anbieter} – {it.title}", ""]
    lines += [f"**Nettogewinn (konservativ): {c.netto_eur:.2f} €** · Kategorie: {a.kategorie} · "
              f"Frist: {a.frist or a.frist_text or 'unbekannt'} · Risiko: **{a.risiko.rating}** · "
              f"Konfidenz: {a.konfidenz:.2f}", ""]
    lines += ["### Zusammenfassung", a.zusammenfassung, ""]
    lines += [f"**Zielgruppe:** {a.zielgruppe}"]
    if a.ausgeschlossen:
        lines += [f"**Ausgeschlossen:** {'; '.join(a.ausgeschlossen)}"]
    lines += [f"**Wiederholbarkeit:** {a.wiederholbarkeit}"
              + (f" (max. {a.max_wiederholungen})" if a.max_wiederholungen else ""), ""]
    lines += ["### Rechnung"] + [f"- {p}" for p in c.posten] + [""]
    if a.hebel:
        icon = {"erlaubt": "✅", "nicht_ausgeschlossen": "🟡", "unklar": "❔", "ausgeschlossen": "⛔"}
        lines += ["### Mehr herausholen (Hebel – nicht im Netto enthalten)"]
        for h in a.hebel:
            z = f" · +{h.zusatz_eur:.2f} €" if h.zusatz_eur else ""
            lines += [f"- {icon[h.status]} **{h.status.replace('_', ' ')}** – {h.beschreibung}{z}",
                      f"  - Beleg: „{h.beleg}“"]
        if c.zusatz_eur:
            lines += [f"- **Zusatzpotenzial, falls die Bedingungen es zulassen: +{c.zusatz_eur:.2f} €** "
                      f"(🟡 = nicht ausdrücklich erlaubt, vorher prüfen)"]
        lines += [""]
    if a.klauseln:
        lines += ["### Relevante Klauseln"]
        for k in a.klauseln:
            tag = " ⛔ nicht empfohlen" if k.empfehlung == "nicht_empfohlen" else ""
            lines += [f"> „{k.zitat}“", f"> — {k.fundstelle}", "",
                      f"{k.warum_interessant}{tag}"
                      + (f" ({k.grund_nicht_empfohlen})" if k.grund_nicht_empfohlen else ""), ""]
    r, s = a.risiko, a.seriositaet
    lines += ["### Risiko", f"**{r.rating}** – {r.begruendung}",
              f"- Rückforderung: {r.rueckforderung}", f"- Kontosperre: {r.kontosperre}",
              f"- Missbrauchsklausel: {r.missbrauchsklausel}", f"- Haltefrist: {r.haltefrist}",
              f"- Steuer: {r.steuer}", ""]
    lines += ["### Seriosität", f"{s.bewertung} · Aufsicht: {s.aufsicht} · Einlagensicherung: {s.einlagensicherung}", ""]
    if d.hinweise or a.unsicher:
        lines += ["### Unsicherheiten"] + [f"- {h}" for h in d.hinweise] + [f"- {u}" for u in a.unsicher] + [""]
    lines += ["### Links",
              f"- Originalbedingungen: {it.conditions_url or f'nicht geladen ({it.conditions_status})'}",
              f"- Fundstelle: {it.source_url} ({it.source})", "",
              f"*{DISCLAIMER}*", "",
              "### Checkliste", "- [ ] Bedingungen selbst gelesen", "- [ ] Habe ich gemacht (Datum: )",
              "- [ ] Ausgezahlt am: ", ""]
    return "\n".join(lines)
