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
echo "[1/4] Creating venv..."
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
echo "[2/4] Checking .env..."
ENV_PLACEHOLDERS=0
if [ ! -f "$FPT_DIR/.env" ]; then
    cp "$FPT_DIR/.env.example" "$FPT_DIR/.env"
    echo "      Created .env from .env.example — edit it with your credentials."
    ENV_PLACEHOLDERS=1
else
    echo "      OK: .env exists"
fi

# Content validation — detects a stale .env still holding the .env.example
# placeholder values. Without this check the first real ShotGrid call
# fails with an opaque SSL CERTIFICATE_VERIFY_FAILED error.
if [ -f "$FPT_DIR/.env" ] && [ $ENV_PLACEHOLDERS -eq 0 ]; then
    if grep -qE '^SHOTGRID_URL=https?://(YOUR_SITE|yoursite\.shotgrid)' "$FPT_DIR/.env"; then
        ENV_PLACEHOLDERS=1
    fi
    if grep -qE '^SHOTGRID_SCRIPT_NAME=your_script_name' "$FPT_DIR/.env"; then
        ENV_PLACEHOLDERS=1
    fi
    if grep -qE '^SHOTGRID_SCRIPT_KEY=(your_script_key|your_key)' "$FPT_DIR/.env"; then
        ENV_PLACEHOLDERS=1
    fi
fi

if [ $ENV_PLACEHOLDERS -eq 1 ]; then
    echo ""
    echo "      ⚠  WARNING: .env holds placeholder values from .env.example."
    echo "         Edit $FPT_DIR/.env with your real ShotGrid credentials"
    echo "         BEFORE using fpt-mcp. Every call will fail with an SSL"
    echo "         certificate error until this is done."
fi

# -----------------------------------------------
# 3. (removed) MCP HTTP launchd service
# -----------------------------------------------
# A launchd agent used to run `fpt_mcp.server --http --port 8090` at boot.
# It is NOT installed any more, and nothing in this project needs it.
#
# Why it existed: the first AMI console (March 2026) was an HTML page in the
# browser, and a web page cannot spawn a stdio process — it needed an HTTP MCP
# endpoint to call. The native Qt console replaced that page, and the console
# spawns `claude`, which starts its own private fpt-mcp over stdio per message.
# The daemon has had no caller since.
#
# It was already diagnosed and unloaded by hand once (see HANDOFF_CHAT_70:
# "las consolas usan stdio per-mensaje; no lo necesitan"), but this installer
# kept reinstalling it, so the decision silently reverted on the next run. That
# is what this removal fixes. It had also caused a real incident before, in
# Chat 40: three concurrent fpt_mcp.server processes with divergent
# environments made an SSL hostname mismatch far harder to diagnose.
#
# Leaving it running is not free: the endpoint has NO authentication, so any
# local process could drive production ShotGrid through it.
#
# The --http transport itself is kept — it is the right entry point for an
# external MCP client (Claude Desktop, a script). Start it deliberately:
#
#   .venv/bin/python -m fpt_mcp.server --http --port 8090
#
# Do not expose it beyond localhost without putting authentication in front.

# Remove the agent this installer used to create, plus any older variants.
launchctl unload "$HOME/Library/LaunchAgents/$PLIST_LABEL_MCP.plist" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$PLIST_LABEL_MCP.plist" 2>/dev/null || true
for old_label in $(launchctl list 2>/dev/null | grep -o '[^ ]*fpt[^ ]*' | grep -v "$PLIST_LABEL_MCP"); do
    launchctl unload "$HOME/Library/LaunchAgents/${old_label}.plist" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/${old_label}.plist" 2>/dev/null || true
done

# -----------------------------------------------
# 4. Build Qt console .app bundle (protocol handler)
# -----------------------------------------------
echo ""
echo "[4/4] Building Qt console app bundle..."

APP_DIR="$HOME/Applications"
mkdir -p "$APP_DIR"

source "$FPT_DIR/.venv/bin/activate"
python3 -m fpt_mcp.qt.build_app_bundle \
    --venv "$FPT_DIR/.venv" \
    --output "$APP_DIR" \
    --project-dir "$FPT_DIR"
deactivate

# Register the protocol handler with macOS
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP_DIR/FPT-MCP Console.app" 2>/dev/null || true

echo "      OK: FPT-MCP Console.app in $APP_DIR"

# -----------------------------------------------
# Verify
# -----------------------------------------------
echo ""
echo "Verifying..."

if [ -d "$APP_DIR/FPT-MCP Console.app" ]; then
    echo "      OK: Console.app built and registered for fpt-mcp://"
else
    echo "      WARN: Console.app missing — the AMI protocol URL will not open."
fi

echo ""
echo "=== Done ==="
echo ""
echo "  Qt console:  ~/Applications/FPT-MCP Console.app"
echo "  Protocol:    fpt-mcp://"
echo ""
echo "  The console spawns claude, which starts fpt-mcp over stdio per message."
echo "  No background service is installed or needed."
echo ""
echo "ShotGrid AMI URL (light payload):"
echo "  fpt-mcp://chat?entity_type={entity_type}&selected_ids={selected_ids}&project_id={project_id}&project_name={project_name}&user_login={user_login}"
echo ""
echo "Manage:"
echo "  launchctl stop/start $PLIST_LABEL_MCP"
