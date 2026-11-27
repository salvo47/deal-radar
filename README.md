# Deal Radar

Findet täglich Geld- und Spar-Aktionen in Deutschland/EU – Konto-, Depot-, Order-Prämien,
Neukundenzinsen, Preisfehler, **Cashback und Geld-zurück-Aktionen, Wechselboni (Strom, Gas, DSL)
und Gutscheine/Punkte, die sich in Geld umwandeln lassen** –, liest die Teilnahmebedingungen,
rechnet den realistischen Nettogewinn **in Python** aus, zeigt legitime **Hebel**, mit denen sich
mehr herausholen lässt, und meldet nur, was deinen Schwellenwert übersteigt.

> Jeder Fund ist ein Hinweis, keine Beratung. Lies die Originalbedingungen vor dem Handeln
> selbst gegen. Eingesetztes Geld gilt als Risikokapital. Der Agent eröffnet keine Konten,
> führt keine Orders oder Überweisungen aus und speichert keine Bank-, Konto- oder Ausweisdaten.

## Ablauf

```
fetch (Quellen-Module) → normalisieren (RawDeal) → SQLite (Hash, first/last_seen, Status)
  → nur neue/geänderte → Duplikate über Quellen hinweg zusammenfassen → Vorfilter
  → Originalbedingungen laden (HTML/PDF) → LLM extrahiert Zahlen + Klauseln (JSON-Schema)
  → calc.py rechnet → scoring.py entscheidet (harte Grenzen) → E-Mail / ntfy / Vault-Notiz
```

## Dateistruktur

```
config.yaml              Schwellenwert, Risiko, Budget, Rechenannahmen, Kanäle
sources.yaml             Quellen (je Quelle ein Modul)
.env                     Secrets (nicht im Git)
dealradar/
  cli.py                 python -m dealradar run|report|sources
  pipeline.py            Ablaufsteuerung, Dedupe, Tageslimit, Quellen-Warnungen
  http.py                robots.txt (inkl. Wildcards), ≥2 s Pause pro Host, Cache, User-Agent
  sources/*.py           modern_banking, mydealz_rss, finanztip, brokerexperte,
                         depot_vergleichen, praemien_programme
  conditions.py          Teilnahmebedingungen finden und als Text laden (pypdf für PDFs)
  llm.py                 Anthropic API, strukturierte Ausgabe, Budgetgrenze
  models.py              RawDeal + Pydantic-Schema der Analyse
  calc.py                Gewinnrechnung (mit Unit-Tests)
  scoring.py             Melde-Entscheidung, harte Grenzen
  db.py                  SQLite
  notify/                console, email, ntfy, vault
tests/                   Parser (Fixtures), Rechnung, Dedupe, Entscheidung, robots, Ende-zu-Ende
launchd/, scripts/       täglicher Lauf auf dem Mac
```

## Setup

```bash
cd ~/Projects/deal-radar
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
cp .env.example .env    # dann ausfüllen, siehe unten
.venv/bin/python -m pytest -q
.venv/bin/python -m dealradar run --dry-run -v
```

**LLM:** Standard ist lokal und kostenlos über [Ollama](https://ollama.com) mit `gemma3:12b`
(`ollama pull gemma3:12b`, ca. 8 GB, läuft auf 16 GB RAM). Die Ollama-App muss laufen.
Umschalten auf Claude: `llm.provider: anthropic` in `config.yaml` + `ANTHROPIC_API_KEY` in `.env`.

Das Projekt liegt bewusst **nicht** im iCloud-Vault (venv und SQLite vertragen kein Sync).
In den Vault wird nur die Tagesnotiz geschrieben.

### .env-Variablen

| Variable | Pflicht | Zweck |
|---|---|---|
| `GEMINI_API_KEY` | für den Cloud-Lauf | Gemini-Analyse, kostenloser Key unter aistudio.google.com/apikey |
| `ANTHROPIC_API_KEY` | nur bei `llm.provider: anthropic` | Claude-Analyse (Guthaben unter console.anthropic.com nötig) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_TO` | für E-Mail | Versand (Port 587 = STARTTLS, 465 = SSL). Bei Gmail ein App-Passwort verwenden. |
| `MAIL_FROM` | nein | Absender, Standard = `SMTP_USER` |
| `NTFY_TOPIC` | für ntfy | Topic auf ntfy.sh – wie ein Passwort behandeln (wer es kennt, liest mit) |
| `NTFY_SERVER` | nein | eigener ntfy-Server, Standard `https://ntfy.sh` |

## Bedienung

```bash
python -m dealradar run                 # voller Lauf (sendet, schreibt Vault-Notiz)
python -m dealradar run --dry-run -v    # nichts senden/schreiben, nur Ausgabe (+ Gründe für Nicht-Meldungen)
python -m dealradar run --source mydealz
python -m dealradar run --limit 5       # höchstens 5 LLM-Analysen
python -m dealradar report              # letzte Meldungen
python -m dealradar sources             # Zustand der Quellen
```

`--dry-run` arbeitet auf einer Kopie der Datenbank im Arbeitsspeicher: nichts wird gesendet,
keine Vault-Datei, keine DB-Änderung. Die LLM-Aufrufe finden trotzdem statt (kosten Geld),
und der HTTP-Cache unter `data/cache` sowie das Log werden geschrieben.

## Schwellenwert und weitere Einstellungen (`config.yaml`)

- `min_net_gain_eur: 30` – Schwellenwert in Euro (Nettogewinn nach Rechnung).
- `wohnsitz: DE` – Aktionen mit Wohnsitzpflicht in anderen Ländern (z. B. nur Österreich) werden aussortiert.
- `calc.verfuegbares_kapital_eur: 10000` – **wichtig:** so viel würdest du maximal anlegen. Zinsvorteile werden darauf begrenzt, und Aktionen, die mehr Kapital je Runde verlangen, fallen raus.
- `risk_max: mittel` – höhere Risiken nur, wenn Netto > `high_risk_multiplier` × Schwelle (Standard 5), dann mit deutlichem Warnhinweis.
- `categories` – einzelne Kategorien an/aus.
- `max_alerts_per_day: 5` – überzählige Treffer werden zurückgestellt und am nächsten Tag gemeldet (nach Netto sortiert).
- `llm.model`, `llm.effort`, `llm.budget_per_run_eur`, `llm.max_deals_per_run` – Modell und Kostenbremse. Wird das Budget erreicht, bricht der LLM-Teil ab; nicht analysierte Funde bleiben offen und kommen im nächsten Lauf dran (größte Beträge zuerst).
- `calc` – Rechenannahmen (siehe unten).
- `notify.channels` – `[email, vault]` (Standard), `ntfy` zusätzlich möglich. `notify.vault_dir` – Zielordner der Notizen.

## Gewinnrechnung (konservativ)

Das LLM **extrahiert** nur Zahlen (Prämie je Ausführung, Limit, Mindestvolumen, Gebühren,
Zinssatz, Laufzeit, Kapitalbindung, Zeitaufwand). `calc.py` rechnet:

- Gutscheine × 0,9, Sachprämien × 0,6; Order-Prämien mit 15 % Sicherheitsabschlag.
- Kein Limit genannt → bei Order-Aktionen höchstens 10 Wiederholungen, sonst (Konto-, Wechsel-, Cashback-Prämien) einmal je Person/Haushalt.
- Spread: Standard 0,30 % pro Kauf+Verkauf, wenn nicht belegt; zählen Kauf und Verkauf jeweils als Order, verteilt sich der Spread auf zwei Ausführungen. Wegen des EU-Verbots der Orderflow-Vergütung (seit Juli 2026) bewusst nicht niedriger.
- Zinsaktionen: nur der Vorteil über einem Referenzzins (2,0 %), nach Abgeltungsteuer (26,375 %, ohne Sparerpauschbetrag), auf höchstens `verfuegbares_kapital_eur` (nicht auf die Obergrenze der Aktion). Ist keine Obergrenze genannt, wird das verfügbare Kapital angesetzt.
- Wiederholungen werden zusätzlich durch das Kapital begrenzt (z. B. je 25.000 € Aufstockung bei 10.000 € verfügbar → 0). Ist die Prämie selbst ein Zinsertrag, wird sie nicht doppelt gezählt.
- Zeitaufwand × 20 €/h, entgangene Zinsen auf gebundenes Kapital.
- Prozentprämien (z. B. 0,75 % des Übertrags, max. 5.000 €): Prozentsatz × verfügbares Kapital, höchstens der Maximalbetrag.
- Kaufpflicht (Geld-zurück, gratis testen, Cashback auf Kauf, Preisfehler): Kaufpreis = Kosten,
  Erstattung = Prämie, das Produkt zählt nur mit 30 % des Kaufpreises (`produkt_restwert_faktor`) bzw.
  mit 80 % eines belegten Wiederverkaufswerts.
- Wechselboni: Bonus minus Mehrkosten des Tarifs, sofern die Quelle sie beziffert; sonst steht der
  Tarifvergleich unter „Unsicherheiten“ (deinen aktuellen Vertrag kennt Deal Radar nicht).

Jede Meldung enthält die Rechenschritte.

### Hebel: mehr herausholen
Zu jedem Fund sucht das Modell legitime Wege, mit **derselben** Aktion mehr zu verdienen:
zusätzlich über ein Cashback-Portal, Prämie als Geworbener, Kombination mit einer anderen Aktion,
erlaubte Wiederholung, Coupons/Punkte, Zahlungsart, Zeitpunkt. Jeder Hebel trägt seinen Status laut
Bedingungen – ✅ erlaubt, 🟡 nicht ausgeschlossen, ❔ unklar, ⛔ ausgeschlossen – mit wörtlichem Beleg.
Hebel fließen **nicht** in den Nettogewinn ein, sondern erscheinen als „Zusatzpotenzial“.
Hebel, die falsche Angaben, fremde Daten, Zweitkonten oder Wegwerf-Adressen voraussetzen, werden
im Code verworfen, egal was das Modell vorschlägt.

### Klausel-Scanner: Lücken und Grauzonen (ohne LLM)
`dealradar/clauses.py` liest den **vollständigen** Bedingungstext deterministisch und ergänzt die Hebel
mit wörtlichem Beleg – auch wenn das Modell nichts findet oder gerade kein Modell verfügbar ist:

| Prüfung | Ergebnis |
|---|---|
| Kombinationsverbot? | ⛔ „nicht kombinierbar“ / ✅ „kombinierbar“ / 🟡 kein Verbot gefunden |
| Cashback-Portale/Vermittler ausgeschlossen? | ⛔ mit Zitat / 🟡 nicht erwähnt → Portal zusätzlich prüfen |
| Limit „einmal pro Person“ ohne Haushaltsklausel | 🟡 weitere volljährige Personen im Haushalt mit **eigenem** Konto und **eigenen, echten** Daten |
| Neukunden-Sperrfrist („in den letzten 12 Monaten kein Kunde“) | ✅ nach Ablauf erneut teilnahmeberechtigt |
| Empfehlungsprogramm erwähnt | ❔ prüfen, ob Geworbene zusätzlich etwas bekommen (nur echte Empfehlung) |
| Rückforderung / Mindesthaltedauer | Hinweis mit Zitat |

Ein Ausschluss im Text schlägt eine gegenteilige Behauptung des Modells. „Grauzone“ heißt hier nur:
die Bedingungen schließen es nicht aus. Was falsche Angaben, fremde Identitäten oder Mehrfachkonten
erfordert, sucht der Scanner nicht und das Programm meldet es nicht.

### Originalbedingungen prüfen
Bringt der Fund keinen direkten Link mit, sucht Gemini per Google-Suche die offiziellen
Bedingungen – aus Kontingentgründen nur für vielversprechende Funde (Netto ≥ 50 % der Schwelle,
höchstens 8 pro Lauf). Eine URL wird nur übernommen, wenn ihre Domain unter den echten
Suchtreffern ist, die Seite ladbar ist (robots.txt) und den Anbieter nennt. Dann wird der Fund mit
den Originalbedingungen neu analysiert.

## Harte Grenzen (fest eingebaut)

- Wege, die falsche Angaben, Mehrfachkonten unter falschen Daten, fremde Identitäten oder das
  Umgehen von Identitätsprüfungen erfordern, werden als „nicht empfohlen“ markiert und nie gemeldet
  (`scoring.py`, Test `test_unzulaessig_wird_nie_gemeldet`).
- Seriositätsprüfung: Vorkasse, Krypto-Überweisung an Unbekannte, Gewinnversprechen ohne Bedingungen → nie melden.
- Keine Ausführung von Käufen/Orders/Kontoeröffnungen – es gibt dafür schlicht keinen Code.
- Keine Speicherung von Bank-/Ausweisdaten; die DB enthält nur Titel, URLs, Hashes und eigene Analysen. Secrets nur in `.env`, nie im Log.
- Fehlen die Originalbedingungen, steht das in der Meldung („NICHT gelesen“) und die Konfidenz sinkt – es wird nicht geraten.

## Quellen und Regeln

Geprüft am 2026-11-21 (robots.txt und Impressum/AGB):

| Quelle | Zugriff | Hinweis |
|---|---|---|
| modern-banking.de | HTML, Broker- und Giro-Neukundenseite | robots.txt erlaubt `/` für alle Bots. Das Impressum untersagt automatisierte Zugriffe *über robots.txt hinaus* und das Speichern von Inhalten → Abruf 1× täglich, **kein Platten-Cache, kein Rohtext in der DB**. Affiliate-Links `/r/` sind per robots.txt gesperrt und werden nicht verfolgt. Eine eigene Tagesgeld-Neukundenseite gibt es nicht. |
| mydealz.de, Gruppen Konto & Kreditkarten, Cashback, Geld zurück, Strom & Gas, DSL, Preisfehler, Payback | **nur RSS** (`/rss/gruppe/…`, geprüft 2026-11-22) | robots.txt sperrt KI-Crawler (u. a. ClaudeBot) für die Website. Deal Radar ist kein Crawler, nutzt aber trotzdem ausschließlich den öffentlichen Feed. |
| finanztip.de/deals | HTML | `Crawl-delay: 3` → 3 s Pause. Enthält auch Nicht-Finanz-Deals (Handytarife); die fallen über Vorfilter/Schwelle raus. |
| brokerexperte.de | HTML, Tabelle „Neukundenprämien“ auf der Startseite | `/neukundenaktionen/` existiert nicht (404). Impressum untersagt die Vervielfältigung der Tabellen → keine Speicherung, nur Auswertung. |
| depot-vergleichen.de/neukundenaktionen | HTML | keine Einschränkungen gefunden. |
| praemien-programme.de | HTML, Detailseiten Depot, Giro, Tagesgeld/Festgeld | robots.txt sperrt u. a. `/ext.php` (Weiterleitungen) – wird nicht verfolgt. Liefert viele Einträge (~200). |
| Anbieterseiten | HTML/PDF der Teilnahmebedingungen | robots.txt wird für jeden Host geprüft. Findet sich im Fund kein direkter Link, sucht das LLM per Websuche die offizielle Bedingungsseite; geladen wird sie von Deal Radar selbst. |

Allgemein: User-Agent `DealRadar/0.1 (privater, nicht-kommerzieller Aktions-Monitor…)`,
mindestens 2 s Pause pro Host, robots.txt nach RFC 9309 inkl. `*` und `$`, Cache 12 h.
Affiliate-/Tracking-Redirects (`/r/`, `/go/`, `/visit/`, `linkcode=` …) werden nie aufgerufen.

Die Test-Fixtures bilden die HTML-Struktur nach, enthalten aber Platzhaltertexte statt der
Originalseiten (wegen der Vervielfältigungsverbote).

### Quelle hinzufügen

1. `dealradar/sources/meinequelle.py` mit `parse(content, page_url, source) -> list[RawDeal]`
   anlegen (optional `fetch(fetcher, cfg, source)` für Sonderfälle).
   `source_key` muss stabil sein (Anker, GUID, Anbietername).
2. In `sources.yaml` eintragen:
   ```yaml
   meinequelle:
     module: meinequelle
     enabled: true
     cache: true
     pages: [https://…]
   ```
3. Fixture mit nachgebauter Struktur unter `tests/fixtures/` und Test in `tests/test_parsers.py`.
4. Vorher robots.txt und Nutzungsbedingungen prüfen und hier in der Tabelle vermerken.

## Überwachung

- Fehler einer Quelle brechen den Lauf nicht ab (Log + Zusammenfassung).
- Liefert ein Parser 0 Ergebnisse (Strukturänderung) oder leitet eine Seite um → Warnung über die aktiven Kanäle (höchstens 1× pro Tag und Quelle).
- Fällt eine Quelle 3 Tage aus → Warn-Mail/-Push.
- Kein Treffer → keine Nachricht, nur eine Logzeile in `logs/dealradar.log`.
- Bereits gemeldete Deals: Bedingungen werden alle 7 Tage neu geladen; geänderte Bedingungen oder Inhalte → Meldung als „Update“.

## Täglicher Lauf in der Cloud (GitHub Actions, Standard)

Läuft unabhängig vom Mac: `.github/workflows/dealradar.yml`, täglich 08:15 UTC
(09:15 MEZ, im Sommer 10:15 MESZ; GitHub startet geplante Läufe oft einige Minuten später).
Warum nicht 07:30: Die kostenlose Gemini-Stufe erlaubt je Modell nur 20 Anfragen pro Tag und setzt
um Mitternacht Pazifikzeit (09:00 deutscher Zeit) zurück. Ein Lauf kurz danach hat das volle
Tageskontingent; deshalb gibt es auch acht Ausweichmodelle (zusammen ca. 180 Anfragen/Tag,
ein Lauf braucht ca. 40).

- Modell: `DEALRADAR_LLM_PROVIDER=auto` = erst **Gemini** (kostenlose Stufe, `llm.gemini`), und sobald
  dessen Kontingent leer ist oder es nicht antwortet, **Gemma 3 4B über Ollama auf dem Runner**
  (`OLLAMA_MODEL` im Workflow, Modell wird im Actions-Cache gehalten, Zeitbudget 75 min, ca. 30 Deals).
  Ohne `GEMINI_API_KEY` läuft nur das Open-Source-Modell. Lokal bleibt `provider: ollama`.
- Benachrichtigung: **GitHub-Issue** im Repo (`DEALRADAR_CHANNELS=github`). Die Mail kommt von GitHub an die
  Adresse des GitHub-Kontos, wenn das Repo beobachtet wird (Watch → All Activity). Optional zusätzlich
  ein eigener **E-Mail-Digest** per SMTP (`github,email`).
  Jeden Morgen kommt ein Issue: mit Treffern (Label `deal`, bleibt offen) oder ein kurzer Tagesbericht
  (Label `bericht`, sofort geschlossen). Jedes Issue erwähnt den Repo-Inhaber (`DEALRADAR_GITHUB_NOTIFY`),
  GitHub mailt Erwähnungen standardmäßig – auch ohne Watch.
- **Ausgeschlossen (Entscheidung des Nutzers, 2026-11-27):** Strom/Gas, Internet/Mobilfunk/TV und Verträge
  mit Laufzeit (Versicherung, Abo, Streaming). Kategorie `wechselbonus` ist aus, die mydealz-Gruppen Strom/Gas
  und DSL sind entfernt, und `dealradar/exclude.py` sortiert solche Titel/Anbieter aus allen Quellen vor der
  Analyse aus (auch bereits zurückgestellte Treffer). Gemeldet wird nur, was direkt Geld bringt (Prämie,
  Bonus, Zins, Cashback) oder stark reduziert ist (Preisfehler, hoher Rabatt).
- mydealz-Hot-Deals (`/rss/trending`): nur Einträge mit Cashback, Portal, Geld zurück, Guthaben,
  Prämie/Bonus, Preisfehler oder Punkten im Titel oder mit mind. 50 % Rabatt im Titel.
- Startzeit: GitHub startet geplante Läufe unter Last oft Stunden zu spät. Darum drei Startzeiten
  (03:47, 05:47, 08:15 UTC); der Job `gate` lässt pro Tag nur den ersten erfolgreichen geplanten Lauf zu.
  Ein Issue pro Lauf mit Treffern (Label `deal`), Warnungen mit Label `warnung`; abhaken („Habe ich
  gemacht“) direkt im Issue, erledigt = Issue schließen. Die Mail braucht `SMTP_USER`, `SMTP_PASSWORD`
  (Gmail-App-Passwort, setzt Bestätigung in zwei Schritten voraus) und `MAIL_TO` in `.env`; danach
  `scripts/set_github_secrets.sh` (überträgt die Werte als Secrets, zeigt sie nicht an). Fehlen sie,
  wird der Mail-Kanal mit Warnung übersprungen und nur das Issue erstellt.
  Der Vault ist aus der Cloud nicht erreichbar; das Issue enthält dieselben Angaben wie die Vault-Notiz.
- Datenbank: über den Actions-Cache von Lauf zu Lauf weitergereicht (auch nach Fehlern gesichert).
  Geht der Cache verloren (z. B. 7 Tage kein Lauf), beginnt das Dedupe neu – schlimmstenfalls
  kommen bekannte Deals einmal erneut.
- Kontingent: Ist das Tageslimit der kostenlosen Stufe erreicht, endet die Analyse sauber; offene
  Deals bleiben für den nächsten Tag; wird dabei gar nichts analysiert, kommt ein Warn-Issue.
- Log jedes Laufs: im Actions-Tab als Artefakt `dealradar-log` (14 Tage).

Secrets (Repo → Settings → Secrets and variables → Actions, oder `gh secret set`):
nur `GEMINI_API_KEY` (aus `.env` übertragen mit `./scripts/set_github_secrets.sh`). `GITHUB_TOKEN`
stellt GitHub selbst bereit.
Sofort testen: `gh workflow run deal-radar`, dann `gh run watch`.

### Alternative: lokal mit launchd (07:30 Europe/Berlin)

Nur sinnvoll, wenn der Mac morgens läuft – und **nicht gleichzeitig** mit dem Cloud-Lauf
(zwei Datenbanken = doppelte Meldungen).

```bash
./scripts/install_launchd.sh
launchctl kickstart -k gui/$(id -u)/de.dealradar.daily   # einmal sofort testen
```

Schläft der Mac um 07:30, holt launchd den Lauf beim Aufwachen nach; die Ollama-App muss laufen.
Entfernen: `launchctl bootout gui/$(id -u)/de.dealradar.daily && rm ~/Library/LaunchAgents/de.dealradar.daily.plist`.

## Entscheidungen (selbst getroffen, hier dokumentiert)

- **Öffentliches Repo** (Wunsch: mehr Rechenzeit): Code und Treffer-Issues sind öffentlich, persönliche Daten nicht. Commits laufen über die GitHub-No-Reply-Adresse, der Obsidian-Pfad steht nur in `.env` (`DEALRADAR_VAULT_DIR`), Secrets nur als GitHub-Secret. Die frühere private Historie liegt unverändert im privaten Repo `deal-radar-alt` (Zeitplan dort abgeschaltet).

- **Benachrichtigung:** E-Mail als Standard (Auswahl im Chat), ntfy ist fertig eingebaut und per `notify.channels: [email, ntfy, vault]` zuschaltbar. Mail kommt als ein Digest pro Lauf (`email_mode: per_deal` für eine Mail pro Treffer).
- **Modell:** lokal `gemma3:12b` über Ollama (kostenlos, keine Daten verlassen den Mac). `llama3.1` (8B) wurde getestet und war zu unzuverlässig (falsche Kategorien, erfundene Order-Parameter, vertauschte Ausschlüsse). Lokal gibt es keine Websuche nach Bedingungen – meist liegen nur die Texte der Aggregatoren vor, die Meldung sagt das dann ("Bedingungen NICHT gelesen"). Alternative: `provider: anthropic` mit `claude-opus-5-5` (ca. 0,05–0,15 € pro Deal, inkl. Websuche nach Originalbedingungen und serverseitigem Fallback).
- **Plausibilitätsprüfungen in Python** (`models.sanitize`, `check_frist`), weil lokale Modelle Schemafehler machen: "nicht empfohlen" gilt nur mit echter Begründung (Täuschung, Identität, Mehrfachkonten), leere Zins-/Orderblöcke werden entfernt, Zinsangebote unter "sonstiges" werden zu "neukundenzins", eine Frist, die nicht als Datum im Text steht, senkt die Konfidenz.
- **Erster Lauf:** rund 250 eindeutige Angebote, lokal ca. 4 min pro Deal. Mit `max_deals_per_run: 15` (≈ 1 h) wird der Bestand über gut zwei Wochen abgearbeitet, größte Beträge zuerst. Danach kommen täglich nur neue/geänderte Funde ins LLM.
- **Duplikate über Quellen:** Schlüssel aus Anbietername + größtem Betrag im Titel; die Quelle mit dem ausführlichsten Text (Reihenfolge in `sources.yaml`, modern-banking zuerst) wird analysiert.
- **Vorfilter:** Funde, deren größter Betrag unter der Schwelle liegt und die weder Zins- noch Pro-Order-Bezug haben, gehen nicht ins LLM (der Nettogewinn kann nicht über der Prämie liegen).
- **Lange Bedingungen** werden auf die relevanten Absätze gekürzt (Prämie, Teilnahme, Limits, Kombination, Fristen, Rückforderung; Auslassungen als „[…]“), damit auch das kleine Cloud-Modell sie lesen kann. Der Klausel-Scanner und die Änderungserkennung arbeiten trotzdem auf dem vollständigen Text, und die Meldung sagt, dass gekürzt wurde. Nur Texte über dem 20-Fachen des Limits (ganze AGB-Sammlungen) gelten weiter als „zu lang“. (Früher: nicht gekürzt – geändert, weil dadurch viele Bedingungen gar nicht gelesen wurden.)
- **Cloud-Modell ohne Key:** GitHub Models wurde geprüft – der Dienst ist eingestellt (Endpunkt antwortet auf alles mit „OK“). Stattdessen läuft **Gemma 3 4B** (offene Gewichte, Ollama) direkt auf dem GitHub-Runner: kein Key, kein Tageslimit, nur Rechenzeit. Das Repo ist öffentlich: unbegrenzte Actions-Minuten und ein 4-Kern-Runner mit 16 GB (privat: 2 Kerne, 2 000 min/Monat). Gemessen ca. 2,3 min pro Deal, Budget 75 min und höchstens 40 Deals pro Lauf (`DEALRADAR_OLLAMA_MAX_MINUTES`, `DEALRADAR_MAX_DEALS` im Workflow). Kleiner als lokal (12B), daher weniger zuverlässig – die Python-Prüfungen und der Klausel-Scanner fangen das ab.
- **Vault:** Nur neue Dateien in `Deals/`; existiert die Tagesdatei schon, entsteht `YYYY-MM-DD-2.md`. Warnungen gehen nicht in den Vault.
