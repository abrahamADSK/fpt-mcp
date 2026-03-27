# fpt-mcp

MCP server for **Autodesk Flow Production Tracking** (formerly ShotGrid).
Part of the VFX pipeline ecosystem alongside [maya-mcp](https://github.com/abrahamADSK/maya-mcp) and [flame-mcp](https://github.com/abrahamADSK/flame-mcp).

```
Claude Desktop / Claude Code / Terminal
├── maya-mcp    → 3D / Arnold render
├── flame-mcp   → compositing
└── fpt-mcp     → production tracking (this repo)
        ├── stdio       → Claude Desktop / Claude Code
        ├── HTTP        → Maya, Flame, scripts, inter-service
        └── AMI console → ShotGrid Action Menu Items
```

## Tools

| Tool | Description |
|------|-------------|
| `fpt_find_assets` | List assets with filters (type, status, name) |
| `fpt_get_asset_image` | Download reference image from a Version or Asset thumbnail |
| `fpt_create_sequence` | Create a Sequence entity |
| `fpt_create_shot` | Create a Shot inside a Sequence |
| `fpt_create_version` | Create a Version linked to Asset/Shot, optionally upload movie |
| `fpt_upload_thumbnail` | Upload thumbnail to any entity |
| `fpt_create_published_file` | Publish OBJ / Texture / Maya Scene / EXR with Toolkit-compatible paths |
| `fpt_find_published_files` | Query publishes by entity, type, etc. |

## Hybrid approach

Entity CRUD uses `shotgun_api3` directly. Publish paths follow `tk-config-default2` conventions so that Toolkit loaders (tk-multi-loader2) in Maya and Flame can pick them up natively.

## Install

```bash
cd fpt-mcp
pip install -e .
```

## Configure

Copy `.env.example` → `.env` and fill in your credentials:

```
SHOTGRID_URL=https://yoursite.shotgrid.autodesk.com
SHOTGRID_SCRIPT_NAME=your_script_name
SHOTGRID_SCRIPT_KEY=your_key
SHOTGRID_PROJECT_ID=123
```

## Transports

### stdio (Claude Desktop / Claude Code)

Default mode. The server communicates via standard input/output as a subprocess.

```bash
python -m fpt_mcp.server
```

### HTTP (inter-service, scripts, AMIs)

Runs on a network port so Maya, Flame, scripts, and the AMI console can connect via TCP.

```bash
python -m fpt_mcp.server --http                # port 8090 (default)
python -m fpt_mcp.server --http --port 9000    # custom port
```

## Client configurations

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "fpt-mcp": {
      "command": "python",
      "args": ["-m", "fpt_mcp.server"],
      "cwd": "/path/to/fpt-mcp/src",
      "env": {
        "SHOTGRID_URL": "https://yoursite.shotgrid.autodesk.com",
        "SHOTGRID_SCRIPT_NAME": "your_script_name",
        "SHOTGRID_SCRIPT_KEY": "your_key"
      }
    }
  }
}
```

### Claude Code (terminal, natural language with all MCPs)

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "fpt-mcp": {
      "command": "python",
      "args": ["-m", "fpt_mcp.server"],
      "cwd": "~/Claude_projects/fpt-mcp/src",
      "env": {
        "SHOTGRID_URL": "https://yoursite.shotgrid.autodesk.com",
        "SHOTGRID_SCRIPT_NAME": "your_script_name",
        "SHOTGRID_SCRIPT_KEY": "your_key"
      }
    },
    "maya-mcp": {
      "command": "python",
      "args": ["server.py"],
      "cwd": "~/Claude_projects/maya-mcp-project/core"
    }
  }
}
```

This gives you natural language access to all MCP servers simultaneously from any terminal.

## AMI console (ShotGrid Action Menu Items)

Interactive web-based chat that connects to the HTTP server. Can be launched from ShotGrid as an Action Menu Item or opened directly in a browser.

### Start

```bash
# Terminal 1: MCP HTTP server
python -m fpt_mcp.server --http

# Terminal 2: AMI console
python -m fpt_mcp.ami.handler
```

### Access

- Direct: `http://localhost:8091/console`
- From ShotGrid AMI: `http://YOUR_IP:8091/ami`

When launched from a ShotGrid AMI, entity context (asset, shot, project) is passed automatically via query params but is not required — the console works as a free-form chat regardless.

### ShotGrid AMI setup

Admin → Action Menu Items → Add:
- **Title**: FPT Console
- **URL**: `http://YOUR_IP:8091/ami`
- **Entity types**: Asset, Shot, Sequence (or any)

## Autostart with launchd (macOS)

Install the server as a system service so it starts on login and restarts if it crashes:

```bash
cp com.abrahamadsk.fpt-mcp.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.abrahamadsk.fpt-mcp.plist
```

Manage:
- `launchctl stop com.abrahamadsk.fpt-mcp` — stop
- `launchctl start com.abrahamadsk.fpt-mcp` — start
- `launchctl unload ~/Library/LaunchAgents/com.abrahamadsk.fpt-mcp.plist` — uninstall

Logs: `/tmp/fpt-mcp.log` and `/tmp/fpt-mcp.err`

## Pipeline flows

1. **Assets → 3D**: `fpt_get_asset_image` → Hunyuan3D via maya-mcp → `fpt_create_published_file` (OBJ + Texture)
2. **Layout**: `fpt_create_sequence` → assemble in Maya → `fpt_create_published_file` (Maya Scene)
3. **Shots**: `fpt_create_shot` → camera per shot in Maya → publish Maya Scene per shot
4. **Render → Comp**: `maya_batch_render` → `fpt_create_published_file` (EXR) → load in Flame via flame-mcp

## Requirements

- Python >= 3.10
- `shotgun_api3` (ShotGrid Python API)
- `mcp[cli]` (MCP Python SDK with FastMCP)
- `pydantic` >= 2.0
- `python-dotenv`
- `httpx`
