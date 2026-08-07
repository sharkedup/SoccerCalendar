#!/bin/bash
# Sets up Soccer Calendar Checker on this Mac.
# Run once after cloning the repo.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_LABEL="com.$(whoami).soccer-calendar"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
PYTHON=$(which python3)

echo "=== Soccer Calendar Checker Setup ==="
echo "Directory : $SCRIPT_DIR"
echo "Python    : $PYTHON"
echo "User      : $(whoami)"
echo ""

# 1. Install dependencies
echo "Installing Python dependencies..."
pip3 install -r "$SCRIPT_DIR/requirements.txt" --quiet
echo "Done."

# 2. Copy config if not already present
if [ ! -f "$SCRIPT_DIR/config.json" ]; then
  cp "$SCRIPT_DIR/config.example.json" "$SCRIPT_DIR/config.json"
  echo ""
  echo "Created config.json from example."
  echo ">> Open http://localhost:8080/config after starting to fill in your settings."
else
  echo "config.json already exists — skipping."
fi

# 3. Grant Calendar access
echo ""
echo "NOTE: When the app first runs, macOS may prompt for Calendar access."
echo "      Click OK to allow it."

# 4. Write launchd plist
echo ""
echo "Installing launchd agent (auto-start on login)..."

cat > "$PLIST_DEST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON}</string>
        <string>${SCRIPT_DIR}/app.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>

    <key>KeepAlive</key>
    <true/>

    <key>RunAtLoad</key>
    <true/>

    <key>StandardOutPath</key>
    <string>${SCRIPT_DIR}/soccer_calendar.log</string>

    <key>StandardErrorPath</key>
    <string>${SCRIPT_DIR}/soccer_calendar_error.log</string>
</dict>
</plist>
PLIST

# Unload if already running
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"

echo ""
echo "=== Setup complete ==="
echo ""
echo "The app is now running and will restart automatically on login."
echo ""
echo "  Dashboard : http://localhost:8080"
echo "  Settings  : http://localhost:8080/config"
echo "  Logs      : tail -f $SCRIPT_DIR/soccer_calendar.log"
echo ""
echo "To uninstall:"
echo "  launchctl unload $PLIST_DEST && rm $PLIST_DEST"
