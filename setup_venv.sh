#!/bin/bash
set -e

echo "=== FPT-MCP: Setup ==="

# Auto-detect project root (directory where this script lives)
FPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_LABEL_MCP="com.fpt-mcp.server"

# -----------------------------------------------
# 1. Create venv
# -----------------------------------------------
echo ""
echo "[1/5] Creating venv..."
cd "$FPT_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -e . -q
echo "      OK: $(python3 --version) in $FPT_DIR/.venv"
echo "      Packages:"
pip list 2>/dev/null | grep -iE "mcp|shotgun|pydantic|dotenv|httpx|pyside" | sed 's/^/      /'
deactivate

# -----------------------------------------------
# 2. Check .env
# -----------------------------------------------
echo ""
echo "[2/5] Checking .env..."
if [ ! -f "$FPT_DIR/.env" ]; then
    cp "$FPT_DIR/.env.example" "$FPT_DIR/.env"
    echo "      Created .env from .env.example — edit it with your credentials."
else
    echo "      OK: .env exists"
fi

# -----------------------------------------------
# 3. Install MCP HTTP server as launchd service
# -----------------------------------------------
echo ""
echo "[3/5] Installing MCP server launchd service..."

FPT_PYTHON="$FPT_DIR/.venv/bin/python3"
FPT_WORKDIR="$FPT_DIR"

# Unload existing services (ignore errors)
launchctl unload "$HOME/Library/LaunchAgents/$PLIST_LABEL_MCP.plist" 2>/dev/null || true
# Clean up deprecated services from previous versions
for old_label in $(launchctl list 2>/dev/null | grep -o '[^ ]*fpt[^ ]*' | grep -v "$PLIST_LABEL_MCP"); do
    launchctl unload "$HOME/Library/LaunchAgents/${old_label}.plist" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/${old_label}.plist" 2>/dev/null || true
done

# Generate MCP server plist
cat > "$HOME/Library/LaunchAgents/$PLIST_LABEL_MCP.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_LABEL_MCP</string>

    <key>ProgramArguments</key>
    <array>
        <string>$FPT_PYTHON</string>
        <string>-m</string>
        <string>fpt_mcp.server</string>
        <string>--http</string>
        <string>--port</string>
        <string>8090</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$FPT_WORKDIR</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>/tmp/fpt-mcp.log</string>

    <key>StandardErrorPath</key>
    <string>/tmp/fpt-mcp.err</string>
</dict>
</plist>
PLIST

launchctl load "$HOME/Library/LaunchAgents/$PLIST_LABEL_MCP.plist"
echo "      OK: MCP server service installed"

# -----------------------------------------------
# 4. Build Qt console .app bundle (protocol handler)
# -----------------------------------------------
echo ""
echo "[4/5] Building Qt console app bundle..."

APP_DIR="$HOME/Applications"
mkdir -p "$APP_DIR"

source "$FPT_DIR/.venv/bin/activate"
python3 -m fpt_mcp.qt.build_app_bundle \
    --venv "$FPT_DIR/.venv" \
    --output "$APP_DIR" \
    --project-dir "$FPT_DIR"
deactivate

# Register the protocol handler by opening the app once
echo "      Registering fpt-mcp:// protocol handler..."
open "$APP_DIR/FPT-MCP Console.app" 2>/dev/null || true
sleep 2
# Close it after registration
osascript -e 'quit app "FPT-MCP Console"' 2>/dev/null || true

echo "      OK: FPT-MCP Console.app installed in $APP_DIR"

# -----------------------------------------------
# 5. Verify
# -----------------------------------------------
echo ""
echo "[5/5] Verifying..."
sleep 2

if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8090/mcp 2>/dev/null | grep -q "200\|405"; then
    echo "      OK: MCP HTTP server running on :8090"
else
    echo "      WARN: MCP HTTP server not responding on :8090"
    echo "      Check: cat /tmp/fpt-mcp.err"
fi

echo ""
echo "=== Done ==="
echo ""
echo "MCP HTTP server:  http://127.0.0.1:8090"
echo "Qt Console:       ~/Applications/FPT-MCP Console.app"
echo "Protocol:         fpt-mcp://chat?entity_type=Asset&selected_ids=123"
echo ""
echo "ShotGrid AMI URL (light payload):"
echo "  fpt-mcp://chat?entity_type={entity_type}&selected_ids={selected_ids}&project_id={project_id}&project_name={project_name}&user_login={user_login}"
echo ""
echo "Manage MCP server:"
echo "  launchctl stop $PLIST_LABEL_MCP"
echo "  launchctl start $PLIST_LABEL_MCP"
echo "  launchctl unload ~/Library/LaunchAgents/$PLIST_LABEL_MCP.plist"
echo ""
echo "Launch console manually:"
echo "  fpt-console"
echo "  fpt-console --entity-type Shot --entity-id 456"
echo "  open 'fpt-mcp://chat?entity_type=Asset&selected_ids=123'"
