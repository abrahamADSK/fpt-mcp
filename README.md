# fpt-mcp

MCP server for **Autodesk Flow Production Tracking** (formerly ShotGrid).
Part of a VFX pipeline ecosystem alongside maya-mcp and flame-mcp.

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

General-purpose tools with no entity restrictions — works with any ShotGrid entity type and field.

### ShotGrid API

| Tool | Description |
|------|-------------|
| `sg_find` | Search any entity type with any filters and fields |
| `sg_create` | Create any entity with any fields (project auto-linked) |
| `sg_update` | Update any field on any entity |
| `sg_delete` | Soft-delete (retire) any entity |
| `sg_schema` | Inspect available fields for any entity type |
| `sg_upload` | Upload file to any entity field (thumbnail, movie, attachment) |
| `sg_download` | Download attachment from any entity field |

### Toolkit

| Tool | Description |
|------|-------------|
| `tk_resolve_path` | Resolve publish path using tk-config-default2 conventions |
| `tk_publish` | Create PublishedFile with auto-versioned Toolkit-compatible path |

## Approach

Full ShotGrid API access via `shotgun_api3` with no entity restrictions. Publish paths follow `tk-config-default2` conventions so that Toolkit loaders (tk-multi-loader2) in Maya and Flame can pick them up natively.

## Install

```bash
cd fpt-mcp
pip install -e .
```

Or use the automated setup (creates venv + launchd services on macOS):

```bash
chmod +x setup_venv.sh
./setup_venv.sh
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

### Claude Code

Add to `~/.claude/settings.json`:

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

## AMI console (ShotGrid Action Menu Items)

Interactive web-based chat powered by Claude that connects to the HTTP server. Can be launched from ShotGrid as an Action Menu Item or opened directly in a browser.

### Start

```bash
# Terminal 1: MCP HTTP server
python -m fpt_mcp.server --http

# Terminal 2: AMI console
python -m fpt_mcp.ami.handler
```

### Access

- Direct: `http://localhost:8091/ami`
- From ShotGrid AMI: `http://YOUR_IP:8091/ami`

When launched from a ShotGrid AMI, entity context (asset, shot, project) is passed automatically via query params but is not required — the console works as a free-form chat regardless.

### ShotGrid AMI setup

Admin → Action Menu Items → Add:
- **Title**: FPT Console
- **URL**: `http://YOUR_IP:8091/ami`
- **Entity types**: Asset, Shot, Sequence (or any)

## Autostart with launchd (macOS)

The `setup_venv.sh` script automatically generates and installs launchd plists with paths resolved to your local install. Run it once:

```bash
./setup_venv.sh
```

Manage services:
- `launchctl stop com.fpt-mcp.server` — stop
- `launchctl start com.fpt-mcp.server` — start
- `launchctl unload ~/Library/LaunchAgents/com.fpt-mcp.server.plist` — uninstall

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
