# fpt-mcp

MCP server for **Autodesk Flow Production Tracking** (formerly ShotGrid).
Part of a VFX pipeline ecosystem alongside maya-mcp and flame-mcp.

```
Claude Desktop / Claude Code / Terminal
├── maya-mcp    → 3D / Arnold render
├── flame-mcp   → compositing
└── fpt-mcp     → production tracking (this repo)
        ├── stdio         → Claude Desktop / Claude Code
        ├── HTTP          → Maya, Flame, scripts, inter-service
        └── Qt console    → native chat app via fpt-mcp:// protocol handler
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

Or use the automated setup (creates venv + launchd service + Qt console app on macOS):

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

### HTTP (inter-service, scripts)

Runs on a network port so Maya, Flame, and scripts can connect via TCP.

```bash
python -m fpt_mcp.server --http                # port 8090 (default)
python -m fpt_mcp.server --http --port 9000    # custom port
```

## Qt Console (native chat app)

Native PySide6 chat window that routes messages through Claude Code CLI. Replaces the browser-based AMI console with a proper desktop app.

Features:
- Markdown rendering (bold, italic, code, headings, lists)
- Dark theme matching ShotGrid aesthetic
- Protocol handler (`fpt-mcp://`) for direct launch from ShotGrid AMIs
- ShotGrid entity context passed automatically via URL params
- No HTTP server dependency — launches as a standalone app

### Launch

```bash
# Direct
fpt-console

# With entity context
fpt-console --entity-type Shot --entity-id 456 --project-id 123

# Via protocol handler (from ShotGrid AMI or terminal)
open "fpt-mcp://chat?entity_type=Asset&selected_ids=123&project_id=456"
```

### ShotGrid AMI setup

Admin → Action Menu Items → Add:
- **Title**: FPT Console
- **Entity types**: Asset, Shot, Sequence, Version, Task (or any)
- **Light Payload**: Yes
- **URL**: `fpt-mcp://chat?entity_type={entity_type}&selected_ids={selected_ids}&project_id={project_id}&project_name={project_name}&user_login={user_login}`

When launched from an AMI, the entity context is displayed in the header badge and included in every message sent to Claude.

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

## Autostart with launchd (macOS)

The `setup_venv.sh` script automatically:
1. Creates the venv and installs dependencies
2. Generates and installs the MCP server launchd plist
3. Builds the Qt console .app bundle with protocol handler registration

Run it once:

```bash
./setup_venv.sh
```

Manage the MCP server:
- `launchctl stop com.fpt-mcp.server` — stop
- `launchctl start com.fpt-mcp.server` — start
- `launchctl unload ~/Library/LaunchAgents/com.fpt-mcp.server.plist` — uninstall

Logs: `/tmp/fpt-mcp.log` and `/tmp/fpt-mcp.err`

## Architecture

```
ShotGrid AMI click
    → fpt-mcp://chat?entity_type=Shot&selected_ids=123
    → macOS opens FPT-MCP Console.app (protocol handler)
    → Qt chat window with entity context
    → User types natural language
    → Claude Code CLI (claude -p "message")
    → Claude calls fpt-mcp tools via MCP (stdio)
    → ShotGrid API response
    → Formatted in Qt chat window
```

## Requirements

- Python >= 3.10
- macOS (for protocol handler; Qt console also works on Linux/Windows without protocol handler)
- `shotgun_api3` (ShotGrid Python API)
- `mcp[cli]` (MCP Python SDK with FastMCP)
- `pydantic` >= 2.0
- `PySide6` >= 6.6 (Qt for Python)
- `python-dotenv`
- `httpx`
- Claude Code CLI (`npm install -g @anthropic-ai/claude-code`)
