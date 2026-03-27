#!/bin/bash
set -e

echo "=== FPT-MCP: Setup ==="

# Auto-detect project root (directory where this script lives)
FPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_LABEL_MCP="com.fpt-mcp.server"
PLIST_LABEL_AMI="com.fpt-mcp.ami"

# -----------------------------------------------
# 1. Create venv
# -----------------------------------------------
echo ""
echo "[1/4] Creating venv..."
cd "$FPT_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -e . -q
echo "      OK: $(python3 --version) in $FPT_DIR/.venv"
echo "      Packages:"
pip list 2>/dev/null | grep -iE "mcp|shotgun|pydantic|dotenv|httpx" | sed 's/^/      /'
deactivate

# -----------------------------------------------
# 2. Check .env
# -----------------------------------------------
echo ""
echo "[2/4] Checking .env..."
if [ ! -f "$FPT_DIR/.env" ]; then
    cp "$FPT_DIR/.env.example" "$FPT_DIR/.env"
    echo "      Created .env from .env.example — edit it with your credentials."
else
    echo "      OK: .env exists"
fi

# -----------------------------------------------
# 3. Generate and install launchd plists (macOS)
# -----------------------------------------------
echo ""
echo "[3/4] Installing launchd services..."

FPT_PYTHON="$FPT_DIR/.venv/bin/python3"
FPT_WORKDIR="$FPT_DIR"

# Unload existing services (ignore errors)
launchctl unload "$HOME/Library/LaunchAgents/$PLIST_LABEL_MCP.plist" 2>/dev/null || true
launchctl unload "$HOME/Library/LaunchAgents/$PLIST_LABEL_AMI.plist" 2>/dev/null || true

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

# Generate AMI console plist
cat > "$HOME/Library/LaunchAgents/$PLIST_LABEL_AMI.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_LABEL_AMI</string>

    <key>ProgramArguments</key>
    <array>
        <string>$FPT_PYTHON</string>
        <string>-m</string>
        <string>fpt_mcp.ami.handler</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$FPT_WORKDIR</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>/tmp/fpt-ami.log</string>

    <key>StandardErrorPath</key>
    <string>/tmp/fpt-ami.err</string>
</dict>
</plist>
PLIST

# Load services
launchctl load "$HOME/Library/LaunchAgents/$PLIST_LABEL_MCP.plist"
launchctl load "$HOME/Library/LaunchAgents/$PLIST_LABEL_AMI.plist"
echo "      OK: services installed and loaded"

# -----------------------------------------------
# 4. Verify
# -----------------------------------------------
echo ""
echo "[4/4] Verifying..."
sleep 2

if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8090/mcp 2>/dev/null | grep -q "200\|405"; then
    echo "      OK: MCP HTTP server running on :8090"
else
    echo "      WARN: MCP HTTP server not responding on :8090"
    echo "      Check: cat /tmp/fpt-mcp.err"
fi

if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8091/health 2>/dev/null | grep -q "200"; then
    echo "      OK: AMI console running on :8091"
else
    echo "      WARN: AMI console not responding on :8091"
    echo "      Check: cat /tmp/fpt-ami.err"
fi

echo ""
echo "=== Done ==="
echo "MCP HTTP:     http://127.0.0.1:8090"
echo "AMI Console:  http://127.0.0.1:8091/ami"
echo ""
echo "Manage services:"
echo "  launchctl stop $PLIST_LABEL_MCP"
echo "  launchctl start $PLIST_LABEL_MCP"
echo "  launchctl unload ~/Library/LaunchAgents/$PLIST_LABEL_MCP.plist"
