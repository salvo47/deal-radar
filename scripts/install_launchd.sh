#!/bin/zsh
# Installiert den täglichen Lauf (07:30 Europe/Berlin) als LaunchAgent des aktuellen Nutzers.
set -euo pipefail
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$HOME/Library/LaunchAgents/de.dealradar.daily.plist"
mkdir -p "$PROJECT/logs" "$HOME/Library/LaunchAgents"
sed "s#__PROJECT__#$PROJECT#g" "$PROJECT/launchd/de.dealradar.daily.plist" > "$TARGET"
launchctl bootout "gui/$(id -u)/de.dealradar.daily" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"
echo "Installiert: $TARGET"
echo "Sofort testen:  launchctl kickstart -k gui/$(id -u)/de.dealradar.daily"
echo "Entfernen:      launchctl bootout gui/$(id -u)/de.dealradar.daily && rm $TARGET"
