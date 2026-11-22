#!/usr/bin/env bash
# Überträgt die Schlüssel aus .env als verschlüsselte GitHub-Secrets (Werte werden nicht angezeigt).
set -euo pipefail
cd "$(dirname "$0")/.."
for k in GEMINI_API_KEY; do
  v=$(grep -E "^$k=" .env | head -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//')
  if [ -n "$v" ]; then printf '%s' "$v" | gh secret set "$k" >/dev/null && echo "✓ $k"; else echo "– $k leer, übersprungen"; fi
done
