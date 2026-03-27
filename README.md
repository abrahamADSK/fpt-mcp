# fpt-mcp

MCP server for **Autodesk Flow Production Tracking** (formerly ShotGrid).
Part of the VFX pipeline ecosystem alongside [maya-mcp](https://github.com/abrahamADSK/maya-mcp) and [flame-mcp](https://github.com/abrahamADSK/flame-mcp).

```
Claude Desktop
├── maya-mcp    → 3D / Arnold render
├── flame-mcp   → compositing
└── fpt-mcp     → production tracking (this repo)
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

## Setup

### 1. Install

```bash
cd fpt-mcp
pip install -e .
```

### 2. Configure

Copy `.env.example` → `.env` and fill in your credentials:

```
SHOTGRID_URL=https://yoursite.shotgrid.autodesk.com
SHOTGRID_SCRIPT_NAME=your_script_name
SHOTGRID_SCRIPT_KEY=your_key
SHOTGRID_PROJECT_ID=123
```

### 3. Claude Desktop config

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "fpt-mcp": {
      "command": "python",
      "args": ["-m", "fpt_mcp.server"],
      "cwd": "/path/to/fpt-mcp/src",
      "env": {
        "SHOTGRID_URL": "https://ableviadsk.shotgrid.autodesk.com",
        "SHOTGRID_SCRIPT_NAME": "mcp_server",
        "SHOTGRID_SCRIPT_KEY": "your_key_here"
      }
    }
  }
}
```

Or if using the `.env` file, just point `cwd` to the repo root and omit the `env` block.

## Pipeline flows

1. **Assets → 3D**: `fpt_get_asset_image` → Hunyuan3D via maya-mcp → `fpt_create_published_file` (OBJ + Texture)
2. **Layout**: `fpt_create_sequence` → assemble in Maya → `fpt_create_published_file` (Maya Scene)
3. **Shots**: `fpt_create_shot` → camera per shot in Maya → publish Maya Scene per shot
4. **Render → Comp**: `maya_batch_render` → `fpt_create_published_file` (EXR) → load in Flame via flame-mcp

## Requirements

- Python ≥ 3.10
- `shotgun_api3` (ShotGrid Python API)
- `mcp[cli]` (MCP Python SDK with FastMCP)
- `pydantic` ≥ 2.0
- `python-dotenv`
- `httpx`
