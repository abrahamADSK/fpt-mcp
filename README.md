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
- Light Payload support (fetches full context from EventLogEntry API)
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
- **URL**: `fpt-mcp://chat`

ShotGrid automatically appends entity context parameters (`entity_type`, `selected_ids`, `project_id`, `project_name`, `user_login`) to custom protocol URLs. Do not add `{placeholder}` tokens — they are only substituted for `http://` and `https://` URLs.

If **Light Payload** is enabled in the AMI configuration, ShotGrid sends only an `event_log_entry_id` instead of the full entity context. The Qt console detects this automatically and fetches the real entity context from the ShotGrid API via `EventLogEntry.meta.ami_payload`. This requires valid ShotGrid API credentials in `.env`.

After changing an AMI URL in ShotGrid, you may need to hard-refresh the browser (Cmd+Shift+R) to clear the cached AMI configuration.

When launched from an AMI, the entity context is displayed in the header badge and included in every message sent to Claude.

## Client configurations

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "fpt-mcp": {
      "command": "/path/to/fpt-mcp/.venv/bin/python",
      "args": ["-m", "fpt_mcp.server"],
      "cwd": "/path/to/fpt-mcp",
      "env": {
        "SHOTGRID_URL": "https://yoursite.shotgrid.autodesk.com",
        "SHOTGRID_SCRIPT_NAME": "your_script_name",
        "SHOTGRID_SCRIPT_KEY": "your_key",
        "SHOTGRID_PROJECT_ID": "123"
      }
    }
  }
}
```

The `cwd` field is required so the server can find the `.env` file and resolve relative paths correctly.

### Claude Code

Claude Code uses **two separate files** for MCP configuration:

**1. MCP server definitions** — `~/.claude.json` (note: file in home dir, not inside `~/.claude/`):

```bash
# Add the server via CLI (recommended):
claude mcp add fpt-mcp -s user -e SHOTGRID_URL=https://yoursite.shotgrid.autodesk.com -e SHOTGRID_SCRIPT_NAME=your_script_name -e SHOTGRID_SCRIPT_KEY=your_key -- /path/to/fpt-mcp/.venv/bin/python -m fpt_mcp.server

# Or edit ~/.claude.json manually:
```

```json
{
  "mcpServers": {
    "fpt-mcp": {
      "command": "/path/to/fpt-mcp/.venv/bin/python",
      "args": ["-m", "fpt_mcp.server"],
      "env": {
        "SHOTGRID_URL": "https://yoursite.shotgrid.autodesk.com",
        "SHOTGRID_SCRIPT_NAME": "your_script_name",
        "SHOTGRID_SCRIPT_KEY": "your_key"
      }
    }
  }
}
```

**2. Tool permissions** — `~/.claude/settings.json`:

```json
{
  "permissions": {
    "allow": [
      "mcp__fpt-mcp__sg_find",
      "mcp__fpt-mcp__sg_create",
      "mcp__fpt-mcp__sg_update",
      "mcp__fpt-mcp__sg_delete",
      "mcp__fpt-mcp__sg_schema",
      "mcp__fpt-mcp__sg_upload",
      "mcp__fpt-mcp__sg_download",
      "mcp__fpt-mcp__tk_resolve_path",
      "mcp__fpt-mcp__tk_publish"
    ]
  }
}
```

> **Important:** `mcpServers` must be in `~/.claude.json`, NOT in `~/.claude/settings.json`. The `settings.json` file is only for permissions and other settings. If you put `mcpServers` in the wrong file, `claude mcp list` will not show the server.

The `permissions.allow` list auto-approves all fpt-mcp tools so Claude Code (and the Qt console, which uses Claude Code CLI internally) can call them without manual confirmation each time.

## Cross-MCP pipeline (fpt-mcp + maya-mcp)

When both fpt-mcp and maya-mcp are configured in Claude Code or Claude Desktop, Claude can orchestrate cross-tool workflows in a single conversation. For example:

```
User: "Download the reference image for Asset #1478 and generate a 3D model in Maya"

Claude:
  1. sg_find → get Asset #1478 details and linked Version with thumbnail
  2. sg_download → download the reference image to local disk
  3. shape_generate_remote → send image to GPU, generate mesh.glb via Hunyuan3D-2 DiT
  4. texture_mesh_remote → paint texture on mesh.glb via Hunyuan3D-2 Paint
  5. maya_execute_python → import the textured mesh into the Maya scene
  6. sg_create → register a PublishedFile in ShotGrid with the new mesh path
```

To enable this, add both servers to your `~/.claude/settings.json` (see the maya-mcp README for its configuration) and include permissions for both in `permissions.allow`.

## Autostart with launchd (macOS)

The `setup_venv.sh` script automatically:
1. Creates the venv and installs dependencies
2. Generates and installs the MCP server launchd plist (HTTP mode on port 8090)
3. Builds the Qt console .app bundle with protocol handler registration
4. Registers the protocol handler with macOS Launch Services

Run it once:

```bash
./setup_venv.sh
```

Manage the MCP server:
- `launchctl stop com.fpt-mcp.server` — stop
- `launchctl start com.fpt-mcp.server` — start
- `launchctl unload ~/Library/LaunchAgents/com.fpt-mcp.server.plist` — uninstall

Logs: `/tmp/fpt-mcp.log` and `/tmp/fpt-mcp.err`
Qt console logs: `/tmp/fpt-console.log`

## Architecture

```
ShotGrid AMI click
    → fpt-mcp://chat  (macOS appends entity params automatically)
    → macOS opens FPT-MCP Console.app (protocol handler via Apple Events)
    → QFileOpenEvent delivers the URL to the Qt app
    → If Light Payload: fetch real context from EventLogEntry API
    → Qt chat window with entity context badge
    → User types natural language
    → Claude Code CLI (claude -p "message" --output-format text)
    → Claude calls fpt-mcp tools via MCP (stdio)
    → ShotGrid API response
    → Markdown rendered in Qt chat window
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
