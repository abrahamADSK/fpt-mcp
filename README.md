# fpt-mcp

> Connect Claude to Autodesk Flow Production Tracking (ShotGrid) for production management using the Model Context Protocol (MCP)

> [!WARNING]
> **Experimental project — use at your own risk.**
> This is an independent, unofficial experiment created with [Claude Code](https://claude.com/claude-code). It is **not** affiliated with, endorsed by, or officially supported by Autodesk in any way. The ShotGrid / Flow Production Tracking name and trademarks belong to Autodesk, Inc.
>
> Allowing AI-generated operations against a live ShotGrid instance carries real risks: **unintended data modifications, accidental entity deletion, incorrect publishes, or metadata corruption.** Always test against a dedicated sandbox project first. Never run this against production data without understanding the operations being performed. The author(s) accept no responsibility for data loss, corruption, or any other damage resulting from its use.

MCP server for **Autodesk Flow Production Tracking** (formerly ShotGrid).

Gives any MCP-compatible AI assistant (Claude Desktop, Claude Code, or any MCP client) full access to the ShotGrid API, Toolkit path resolution, and a RAG-powered knowledge engine that prevents common API hallucinations.

```
Claude Desktop / Claude Code / any MCP client
└── fpt-mcp
        ├── stdio         → Claude Desktop / Claude Code
        ├── HTTP          → scripts, inter-service calls
        └── Qt console    → native chat app via fpt-mcp:// protocol handler
```

## Tools (18)

General-purpose tools with no entity restrictions — works with any ShotGrid entity type and field.

### ShotGrid API (13 tools)

| Tool | Description |
|------|-------------|
| `sg_find` | Search any entity type with any filters and fields |
| `sg_create` | Create any entity with any fields (project auto-linked) |
| `sg_update` | Update any field on any entity |
| `sg_delete` | Soft-delete (retire) any entity |
| `sg_revive` | Restore a soft-deleted entity from trash |
| `sg_batch` | Transactional bulk operations — all succeed or all fail |
| `sg_schema` | Inspect available fields for any entity type |
| `sg_upload` | Upload file to any entity field (thumbnail, movie, attachment) |
| `sg_download` | Download attachment from any entity field |
| `sg_text_search` | Full-text search across multiple entity types simultaneously |
| `sg_summarize` | Server-side aggregation: count, sum, avg, min, max with grouping |
| `sg_note_thread` | Read the full reply thread of a Note with all nested replies |
| `sg_activity` | Read the activity stream (updates, status changes, notes) for an entity |

### Toolkit (2 tools)

| Tool | Description |
|------|-------------|
| `tk_resolve_path` | Resolve publish path from the project's real PipelineConfiguration |
| `tk_publish` | Publish file: resolve path, copy file, find/create PublishedFileType, link Task, register in ShotGrid |

### RAG — API Knowledge Engine (3 tools)

| Tool | Description |
|------|-------------|
| `search_sg_docs` | Hybrid search across ShotGrid API documentation (ChromaDB + BM25 + HyDE + RRF). Returns relevant API patterns, correct filter syntax, and entity format examples. **Called automatically before complex queries** |
| `learn_pattern` | Persist validated API patterns into the knowledge base. Model trust gates: Sonnet/Opus write directly, other models stage candidates for human review |
| `session_stats` | Token usage statistics: calls, tokens in/out, RAG savings, cache hits, efficiency ratio |

## Approach

Full ShotGrid API access via `shotgun_api3` with no entity restrictions.

### Toolkit path resolution

**Projects with Advanced Setup** (PipelineConfiguration exists):

The server queries the `PipelineConfiguration` entity from ShotGrid, reads the local `roots.yml` and `templates.yml`, and resolves publish paths using the project's real Toolkit config. This works with local configs, `dev` descriptors, and distributed configs. No hardcoded templates — paths come from the actual tk-config.

**Projects without Advanced Setup:**

If no `PipelineConfiguration` is found, `tk_publish` asks for an explicit publish path. The file is copied to the given location and registered as a PublishedFile in ShotGrid. If the project has a Local File Storage configured (ShotGrid → File Management → Local File Storage), the path will be resolvable from the ShotGrid web UI. Without Local Storage, the path is still stored in the PublishedFile `path` field and accessible to any script or loader that reads it.

The `tk_config.py` module reads whatever Toolkit config is installed — default, custom, or forked.

## RAG — Anti-hallucination Engine

fpt-mcp includes a hybrid Retrieval-Augmented Generation (RAG) system that provides Claude with verified ShotGrid API knowledge at query time, eliminating common hallucinations like invalid filter operators, incorrect entity reference formats, and wrong Toolkit template tokens.

### Architecture

```
User query → search_sg_docs tool
                ↓
        ┌───────┴───────┐
        │  HyDE Expander │ ← Adaptive: detects shotgun_api3 / Toolkit / REST
        └───────┬───────┘
                ↓
    ┌───────────┼───────────┐
    │           │           │
ChromaDB    BM25 Index   In-session
(semantic)  (lexical)     Cache
    │           │
    └─────┬─────┘
          ↓
    RRF Fusion (k=60)
          ↓
    Top-N chunks + relevance score
```

### Technology stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Vector DB | ChromaDB (persistent) | Semantic search with cosine similarity |
| Embeddings | BAAI/bge-large-en-v1.5 | Document and query encoding (~570 MB model) |
| Lexical search | BM25Okapi (rank_bm25) | Exact API method name matching |
| Query expansion | HyDE (adaptive) | Generates domain-specific hypothetical code before embedding |
| Rank fusion | RRF (k=60) | Combines semantic + BM25 rankings without score calibration |
| Safety | 12+ regex patterns | Detects dangerous operations before execution |
| Token tracking | Session stats | Measures tokens used vs saved by RAG, calculates efficiency |
| Self-learning | learn_pattern + model gates | Grows the knowledge base from validated patterns |
| Cache | In-session dict | Avoids redundant ChromaDB queries within a session |

### Knowledge corpus

The RAG indexes three ShotGrid API reference documents covering distinct domains:

| Document | Content | Size |
|----------|---------|------|
| `docs/SG_API.md` | shotgun_api3 Python SDK — methods, filter operators by field type, entity format rules, anti-patterns | ~7 KB |
| `docs/TK_API.md` | Toolkit (sgtk) — PipelineConfiguration discovery, template tokens (case-sensitive), descriptor types, path resolution | ~7 KB |
| `docs/REST_API.md` | REST API — comparison table vs Python SDK, filter syntax differences | ~2.5 KB |

### HyDE adaptive expansion

Unlike generic HyDE, fpt-mcp detects which API domain the query targets and generates a domain-specific hypothetical document:

- **Toolkit queries** (template, publish path, roots.yml) → generates `import sgtk` code skeleton
- **REST API queries** (oauth, bearer, endpoint) → generates `import requests` HTTP skeleton
- **Default** (most queries) → generates `from shotgun_api3 import Shotgun` skeleton

This produces embeddings closer to the relevant corpus section, improving retrieval precision.

### Dangerous pattern detection

The `safety.py` module scans tool parameters before execution and blocks or warns about dangerous operations:

- Bulk delete without specific IDs
- Unfiltered search with no limit (returns entire database)
- Entity reference format errors (int instead of `{type, id}` dict)
- Path traversal in publish paths (`../`)
- Schema modifications (field create/delete)
- PublishedFile deletion (breaks Toolkit references)
- Invalid filter operators (hallucinated by LLMs)
- Large batch operations (>100 entities)
- Incorrect template tokens

### Building the RAG index

After installing dependencies, build the ChromaDB index from the documentation corpus:

```bash
# From the project directory, with venv activated:
source .venv/bin/activate
python -m fpt_mcp.rag.build_index
```

This creates the persistent ChromaDB database and BM25 corpus.json. The first run downloads the BAAI/bge-large-en-v1.5 embedding model (~570 MB). The index only needs rebuilding when the documentation files in `docs/` change.

## Requirements

- Python >= 3.10
- macOS (for protocol handler; Qt console also works on Linux/Windows without protocol handler)
- `shotgun_api3` (ShotGrid Python API)
- `mcp[cli]` (MCP Python SDK with FastMCP)
- `pydantic` >= 2.0
- `PySide6` >= 6.6 (Qt for Python)
- `python-dotenv`
- `httpx`
- `pyyaml` (Toolkit config parsing)
- `chromadb` >= 0.5.0 (RAG vector database)
- `sentence-transformers` >= 2.2.0 (RAG embeddings — BAAI/bge-large-en-v1.5)
- `rank-bm25` >= 0.2.2 (RAG lexical search)
- Claude Code CLI (`npm install -g @anthropic-ai/claude-code`)

## Install

```bash
cd fpt-mcp
python3 -m venv .venv
source .venv/bin/activate
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
      "mcp__fpt-mcp__sg_batch",
      "mcp__fpt-mcp__sg_revive",
      "mcp__fpt-mcp__sg_text_search",
      "mcp__fpt-mcp__sg_summarize",
      "mcp__fpt-mcp__sg_note_thread",
      "mcp__fpt-mcp__sg_activity",
      "mcp__fpt-mcp__tk_resolve_path",
      "mcp__fpt-mcp__tk_publish",
      "mcp__fpt-mcp__search_sg_docs",
      "mcp__fpt-mcp__learn_pattern",
      "mcp__fpt-mcp__session_stats"
    ]
  }
}
```

> **Important:** `mcpServers` must be in `~/.claude.json`, NOT in `~/.claude/settings.json`. The `settings.json` file is only for permissions and other settings. If you put `mcpServers` in the wrong file, `claude mcp list` will not show the server.

The `permissions.allow` list auto-approves all fpt-mcp tools so Claude Code (and the Qt console, which uses Claude Code CLI internally) can call them without manual confirmation each time.

## Cross-MCP orchestration (optional)

fpt-mcp works standalone, but when combined with other MCP servers in the same Claude session, Claude can orchestrate multi-tool workflows automatically. For example, with a DCC MCP server configured alongside fpt-mcp, Claude can query ShotGrid for asset data, download references, and register publishes — all in a single conversation.

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

## Project Structure

```
fpt-mcp/
├── pyproject.toml                        # Package metadata and dependencies
├── install.sh                            # One-step installation script
├── setup_venv.sh                         # Virtual environment setup
├── com.abrahamadsk.fpt-mcp.plist         # launchd plist for MCP server daemon
├── com.abrahamadsk.fpt-ami.plist         # launchd plist for AMI URL handler
├── .env.example                          # Environment variables template
├── src/
│   └── fpt_mcp/
│       ├── __init__.py
│       ├── server.py                     # MCP server entry point (FastMCP)
│       ├── client.py                     # ShotGrid API client wrapper
│       ├── safety.py                     # Safety module — blocks dangerous write patterns
│       ├── paths.py                      # Path resolution utilities
│       ├── tk_config.py                  # Toolkit (ShotGrid Toolkit) config loader
│       ├── ami/
│       │   ├── __init__.py
│       │   ├── handler.py                # AMI URL protocol handler (fpt-mcp://)
│       │   └── console.html             # AMI console HTML template
│       ├── qt/
│       │   ├── __init__.py
│       │   ├── app.py                    # Qt application entry point
│       │   ├── chat_window.py            # Chat window widget
│       │   ├── claude_worker.py          # Async Claude subprocess worker thread
│       │   └── build_app_bundle.py       # macOS .app bundle builder script
│       ├── rag/
│       │   ├── __init__.py
│       │   ├── build_index.py            # RAG index builder (run to rebuild)
│       │   ├── config.py                 # RAG configuration (chunk size, model)
│       │   ├── corpus.json               # Parsed documentation corpus
│       │   ├── search.py                 # Semantic search over RAG index
│       │   └── index/                    # auto-generated (ChromaDB vector store)
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── assets.py                 # Asset management MCP tools
│       │   ├── publish.py                # Publish MCP tools (tk_publish)
│       │   ├── sequences.py              # Sequence MCP tools
│       │   ├── shots.py                  # Shot MCP tools
│       │   └── versions.py               # Version MCP tools
│       ├── docs/
│       │   ├── REST_API.md               # ShotGrid REST API documentation corpus
│       │   ├── SG_API.md                 # ShotGrid Python API documentation corpus
│       │   └── TK_API.md                 # Toolkit API documentation corpus
│       └── skills/
│           └── asset-creation/
│               └── SKILL.md              # Claude skill for asset creation workflows
└── tests/
    ├── conftest.py
    ├── fixtures/
    │   └── templates.yml                 # Mock Toolkit templates for tests
    ├── test_rag_search.py
    ├── test_safety.py
    ├── test_sg_operations.py
    ├── test_tk_publish.py
    └── test_toolkit_paths.py
```

## Troubleshooting

**Connection refused on ShotGrid API**
- Verify `SHOTGRID_URL` and `SHOTGRID_SCRIPT_KEY` in `.env`
- Check that the Script Application is active in ShotGrid Admin → Scripts
- Test connectivity: `curl -s https://YOUR_SITE.shotgrid.autodesk.com/api/v1`

**RAG index not found**
- Run `python -m fpt_mcp.rag.build_index` to rebuild
- Check that `docs/` directory contains the ShotGrid API documentation corpus

**Toolkit path resolution fails**
- Verify that a PipelineConfiguration entity exists for the project in ShotGrid
- Check `roots.yml` and `templates.yml` paths in the PipelineConfiguration's `descriptor` field
- For distributed configs, only `dev` descriptor type is currently supported

## Ecosystem

`fpt-mcp` is part of a four-component VFX pipeline. Each component has a defined role:

| Repo | Role |
|------|------|
| [flame-mcp](https://github.com/abrahamADSK/flame-mcp) | Controls Autodesk Flame for compositing, conform, and finishing |
| [maya-mcp](https://github.com/abrahamADSK/maya-mcp) | Controls Autodesk Maya for 3D modeling, animation, and rendering |
| [fpt-mcp](https://github.com/abrahamADSK/fpt-mcp) | Connects to Autodesk Flow Production Tracking (ShotGrid) for production tracking, asset management, and publishes |
| [vision3d](https://github.com/abrahamADSK/vision3d) | GPU inference server for AI-powered 3D generation — the remote backend for maya-mcp's image-to-3D and text-to-3D tools |

`fpt-mcp` is the production backbone of the pipeline. It provides asset metadata, task assignments, path resolution, and publish registration for the other tools. `maya-mcp` and `flame-mcp` both consume `fpt-mcp` data — Maya for asset context and publish targets, Flame for shot and sequence lookup. `vision3d` has no direct connection to `fpt-mcp`.

## License

[MIT](LICENSE)

