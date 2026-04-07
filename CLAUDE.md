# fpt-mcp Project Context for Claude

Reference document that persists across sessions. Update when architecture or workflows change.

---

## 1. General Architecture

**fpt-mcp** is an MCP server for Autodesk Flow Production Tracking (ShotGrid). Works standalone or alongside other MCP servers for cross-tool orchestration.

```
Claude Desktop / Claude Code / Terminal
├── maya-mcp    → 3D modeling, rendering, Vision3D GPU
├── flame-mcp   → compositing
└── fpt-mcp     → production tracking (ShotGrid)
        ├── stdio           → Claude Desktop / Claude Code
        ├── HTTP            → Maya, Flame, scripts, inter-service
        └── Qt console      → native chat app via fpt-mcp:// protocol handler
```

### MCP Server: fpt-mcp

**ShotGrid API tools** (unrestricted access to any entity):
- `sg_find` — query entities with filters and fields
- `sg_create` — create entities (project auto-linked)
- `sg_update` — update any field
- `sg_delete` — soft-delete (retire) entities
- `sg_schema` — inspect available fields
- `sg_upload` — upload files (thumbnail, movie, attachment)
- `sg_download` — download attachments
- `sg_batch` — transactional batch operations (all-or-nothing)
- `sg_text_search` — full-text search across all text fields of multiple entity types
- `sg_summarize` — server-side aggregation (count, sum, avg, min, max) with grouping
- `sg_revive` — restore soft-deleted (retired) entities
- `sg_note_thread` — read full Note reply threads with all linked entities
- `sg_activity` — read the activity stream for any entity

**Toolkit tools**:
- `tk_resolve_path` — resolve publish paths from the project's real PipelineConfiguration
- `tk_publish` — publish file: resolve path, copy file, find/create PublishedFileType, link Task, register PublishedFile in ShotGrid

**RAG tools** (Retrieval-Augmented Generation):
- `search_sg_docs` — hybrid search (ChromaDB semantic + BM25 lexical + HyDE + RRF fusion) across all 3 ShotGrid API docs. **MANDATORY** before complex or unknown queries.
- `learn_pattern` — persist validated patterns in the knowledge base. Model trust gates: only Sonnet/Opus write directly; other models stage candidates.
- `session_stats` — session statistics: tokens used, tokens saved by RAG, learned patterns, safety blocks.

**Safety module** (`safety.py`):
- 12+ regex patterns detecting dangerous operations before execution
- Integrated into sg_find and sg_delete (highest-risk tools)
- Detects: bulk delete, empty filters without limit, path traversal, schema modification, entity format errors, invalid filter operators

### RAG Technologies

| Component | Technology | Purpose |
|---|---|---|
| Vector DB | ChromaDB (persistent) | Semantic similarity search (cosine) |
| Embeddings | BAAI/bge-large-en-v1.5 (~570 MB) | Document and query encoding |
| Lexical search | rank_bm25 (BM25Okapi) | Exact match for method names and operators |
| Query expansion | Adaptive HyDE | Expands short queries with API-specific code templates |
| Rank fusion | RRF (k=60) | Combines semantic + lexical rankings without calibration |
| Token tracking | Built into _stats | Measures tokens used/saved per session |
| Self-learning | learn_pattern + model gates | Accumulates validated patterns across sessions |
| Cache | In-session dict (A12) | Avoids repeated ChromaDB lookups |

### Indexed corpus (3 collections, ~311 chunks)

- `docs/SG_API.md` — shotgun_api3 Python SDK: methods, filters, operators, anti-patterns
- `docs/TK_API.md` — Toolkit sgtk: templates, tokens, PipelineConfiguration, environments
- `docs/REST_API.md` — REST API: comparative reference to avoid confusion with the Python SDK

---

## 2. Toolkit — Dynamic Path Resolution (tk_config.py)

**File**: `src/fpt_mcp/tk_config.py`

### Two modes of operation

**Mode 1 — Auto-discovery** (projects with PipelineConfiguration):
- Queries the `PipelineConfiguration` entity in ShotGrid
- Reads `roots.yml` → gets the `project_root` (local path of the primary storage)
- Reads `templates.yml` → loads all path templates from the project
- Supports local configs and `dev` type distributed configs
- Entry point: `discover_config(project_id, sg_find_func)`

**Mode 2 — Explicit path** (projects without PipelineConfiguration):
- If no `PipelineConfiguration` exists or cannot be resolved, `discover_or_fallback()` returns `None`
- `tk_publish` then requires the user to provide an explicit `publish_path`
- The path is stored in the PublishedFile's `path` field
- If Local File Storage is enabled in ShotGrid, the file becomes browsable from the web UI

**Recommended entry point**: `discover_or_fallback()` — tries Mode 1, returns None for Mode 2.

### Key classes and functions

- **`TkConfig`**: Main class. Stores project_root, parsed templates, resolved aliases.
  - `resolve_path(template_name, fields)` → full filesystem path
  - `next_version(template_name, fields)` → next version number by scanning filesystem
  - `get_template(name)` → template string with aliases expanded
  - `list_templates(pattern)` → list filtered templates
- **`discover_or_fallback()`**: Main entry point, caches by project_id

### Publish pipeline (tk_publish)

```
Mode 1 (with PipelineConfiguration):
1. discover_or_fallback → TkConfig
2. Match template by convention (entity type + step + file extension)
3. Build template fields from SG entity context
4. tk_config.next_version → auto-increment by scanning filesystem
5. tk_config.resolve_path → full path
6. shutil.copy2 → copy source file if local_path provided
7. sg_find_one("PublishedFileType") → find or create if missing
8. sg_find_one("Task") → link with pipeline step task
9. sg_create("PublishedFile") → register in ShotGrid

Mode 2 (explicit path, no PipelineConfiguration):
1. User provides publish_path directly
2. shutil.copy2 → copy source file if local_path provided
3. sg_find_one("PublishedFileType") → find or create
4. sg_create("PublishedFile") → register with explicit path
```

### Distributed configs (TODO phase 2)

Currently only `dev` descriptor type is supported. Pending:
- `app_store` — resolve from bundle cache: `~/Library/Caches/Shotgun/<site>/bundle_cache/`
- `git` — resolve from bundle cache with different format

---

## 3. Qt Console (chat_window.py + claude_worker.py)

Native graphical interface that runs Claude Code CLI as a subprocess with real-time progress streaming.

### Worker Architecture

**File**: `src/fpt_mcp/qt/claude_worker.py`

- **QThread worker**: runs `claude -p "prompt" --output-format stream-json --append-system-prompt`
- **SYSTEM_PROMPT**: defines the complete 3D creation workflow (must read before modifying)
- **_TOOL_LABELS**: dictionary mapping MCP tool names → human-readable labels

### Progress Streaming

- **Text delta events**: parsed line by line from the JSON stream
- **_text_buffer**: buffer that accumulates partial text until `\n` is found
- **_progress_lines**: list accumulating progress lines in the current session (last 12 visible)
- **"Thinking" bubble**: accumulates lines instead of replacing them, providing process visibility

**File**: `src/fpt_mcp/qt/chat_window.py`

---

## 4. SYSTEM_PROMPT (Must read before modifying)

Location: `src/fpt_mcp/qt/claude_worker.py` line 40

The system prompt defines the complete workflow for 3D creation. Structure:

### Step 1: Check Vision3D
```
Call vision3d_health() BEFORE offering options
- If available=true → offer both options
- If available=false → inform and offer Maya only
```

### Steps 2-4: Identify entity, search references, present options
```
- sg_find to fetch ShotGrid context
- sg_find in parallel on Versions (image, sg_uploaded_movie), PublishedFiles
- PRESENT EVERYTHING IN A SINGLE RESPONSE with mandatory quality block
```

### Quality Block (MANDATORY — always show)
```
AI Quality — Vision3D server (model, octree, steps and faces):
 • low    — turbo model, octree 256, 10 steps, 10k faces  (~1 min)
 • medium — turbo model, octree 384, 20 steps, 50k faces  (~2 min) ← default
 • high   — full model,  octree 384, 30 steps, 150k faces (~8 min)
 • ultra  — full model,  octree 512, 50 steps, no limit    (~12 min)
```

**RULES**:
- Do NOT summarize or simplify the quality block
- Always use "Vision3D AI Server" or "Vision3D" (NOT "generative AI")
- The user needs to see the complete technical parameters

### Step 5: Execute granular Vision3D flow

**Image-to-3D**:
1. `sg_download` → download reference image
2. `shape_generate_remote(image_path=..., preset='high')` → returns job_id
3. `vision3d_poll(job_id=...)` → REPEAT while status='running', show new_log_lines
4. `vision3d_download(job_id=..., output_subdir=...)` → download files
5. `maya_execute_python` → import into Maya

**Text-to-3D** (full pipeline with texture):
1. `shape_generate_text(text_prompt=..., preset='medium')` → returns job_id
2. `vision3d_poll(job_id=...)` → repeat until completed (3 phases: text→image, shape, texture)
3. `vision3d_download(job_id=..., output_subdir=..., files=['textured.glb', 'mesh.glb', 'mesh_uv.obj', 'texture_baked.png'])`
4. `maya_execute_python` → import into Maya

**Direct Maya modeling**: `maya_create_primitive` + `maya_transform` + `maya_assign_material`

### Step 6: Post-creation
```
Offer maya_save_scene and tk_publish
```

### General rules
- NEVER repeat a question already answered in history
- ALWAYS use MCP tools, NEVER tell the user "do it manually"
- If Maya doesn't respond → `maya_launch`
- If Vision3D doesn't respond → `vision3d_health()` for diagnostics
- Text-to-3D: translate prompt to English
- Be concise, execute don't explain

---

## 5. Conversation History

The system prompt requires passing history as context for multi-turn:

```
IMPORTANT: There may be a CONVERSATION HISTORY before the current message.
Read it carefully — if the user already chose a reference or a method,
DO NOT ask again. Continue from where the conversation left off.
```

**Implementation**:
- `ClaudeWorker.__init__` receives `history: list | None = None`
- History is passed to the prompt so Claude can contextualize

---

## 6. Required Permissions

In `~/.claude/settings.json`, enable all these tools:

**maya-mcp**:
- `mcp__maya-mcp__vision3d_health`
- `mcp__maya-mcp__shape_generate_remote`
- `mcp__maya-mcp__shape_generate_text`
- `mcp__maya-mcp__texture_mesh_remote`
- `mcp__maya-mcp__vision3d_poll`
- `mcp__maya-mcp__vision3d_download`
- All maya_* tools (maya_launch, maya_ping, maya_create_primitive, maya_assign_material, maya_transform, maya_list_scene, maya_delete, maya_execute_python, maya_new_scene, maya_save_scene, maya_create_light, maya_create_camera)

**fpt-mcp**:
- All sg_* tools (sg_find, sg_create, sg_update, sg_delete, sg_schema, sg_upload, sg_download, sg_batch, sg_text_search, sg_summarize, sg_revive, sg_note_thread, sg_activity)
- All tk_* tools (tk_resolve_path, tk_publish)
- All RAG tools (search_sg_docs, learn_pattern, session_stats)

---

## 7. Known Issues / History

### Resolved
- **"Thinking..." without real progress** → Fixed with text_delta streaming + line accumulation in _progress_lines
- **System prompt oversimplified quality options** → Fixed with "MANDATORY" block showing full parameters
- **paths.py incompatible with tk-config-default2** → Replaced by `tk_config.py` with dynamic PipelineConfiguration discovery
- **tk_resolve_path crash: `next_version_number() takes 1 positional argument but 4 were given`** → Caused by old paths.py. Resolved with new tk_config.py
- **PublishedFileType not created for new types** → tk_publish now does automatic find-or-create
- **Task not linked to PublishedFile** → tk_publish now searches Task by entity + step automatically
- **File not copied to publish path** → tk_publish now does shutil.copy2 if local_path is provided
- **Pipeline-specific templates in core code** → Removed DERIVED_TEMPLATES and PUBLISH_TYPE_MAP. tk_config.py is now generic.
- **Mode 2 fabricated directory structure** → Replaced with explicit publish_path from user

### Pending
- **Maya Command Port sometimes unresponsive from console** → Consider retry logic or longer timeout
- **Distributed config: app_store and git descriptors** → Only dev type supported currently

---

## 8. Relationship with Other Projects

All three repos are in `~/Claude_projects/` on the local Mac:

- **maya-mcp**: MCP server used by the console for Maya + Vision3D
  - Repo: `~/Claude_projects/maya-mcp/`
  - Contains tools for maya_launch, maya_create_primitive, maya_execute_python, etc.
  - Internally calls vision3d (remote GPU server) via HTTP REST (port 8000)
  - Includes `vision3d_health` to check availability before offering options

- **vision3d**: remote GPU server accessible via maya-mcp
  - Repo: `~/Claude_projects/vision3d/` (Mac) / `/home/flame/ai-studio/vision3d/` (glorfindel)
  - Handles shape_generate_remote, shape_generate_text, texture_mesh_remote
  - Text-to-3D: 3-phase pipeline (HunyuanDiT → rembg → shape → paint → textured.glb)
  - Returns job_id for polling

- **fpt-mcp**: this repo (ShotGrid + Toolkit + Qt console)
  - 13 ShotGrid API tools (CRUD, schema, media, batch, search, summarize, revive, notes, activity)
  - 2 Toolkit tools (path resolution, publish pipeline) with dynamic config discovery
  - 3 RAG tools (search_sg_docs, learn_pattern, session_stats)
  - Native Qt console running Claude Code CLI

### Typical cross-MCP flow (full pipeline)
```
1. User → Qt Console (fpt-mcp) → Claude Code CLI
2. sg_find → search Asset/Shot and references in ShotGrid
3. sg_download → download reference image
4. shape_generate_remote (maya-mcp) → start 3D generation in Vision3D
5. vision3d_poll (maya-mcp) → monitor progress
6. vision3d_download (maya-mcp) → download results (GLB, OBJ, texture)
7. maya_execute_python (maya-mcp) → import mesh into Maya, normalize
8. maya_save_scene (maya-mcp) → save Maya scene
9. tk_publish (fpt-mcp) → resolve path + copy file + register PublishedFile
```

---

## 9. Development Notes

### Reinstallation after changes

After modifying `claude_worker.py` or `chat_window.py`:
```bash
cd /path/to/fpt-mcp
pip install -e .
```

### User environment (Abraham)

- Uses ShotGrid for VFX pipeline
- Works on local Mac with glorfindel (remote server for GPU/Vision3D)
- **RULE**: NEVER mix Mac and glorfindel commands in the same code block

---

## 10. Timeout and Limits

- **TIMEOUT_SECONDS**: 900 seconds (15 min) for shape generation which can take ~15 minutes
- **Max visible progress lines**: 12 lines in the "thinking" bubble

---

## 11. Change Checklist

Before committing changes in this project:

- [ ] Does it affect the SYSTEM_PROMPT? → Update this document (section 4)
- [ ] Change in _TOOL_LABELS? → Document here (section 3)
- [ ] Change in streaming/progress logic? → Describe in section 3
- [ ] Change in tk_config.py or templates? → Update section 2
- [ ] New tool or integration? → Mention in sections 1 and 8
- [ ] Reinstall: `cd fpt-mcp && pip install -e .`
- [ ] Test with ConsoleKit if Qt change

---

## 12. LLM Backend & Model Selection

fpt-mcp supports multiple LLM backends via the model selector in the Qt Console header.

### Recommended local model: Qwen3.5 9B (`qwen3.5-mcp`)
- **Tool calling**: 97.5% accuracy (1st of 13 models, eval J.D. Hodges)
- **Context window**: 262K tokens
- **Memory**: 6.6 GB (Q4_K_M)
- **Modelfile**: `qwen3.5-mcp` is a custom Modelfile derived from `qwen3.5:9b` with
  `num_ctx 8192`, `temperature 0.7`, `top_p 0.8`, `top_k 20`.
  Available on glorfindel and Mac M5 Pro.
- **Mac 24GB fallback**: `qwen3.5:4b` (direct, no custom Modelfile)
- **Ollama API note**: requires `"think": false` in each request to disable thinking mode.

### Available backends
| Backend | Label in combo | URL source | Notes |
|---|---|---|---|
| `anthropic` | Claude Sonnet/Opus | Anthropic API | Default, needs internet + API key |
| `ollama` | 🖥 models | `config.json → ollama_url` | glorfindel RTX 3090, LAN |
| `ollama_mac` | 🍎 models | `config.json → ollama_mac_url` | Mac-local, offline |

### Backend switching
The Qt Console passes `--model` and env vars (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`,
`ANTHROPIC_API_KEY`) to the Claude Code CLI subprocess. For Ollama backends, the Anthropic
SDK is redirected to the Ollama Messages-compatible endpoint (Ollama v0.14+).

### Write-allowed models (RAG trust gates)
Only Claude models can write patterns via `learn_pattern`. Local models (Ollama) are
read-only — they can search docs but cannot persist new patterns. Configured via
`write_allowed_models` in `config.json` (default: `["claude-opus", "claude-sonnet"]`).

### Prerequisites for local models
```bash
# Install Ollama (macOS)
brew install ollama
brew services start ollama

# Pull the model
ollama pull qwen3.5:9b
# On Mac 24GB (fallback):
ollama pull qwen3.5:4b
```

### Configuration
Copy `src/fpt_mcp/config.example.json` to `src/fpt_mcp/config.json` and adjust URLs.

### Full LLM strategy
See `MODEL_STRATEGY.md` in the ecosystem root for hardware configs, VRAM management,
update procedures, and architecture decisions.

---

**Last updated**: 2026-04-07
**Author**: Claude Agent
**Project**: fpt-mcp (Autodesk Flow Production Tracking + Qt Console)

---

## 13. MANDATORY: Update install.sh on tool changes

**RULE — NON-NEGOTIABLE:**
Whenever a tool is added, removed, or renamed in `src/fpt_mcp/server.py`:
1. Update the tools list in `install.sh` (Step 6 — Pre-approve MCP tools)
2. The tool name format is `mcp__fpt-mcp__<function_name>`
3. Run `bash -n install.sh` to verify syntax
4. Commit install.sh together with the server.py change — never separately

Forgetting this step means users get permission prompts on first use of the new tool.
