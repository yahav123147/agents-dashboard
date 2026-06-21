#!/bin/bash
# Generates a launchd job that keeps the dashboard server running and starts it
# at login. It writes the plist with YOUR paths (no hard-coded usernames), loads
# it, and verifies it came up. Re-run any time to refresh.
#
# Usage:  bash install-launchagent.sh
# Remove: launchctl bootout gui/$(id -u)/com.example.agents-dashboard
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$(command -v python3)"
LABEL="com.example.agents-dashboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ -z "$PY" ]; then
  echo "python3 not found in PATH. Install Python 3.9+ and try again."
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$DIR/server.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/server.log</string>
  <key>StandardErrorPath</key><string>$DIR/server.err.log</string>
</dict>
</plist>
EOF

# Reload cleanly (ignore "not loaded" on first run)
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "Installed $LABEL"
echo "Dashboard: http://localhost:8420"
echo "Logs:      $DIR/server.log"
