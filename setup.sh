#!/bin/bash
# Sets up Soccer Calendar Checker on this Mac.
# Run once after cloning the repo.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_LABEL="com.$(whoami).soccer-calendar"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
APP_BUNDLE="$SCRIPT_DIR/SoccerCalendar.app"
APP_BIN="$APP_BUNDLE/Contents/MacOS/SoccerCalendar"

echo "=== Soccer Calendar Checker Setup ==="
echo "Directory : $SCRIPT_DIR"
echo "User      : $(whoami)"
echo ""

# 1. Create venv and install dependencies
# Calendar reads use EventKit (pyobjc), which needs a real Python framework
# build — the system /usr/bin/python3 doesn't have pyobjc available.
if [ ! -d "$SCRIPT_DIR/venv" ]; then
  echo "Creating virtualenv..."
  python3 -m venv "$SCRIPT_DIR/venv"
fi
echo "Installing Python dependencies..."
"$SCRIPT_DIR/venv/bin/pip" install -q --upgrade pip
"$SCRIPT_DIR/venv/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
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

# 3. Build the launcher app bundle.
# macOS's TCC policy refuses to show (or even reliably honor) the Calendar
# access prompt when the "responsible" process resolves to a bare
# interpreter binary run outside an app bundle. Wrapping the Python
# invocation in a compiled binary living inside a proper .app bundle (with
# an Info.plist declaring NSCalendarsUsageDescription /
# NSCalendarsFullAccessUsageDescription) gives TCC a real, stable app to
# attribute the request and grant to.
echo ""
echo "Building launcher app bundle..."
mkdir -p "$APP_BUNDLE/Contents/MacOS"

cat > "$APP_BUNDLE/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>SoccerCalendar</string>
    <key>CFBundleIdentifier</key>
    <string>com.$(whoami).soccer-calendar</string>
    <key>CFBundleName</key>
    <string>SoccerCalendar</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSCalendarsUsageDescription</key>
    <string>Soccer Calendar Checker reads game schedules to compare calendars and send alerts.</string>
    <key>NSCalendarsFullAccessUsageDescription</key>
    <string>Soccer Calendar Checker reads game schedules to compare calendars and send alerts.</string>
</dict>
</plist>
EOF

clang -o "$APP_BIN" "$SCRIPT_DIR/launcher.c"
chmod +x "$APP_BIN"

# 4. Grant Calendar access
echo ""
echo "NOTE: When the app first runs, macOS may prompt for Calendar access."
echo "      Click OK to allow it."

# 5. Write launchd plist
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
        <string>${APP_BIN}</string>
        <string>${SCRIPT_DIR}/venv/bin/python</string>
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
