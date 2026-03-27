#!/bin/bash
# Claude Usage Menu Bar — install & setup
# Usage: curl -sL <url> | bash   OR   ./install.sh

set -e

APP_DIR="$HOME/.claude-usage-menubar"
PLIST_NAME="com.claude-usage-menubar.plist"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== Claude Usage Menu Bar ==="
echo ""

# Check prerequisites
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 not found. Install from https://python.org"
    exit 1
fi

# Check if logged into Claude Code (credentials in Keychain)
if ! security find-generic-password -s "Claude Code-credentials" -a "$USER" -w &>/dev/null; then
    echo "Error: Not logged into Claude Code. Run 'claude' first and sign in."
    exit 1
fi

# Create app directory
mkdir -p "$APP_DIR"

# Copy or download files
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/claude_usage.py" ]; then
    cp "$SCRIPT_DIR/claude_usage.py" "$APP_DIR/"
    cp "$SCRIPT_DIR/requirements.txt" "$APP_DIR/"
else
    echo "Error: claude_usage.py not found next to install.sh"
    exit 1
fi

# Install dependencies
echo "Installing dependencies..."
pip3 install -q -r "$APP_DIR/requirements.txt"

# Stop old instance if running
launchctl unload "$PLIST_PATH" 2>/dev/null || true
pkill -f "claude_usage.py" 2>/dev/null || true

# Create LaunchAgent for autostart
cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_NAME</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(which python3)</string>
        <string>$APP_DIR/claude_usage.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardErrorPath</key>
    <string>/tmp/claude-usage-menubar.log</string>
</dict>
</plist>
EOF

# Start
launchctl load "$PLIST_PATH"

echo ""
echo "Done! Claude Usage widget is now in your menu bar."
echo "It will auto-start on login."
echo ""
echo "Commands:"
echo "  Stop:      launchctl unload ~/Library/LaunchAgents/$PLIST_NAME"
echo "  Start:     launchctl load ~/Library/LaunchAgents/$PLIST_NAME"
echo "  Uninstall: launchctl unload ~/Library/LaunchAgents/$PLIST_NAME && rm -rf $APP_DIR $PLIST_PATH"
echo "  Logs:      cat /tmp/claude-usage-menubar.log"
