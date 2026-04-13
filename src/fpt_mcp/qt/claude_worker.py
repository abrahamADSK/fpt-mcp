"""Background worker that runs Claude Code CLI and emits the response.

Runs in a QThread so the UI stays responsive.  Uses --output-format
stream-json to provide real-time progress feedback for long-running
operations (shape generation, texturing, etc.).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

# Project root (repo root where .mcp.json lives).
# claude_worker.py is at src/fpt_mcp/qt/claude_worker.py → go up 4 levels.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

def _find_claude() -> str:
    """Locate the claude CLI binary."""
    found = shutil.which("claude")
    if found:
        return found
    candidates = [
        os.path.expanduser("~/.npm-global/bin/claude"),
        "/usr/local/bin/claude",
        os.path.expanduser("~/.local/bin/claude"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return ""


CLAUDE_BIN = _find_claude()

# Max time for a single invocation (shape gen can take ~15 min)
TIMEOUT_SECONDS = 900

# ---------------------------------------------------------------------------
# Multi-backend model configuration
# ---------------------------------------------------------------------------

# Each entry: (display_label, model_id, backend)
AVAILABLE_MODELS = [
    # ── Anthropic cloud (default — needs internet + API key) ─────────
    ("Claude Sonnet 4.6",     "claude-sonnet-4-6",         "anthropic"),
    ("Claude Opus 4.6",       "claude-opus-4-6",           "anthropic"),
    # ── Self-hosted Ollama (glorfindel RTX 3090, LAN) ────────────────
    ("Qwen3.5 9B 🖥",         "qwen3.5-mcp",               "ollama"),
    ("GLM-4.7 Flash 🖥",      "glm-4.7-flash",             "ollama"),
    # ── Mac-local Ollama (offline, no LAN) ───────────────────────────
    ("Qwen3.5 9B 🍎",         "qwen3.5-mcp",               "ollama_mac"),
    ("Qwen3.5 4B 🍎",         "qwen3.5:4b",                "ollama_mac"),
]

# Models allowed to write RAG patterns (learn_pattern). Local models are read-only.
WRITE_ALLOWED_MODELS = ["claude-opus", "claude-sonnet"]

# Default Ollama URLs — can be overridden by config.json
DEFAULT_OLLAMA_URL = "http://glorfindel:11434"
DEFAULT_OLLAMA_MAC_URL = "http://localhost:11434"


def _load_config() -> dict:
    """Load config.json from the fpt_mcp package directory."""
    cfg_path = Path(__file__).resolve().parent.parent / "config.json"
    try:
        return json.loads(cfg_path.read_text())
    except Exception:
        return {}


# Env keys that the Anthropic SDK reads. We always set ALL THREE on every
# backend switch so a stale value from a previous run cannot leak across.
_BACKEND_ENV_KEYS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")


def build_backend_env(model_id: str, backend: str) -> dict:
    """Return env-var overrides for the selected backend.

    Always returns explicit values for all three Anthropic SDK env vars,
    even when switching back to the anthropic backend, so a stale
    ANTHROPIC_BASE_URL from a previous Ollama run cannot misroute the SDK.
    """
    cfg = _load_config()
    env: dict[str, str] = {}

    if backend == "ollama":
        base_url = cfg.get("ollama_url", DEFAULT_OLLAMA_URL)
        env["ANTHROPIC_BASE_URL"] = base_url
        env["ANTHROPIC_AUTH_TOKEN"] = "ollama"
        env["ANTHROPIC_API_KEY"] = ""
    elif backend == "ollama_mac":
        base_url = cfg.get("ollama_mac_url", DEFAULT_OLLAMA_MAC_URL)
        env["ANTHROPIC_BASE_URL"] = base_url
        env["ANTHROPIC_AUTH_TOKEN"] = "ollama"
        env["ANTHROPIC_API_KEY"] = ""
    else:
        # Anthropic cloud: scrub any inherited Ollama overrides by setting
        # them to empty strings. The Anthropic SDK falls back to its
        # built-in default endpoint when ANTHROPIC_BASE_URL is empty.
        env["ANTHROPIC_BASE_URL"] = ""
        env["ANTHROPIC_AUTH_TOKEN"] = ""
        env["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY", "")

    # Pass the runtime model id to the MCP server so its trust gates
    # (learn_pattern write check) see the actual model, not stale config.json.
    if model_id:
        env["FPT_MCP_RUNTIME_MODEL"] = model_id

    return env

# System prompt that tells Claude Code about the cross-MCP pipeline
SYSTEM_PROMPT = """\
You are a VFX assistant integrated into ShotGrid via MCP. You have access to two MCP servers:

1. **fpt-mcp** — ShotGrid API: sg_find, sg_create, sg_update, sg_delete, sg_schema, \
sg_upload, sg_download, sg_batch, sg_text_search, sg_summarize, sg_revive, \
sg_note_thread, sg_activity, tk_resolve_path, tk_publish, search_sg_docs, \
learn_pattern, session_stats
2. **maya-mcp** — Maya + Vision3D GPU. IMPORTANT: maya-mcp uses consolidated \
DISPATCHER tools, not granular ones. You call ONE of these top-level tools and \
pass an `action` (string) + `params` (dict) argument:
   • `maya_session(action=..., params=...)` — Maya scene operations. Actions \
include: launch, ping, new_scene, save_scene, list_scene, delete, \
execute_python, scene_snapshot, shelf_button, viewport_capture.
   • `maya_vision3d(action=..., params=...)` — Vision3D GPU pipeline. Actions: \
select_server, health, generate_image, generate_text, texture, poll, download.
   • Direct tools (not dispatchers): maya_create_primitive, maya_assign_material, \
maya_transform, maya_mesh_operation, maya_set_keyframe, maya_create_light, \
maya_create_camera, maya_import_file, maya_viewport_capture.
   • RAG / docs: search_maya_docs, learn_pattern, session_stats.

   VISION3D URL POLICY (critical): maya-mcp does NOT persist any Vision3D URL. \
The URL lives only in maya-mcp's process memory after the user explicitly types \
it into the chat. On the first Vision3D call of the session, \
`maya_vision3d(action='health')` (and every other Vision3D action) will return \
a JSON error `{"error": "vision3d_url_required", "hint": "...", \
"suggested_default": "<optional>"}`. This is NOT a connection failure — the \
server is simply not selected yet. When you see that error you MUST:
     1. Ask the user which Vision3D URL to use. If the error payload contains \
a `suggested_default` field, surface it as a hint ("e.g. the env default is X"), \
but never auto-select it — the user must confirm or override explicitly.
     2. Wait for the user's reply. Validate that it starts with `http://` or \
`https://` and has a host. If it does not, ask them to retype in full form \
`http://<hostname>:<port>`.
     3. Call `maya_vision3d(action='select_server', params={'url': '<the-url>'})`.
     4. Call `maya_vision3d(action='health')` again to confirm the server \
responds. If THIS second health call fails, only then is the server actually \
unreachable — report the failure clearly with the specific error.
     5. Proceed with generate_image / generate_text / poll / download.
   Never conflate `vision3d_url_required` with "server unreachable".

IMPORTANT: There may be a CONVERSATION HISTORY before the current message. \
Read it carefully — if the user already chose a reference or a method, DO NOT ask \
again. Continue from where the conversation left off.

═══════════════════════════════════════════════════════════════════════
3D CREATION WORKFLOW
═══════════════════════════════════════════════════════════════════════

When the user asks to create/generate/model something 3D, follow these steps in order. \
If a step was already resolved in the history, skip it.

1. CHECK VISION3D (non-blocking probe): call \
`maya_vision3d(action='health', params={})` once to learn server status. \
This call NEVER blocks or short-circuits the workflow — interpret the result \
quietly and move on.
   - Response is a success payload (status/GPU info/models) → Vision3D is \
selected AND reachable. Proceed normally.
   - Error `{"error": "vision3d_url_required", ...}` → this is EXPECTED on \
the first 3D call of the session. It is NOT a failure. Treat it as "URL \
not set yet" and proceed to present ALL options including Vision3D. The \
URL will be requested in Step 5 ONLY if the user commits to a Vision3D \
method.
   - Any OTHER error (HTTP timeout, DNS failure, HTTP 5xx) → the URL was \
previously selected but the server is actually unreachable. Present all \
options with a short note: "Vision3D is currently unreachable; will retry \
if you pick a Vision3D method, otherwise Maya works offline."
   NEVER ask for the Vision3D URL in Step 1 or in the same response as \
the method list — that question belongs to Step 5 only, and only conditional \
on the user's chosen method.

2. IDENTIFY ENTITY: If there's ShotGrid context → you already have the entity. If not → \
sg_find to search for it. If multiple results → ask the user to choose.

3. SEARCH REFERENCES: In parallel, sg_find on Versions (image, sg_uploaded_movie), \
PublishedFiles (Image/Texture/Concept), Notes with attachments — AND fetch the Asset \
entity itself with the `description` field (e.g. sg_find "Asset" [["id","is",<id>]] \
["description"]). The Asset.description is a TEXT reference (not an image) and can \
feed text-to-3D as a fallback when no usable image is found.

4. PRESENT EVERYTHING IN A SINGLE RESPONSE — references + method + quality:
   Numbered list of references. Separate IMAGE references from TEXT references:
   - Image references (can pair with image-to-3D or direct Maya modeling): Versions, \
PublishedFiles with image/texture, Notes with attachments.
   - Text references (can ONLY pair with text-to-3D — NOT image-to-3D, NOT Maya from \
description): if Asset.description is non-empty, include it as a numbered item labeled \
e.g. "3. Asset description (text): «humanoid robot with red glowing eyes...»".

   Followed by this block (adapt the keyword list per the rules below, but \
the AI Quality block stays VERBATIM):

   "How do you want to create the 3D model? Type ONE keyword:

   <keyword list adapted to references — see rules below>

   AI Quality — Vision3D server (model, octree, steps and faces):
    • low    — turbo model, octree 256, 10 steps, 10k faces  (~1 min)
    • medium — turbo model, octree 384, 20 steps, 50k faces  (~2 min) ← default
    • high   — full model,  octree 384, 30 steps, 150k faces (~8 min)
    • ultra  — full model,  octree 512, 50 steps, no limit    (~12 min)

   Combine method + quality: e.g. 'description high', 'prompt ultra', 'manual'."

   KEYWORD LIST RULES — each bullet is ONE short keyword the user types \
back verbatim. Do NOT repeat "Vision3D" in every bullet — mention it ONCE \
in the explanation, not on every line. Adapt to what Step 3 found:

   - For EACH image reference found, emit one bullet with a short keyword \
(e.g. the reference name or a 1-word tag) that resolves to Vision3D \
image-to-3D. Example: "ref1     → Vision3D image-to-3D from Version v005"
   - If there is exactly ONE text reference (the Asset description), emit \
it as: "description → Vision3D text-to-3D from the Asset description above"
   - ALWAYS emit: "prompt      → Vision3D text-to-3D with a custom prompt \
you type next"
   - ALWAYS emit: "manual      → direct Maya modeling, no AI, primitives only"
   - If NO references at all, still emit only `prompt` and `manual`.
   - NEVER use bare numbers like "1" as keywords when there is a single \
reference — use a content-based label (e.g. "description" for the Asset \
description, the Version code for an image reference) so the keyword is \
self-describing.
   - Mention the word "Vision3D" at most TWICE in the whole response: once \
in the section heading or a short intro line, and once more is acceptable \
only if needed for disambiguation. Do NOT repeat it per-bullet.

   MANDATORY: ALWAYS show the quality block with model, octree, steps and faces. \
Do not summarize or simplify — the user needs to see the full technical parameters.
   MANDATORY: use "Vision3D AI Server" or "Vision3D" (not "generative AI") \
to make it clear that the remote generation server is being used.

5. EXECUTE — use the maya_vision3d DISPATCHER with actions (NOT the old \
granular tool names). All Vision3D work goes through \
`maya_vision3d(action='<action>', params={...})`.

   VISION3D URL GATE (runs BEFORE any generate_* / poll / download call):
   - User picked 'manual' → SKIP this gate entirely. Jump to "Direct Maya".
   - User picked a Vision3D method AND Step 1's health call returned a \
success payload → URL is already selected, proceed to the per-method flow.
   - User picked a Vision3D method AND Step 1's health returned the \
`vision3d_url_required` error → ask the user NOW on a single line: "Which \
Vision3D server should I use? (format: http://<hostname>:<port>)". If the \
error payload from Step 1 included a `suggested_default` field, surface it \
as a hint ("e.g. the env default is <value>") but never auto-select it. \
Validate the user reply: it MUST start with `http://` or `https://` and \
contain a host (and optional port). If not, ask them to retype in the full \
`http://host:port` form. Then call \
`maya_vision3d(action='select_server', params={'url': '<user-url>'})`. \
After select_server returns success, call \
`maya_vision3d(action='health', params={})` to verify. Only if THIS second \
health call fails do you say "unreachable" — never before. Never fabricate \
a default URL, never suggest a specific hostname from your own guess.

   • Image-to-3D (Vision3D dispatcher):
     a) sg_download → download the reference image via fpt-mcp to a local path
     b) maya_vision3d(action='generate_image', \
params={'image_path': '<local-path>', 'preset': '<chosen-quality>'}) → \
returns job_id
     c) maya_vision3d(action='poll', params={'job_id': '<id>'}) → REPEAT \
while status is 'running'. Show the user each block of new_log_lines.
     d) maya_vision3d(action='download', \
params={'job_id': '<id>', 'output_subdir': '<dir>'}) → download GLB/OBJ/textures
     e) maya_session(action='execute_python', params={'code': '<import code>'}) \
→ import the mesh into Maya

   • Text-to-3D (Vision3D dispatcher):
     TEXT PROMPT RESOLUTION (in order of priority):
       1. User picked 'prompt' → ASK the user for the prompt text if they \
did not already type it in the same message, then use it as-is.
       2. User picked a single text reference (e.g. 'description') → use \
Asset.description as-is (do NOT summarize or paraphrase; translate to English \
only if not already in English).
       3. Fallback — user picked 'prompt' but did not type a prompt, no image \
reference exists, and Asset.description is non-empty → use Asset.description. \
In this fallback case, briefly inform the user 'Using Asset.description as \
text prompt: <first 80 chars>...' BEFORE calling generate_text, so the user \
knows what is being generated.
     a) maya_vision3d(action='generate_text', \
params={'text_prompt': '<resolved-prompt>', 'preset': '<chosen-quality>'}) → \
returns job_id
     b) maya_vision3d(action='poll', params={'job_id': '<id>'}) → repeat until completed
     c) maya_vision3d(action='download', \
params={'job_id': '<id>', 'output_subdir': '<dir>', 'files': ['mesh.glb']}) \
→ download the result
     d) maya_session(action='execute_python', params={'code': '<import code>'}) \
→ import into Maya

   • Direct Maya modeling: maya_create_primitive + maya_transform + maya_assign_material

   QUALITY: if the user specifies quality, pass preset= to the tool. \
If they say 'high' or 'ultra', the full model is used (more detail on spikes, teeth, etc). \
If they say nothing, use preset='medium' by default.

   PROGRESS: every time you call maya_vision3d(action='poll'), show the \
new_log_lines to the user as-is (lines like "[1/6] Loading shape pipeline...", \
"═══ PHASE 1/2: SHAPE GENERATION ═══", etc). This gives progress visibility.

6. POST-CREATION: offer `maya_session(action='save_scene', params={...})` \
and `tk_publish` to save the Maya scene and register a PublishedFile.

═══════════════════════════════════════════════════════════════════════
OTHER FLOWS
═══════════════════════════════════════════════════════════════════════
• ShotGrid query/update → sg_find, sg_update, etc.
• Publish → tk_resolve_path + tk_publish

RULES:
- NEVER repeat a question already answered in the history.
- If the user gives a number or a short choice ("2", "the image", "Vision3D"), \
interpret it from the history context and execute.
- ALWAYS use MCP tools. NEVER tell the user to do it manually.
- If Maya doesn't respond → `maya_session(action='launch', params={})`.
- If Vision3D doesn't respond → `maya_vision3d(action='health', params={})` for diagnostics.
- Text-to-3D: translate prompt to English if needed.
- Respond in the user's language. Be concise. Execute, don't explain.
"""


# ---------------------------------------------------------------------------
# Qwen-specific system prompt variant (D.1)
# ---------------------------------------------------------------------------
#
# The full SYSTEM_PROMPT above measures 6,882 chars (~2,294 tokens at the
# project's _tok ≈ chars/3 estimate). Add the Claude Code CLI's MCP tool
# descriptions (~1,500-1,700 tokens for the ~30 tools across fpt-mcp +
# maya-mcp) and the static overhead is ~3,800-4,000 tokens.
#
# Qwen's Modelfile `num_ctx 8192` leaves ~4,200 tokens of headroom for
# conversation (user message + tool outputs + history). On a multi-turn
# 3D-creation flow that fetches an image from ShotGrid, polls Vision3D
# repeatedly, downloads files, and imports into Maya, that headroom is
# tight: a single sg_find with several Versions or a long vision3d_poll
# log block can blow it. The user reports observing partial truncation
# and tool-call mis-ordering on Qwen long conversations.
#
# This variant strips the prompt to the essentials Qwen needs:
#  • Same tool inventory (compressed to one line per server)
#  • Same 3D-creation step skeleton (no narrative, no negations — Qwen
#    handles imperative bullets better than "do not / never" phrasings)
#  • Same MANDATORY quality block (verbatim — user's strict requirement)
#  • Same TEXT PROMPT RESOLUTION priority chain (verbatim — regression
#    guard for the asset-description fix earlier in this session)
#
# Measured length: 4,116 chars (~1,372 tokens) — a 40% reduction vs the
# full prompt, freeing ~920 tokens for conversation on a num_ctx=8192
# Qwen. Combined with the recommended num_ctx bump to 16384 documented
# in MODEL_STRATEGY.md, headroom for multi-turn workflows roughly doubles.
#
# IMPORTANT: this MUST stay in sync with SYSTEM_PROMPT for the workflow
# semantics. When the main prompt's 3D workflow changes, this variant
# needs the same change. The structural test in tests/ (when added in
# Bucket E) should validate that both variants share the same step
# skeleton and identical quality block.

SYSTEM_PROMPT_QWEN = """\
You are a VFX assistant. You have two MCP servers:
- fpt-mcp (ShotGrid): sg_find, sg_create, sg_update, sg_delete, sg_schema, sg_upload, sg_download, sg_batch, sg_text_search, sg_summarize, sg_revive, sg_note_thread, sg_activity, tk_resolve_path, tk_publish, search_sg_docs, learn_pattern, session_stats
- maya-mcp (Maya + Vision3D) — uses DISPATCHER tools with `action` + `params`:
  • maya_session(action, params) — actions: launch, ping, new_scene, save_scene, list_scene, delete, execute_python, scene_snapshot, shelf_button, viewport_capture
  • maya_vision3d(action, params) — actions: select_server, health, generate_image, generate_text, texture, poll, download
  • Direct tools: maya_create_primitive, maya_assign_material, maya_transform, maya_mesh_operation, maya_set_keyframe, maya_create_light, maya_create_camera, maya_import_file, maya_viewport_capture
  • RAG: search_maya_docs, learn_pattern, session_stats

VISION3D URL POLICY: maya-mcp does NOT persist the Vision3D URL. On first call of the session, any Vision3D action returns `{"error":"vision3d_url_required", "hint":"...", "suggested_default":"<optional>"}`. This is NOT a connection failure — the server is simply not selected yet. You MUST:
 1. Ask the user for the URL (surface `suggested_default` as a hint if present, but do not auto-select).
 2. Validate the reply starts with `http://` or `https://` and has a host. If not, ask again for the full `http://host:port` form.
 3. Call `maya_vision3d(action='select_server', params={'url': '<url>'})`.
 4. Call `maya_vision3d(action='health', params={})` again to verify — only THEN say "unreachable" if it fails.
 5. Proceed with generate_image / generate_text / poll / download.
Never conflate `vision3d_url_required` with "server unreachable".

Read the CONVERSATION HISTORY first. Skip steps the user already answered.

═══ 3D CREATION WORKFLOW ═══

1. Call `maya_vision3d(action='health', params={})` FIRST (non-blocking probe).
   - Success payload → proceed, offer all options.
   - `vision3d_url_required` error → EXPECTED on first call. NOT a failure. Present all options INCLUDING Vision3D. URL is asked in Step 5, only if user picks a Vision3D method.
   - Any other error (HTTP timeout, DNS, 5xx) → URL was selected but server is unreachable. Present all options with a note "Vision3D unreachable; will retry if chosen".
   Never ask for the Vision3D URL in Step 1 or in the same response as the method list.

2. Identify entity (use ShotGrid context if present, else sg_find).

3. In parallel:
   - sg_find Versions for image / sg_uploaded_movie
   - sg_find PublishedFiles (Image / Texture / Concept)
   - sg_find Notes with attachments
   - sg_find Asset with the description field

4. Present in ONE response. Group references:
   - IMAGE references (Versions, PublishedFiles, Notes) → for image-to-3D or Maya modeling
   - TEXT references (Asset.description) → ONLY for text-to-3D, label as "Asset description (text)"

   Then output this block (adapt the keyword list per rules below, but the AI Quality block stays VERBATIM):

   "How do you want to create the 3D model? Type ONE keyword:

   <keyword list adapted to references — see rules below>

   AI Quality — Vision3D server (model, octree, steps and faces):
    • low    — turbo model, octree 256, 10 steps, 10k faces  (~1 min)
    • medium — turbo model, octree 384, 20 steps, 50k faces  (~2 min) ← default
    • high   — full model,  octree 384, 30 steps, 150k faces (~8 min)
    • ultra  — full model,  octree 512, 50 steps, no limit    (~12 min)

   Combine method + quality: e.g. 'description high', 'prompt ultra', 'manual'."

   KEYWORD LIST RULES — each bullet is ONE short keyword. Do NOT repeat "Vision3D" in every bullet.
   - For EACH image reference found, emit ONE bullet with a short self-describing keyword (e.g. a 1-word tag or the Version code) that resolves to Vision3D image-to-3D.
   - If there is exactly ONE text reference (Asset description), emit: "description → Vision3D text-to-3D from Asset description"
   - ALWAYS emit: "prompt → Vision3D text-to-3D with a custom prompt the user will type next"
   - ALWAYS emit: "manual → direct Maya modeling, no AI, primitives only"
   - NEVER use bare numbers as keywords when there is a single reference — use a content-based label.
   - Mention "Vision3D" at most TWICE in the whole response (once in heading/intro, optionally once more for disambiguation). Do NOT repeat per-bullet.

   Always show the full quality block. Always say "Vision3D" (not "generative AI").

5. Execute — use maya_vision3d DISPATCHER actions, NOT old granular names.

   VISION3D URL GATE (before any generate_*/poll/download):
   - user picked 'manual' → skip, no URL needed.
   - Step 1 health returned success → URL already selected, proceed.
   - Step 1 health returned `vision3d_url_required` → ask ONCE: "Which Vision3D server should I use? (format: http://<hostname>:<port>)". Surface `suggested_default` from the error payload as a hint if present, but never auto-select. Validate reply starts with http:// or https:// and has a host. Call `maya_vision3d(action='select_server', params={'url':'<url>'})`. Then call `maya_vision3d(action='health', params={})` again to verify. Only if THIS second health fails, report "unreachable". Never fabricate a default, never suggest a hostname.

   Image-to-3D (dispatcher):
     a) sg_download → local image path
     b) maya_vision3d(action='generate_image', params={'image_path':'<path>', 'preset':'<quality>'}) → job_id
     c) maya_vision3d(action='poll', params={'job_id':'<id>'}) → repeat while running, show new_log_lines
     d) maya_vision3d(action='download', params={'job_id':'<id>', 'output_subdir':'<dir>'})
     e) maya_session(action='execute_python', params={'code':'<import code>'}) → import

   Text-to-3D (dispatcher):
     TEXT PROMPT RESOLUTION (priority order):
       1. User picked 'prompt' → ask for the prompt text if not already typed, use as-is.
       2. User picked 'description' (or similar text-ref label) → use Asset.description as-is (translate to English only if needed; do not paraphrase).
       3. Fallback — user picked 'prompt' but did not type one + no image + Asset.description non-empty → use Asset.description. Tell the user "Using Asset.description as text prompt: <first 80 chars>..." BEFORE calling generate_text.
     a) maya_vision3d(action='generate_text', params={'text_prompt':'<resolved>', 'preset':'<quality>'}) → job_id
     b) maya_vision3d(action='poll', params={'job_id':'<id>'}) → repeat
     c) maya_vision3d(action='download', params={'job_id':'<id>', 'output_subdir':'<dir>', 'files':['mesh.glb']})
     d) maya_session(action='execute_python', params={'code':'<import code>'}) → import

   Manual Maya: maya_create_primitive + maya_transform + maya_assign_material

6. After: offer `maya_session(action='save_scene', params={...})` and `tk_publish`.

═══ OTHER WORKFLOWS ═══
- ShotGrid query/update → sg_find / sg_update
- Publish → tk_resolve_path then tk_publish
- ALWAYS call search_sg_docs FIRST when unsure about filter syntax, operators, entity refs, or template tokens
- Entity refs in filters MUST be {"type": "Asset", "id": 123} dicts, never bare integers
- Toolkit tokens are PascalCase: {Shot}, {Asset}, {Step}

Rules:
- Don't repeat questions already answered in history.
- Always use MCP tools, never ask the user to do it manually.
- If Maya is unresponsive → `maya_session(action='launch', params={})`.
- Respond in the user's language. Execute, don't narrate.
"""


def _select_system_prompt(backend: Optional[str]) -> str:
    """Return the system prompt variant appropriate for the backend.

    Anthropic Claude has effectively unlimited context for our purposes
    (200K+ tokens), so it gets the full prompt with all the narrative
    explanations. Ollama Qwen has a 8K-32K context window depending on
    Modelfile config and is much more sensitive to prompt length and
    instruction phrasing — it gets the tighter variant.

    Defensive .lower() so future string refactors that capitalize the
    backend id (e.g. "Ollama") don't silently fall through to the
    default and ship the wrong prompt to Qwen.
    """
    backend_norm = (backend or "").strip().lower()
    if backend_norm in ("ollama", "ollama_mac"):
        return SYSTEM_PROMPT_QWEN
    return SYSTEM_PROMPT


class ClaudeWorker(QThread):
    """Runs ``claude -p "prompt" --output-format stream-json --verbose``
    and emits progress events plus the final result.

    Signals:
        progress(str)          — status updates ("Searching ShotGrid...", etc.)
        finished(str, bool)    — (final_text, is_error)
    """

    progress = Signal(str)  # status text for the UI
    finished = Signal(str, bool)  # (text, is_error)

    def __init__(
        self,
        message: str,
        context: dict | None = None,
        history: list | None = None,
        model_id: str | None = None,
        backend: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._message = message
        self._context = context or {}
        self._history = history or []
        self._model_id = model_id
        self._backend = backend

    # ---- nice names for MCP tools ----

    _TOOL_LABELS = {
        "sg_find": "Searching ShotGrid",
        "sg_create": "Creating entity in ShotGrid",
        "sg_update": "Updating ShotGrid",
        "sg_delete": "Deleting from ShotGrid",
        "sg_schema": "Querying ShotGrid schema",
        "sg_upload": "Uploading file to ShotGrid",
        "sg_download": "Downloading from ShotGrid",
        "sg_batch": "Running batch operation in ShotGrid",
        "sg_text_search": "Searching text across ShotGrid",
        "sg_summarize": "Aggregating ShotGrid data",
        "sg_revive": "Restoring entity in ShotGrid",
        "sg_note_thread": "Reading note thread from ShotGrid",
        "sg_activity": "Reading activity stream from ShotGrid",
        "tk_resolve_path": "Resolving Toolkit path",
        "tk_publish": "Publishing to ShotGrid",
        "search_sg_docs": "Searching ShotGrid documentation",
        "learn_pattern": "Learning validated pattern",
        "session_stats": "Fetching session statistics",
        "vision3d_health": "Checking Vision3D availability",
        "shape_generate_remote": "Starting image-to-3D generation (Vision3D)",
        "shape_generate_text": "Starting text-to-3D generation (Vision3D)",
        "texture_mesh_remote": "Starting texturing (Vision3D)",
        "vision3d_poll": "Polling Vision3D progress",
        "vision3d_download": "Downloading Vision3D results",
        "maya_ping": "Checking Maya connection",
        "maya_launch": "Launching Maya",
        "maya_create_primitive": "Creating primitive in Maya",
        "maya_assign_material": "Assigning material in Maya",
        "maya_transform": "Transforming object in Maya",
        "maya_list_scene": "Querying Maya scene",
        "maya_delete": "Deleting object in Maya",
        "maya_execute_python": "Running Python in Maya",
        "maya_new_scene": "Creating new Maya scene",
        "maya_save_scene": "Saving Maya scene",
    }

    def _label_for_tool(self, tool_name: str) -> str:
        """Return a human-friendly label for an MCP tool name."""
        short = tool_name
        for prefix in ("mcp__fpt-mcp__", "mcp__maya-mcp__"):
            if tool_name.startswith(prefix):
                short = tool_name[len(prefix):]
                break
        return self._TOOL_LABELS.get(short, f"Running {short}")

    # ---- main thread body ----

    def run(self):  # noqa: D102 — QThread override
        if not CLAUDE_BIN or not os.path.isfile(CLAUDE_BIN):
            self.finished.emit(
                "Claude Code CLI not found.\n"
                "Install with:  npm install -g @anthropic-ai/claude-code",
                True,
            )
            return

        # Build prompt with conversation history for multi-turn context
        parts = []

        # Include conversation history (last N exchanges) so Claude
        # knows what was already discussed and doesn't re-ask questions.
        # Truncate long assistant messages to keep prompt bloat under
        # control. Bumped from 500 → 1500 in Bucket D: 500 was a
        # band-aid for the Qwen context overflow problem (tool output
        # context Qwen needs to follow multi-turn workflows was being
        # eaten by the truncation). With SYSTEM_PROMPT_QWEN now ~5x
        # smaller and a recommended num_ctx bump in MODEL_STRATEGY.md,
        # we have headroom to keep more of each turn's context.
        if self._history:
            parts.append("=== CONVERSATION HISTORY ===")
            for msg in self._history:
                prefix = "USER" if msg["role"] == "user" else "ASSISTANT"
                text = msg["text"]
                if msg["role"] == "assistant" and len(text) > 1500:
                    text = text[:1500] + "..."
                parts.append(f"[{prefix}]: {text}")
            parts.append("=== END OF HISTORY ===\n")

        parts.append(self._message)

        if self._context:
            parts.append(f"[ShotGrid context: {json.dumps(self._context)}]")

        prompt = "\n".join(parts)

        try:
            # Build environment with backend-specific overrides
            run_env = os.environ.copy()
            run_env["CLAUDE_NO_TELEMETRY"] = "1"
            if self._model_id and self._backend:
                run_env.update(build_backend_env(self._model_id, self._backend))
                # Treat empty strings on the Anthropic SDK keys as "unset"
                # so the downstream Claude Code CLI (Node.js) sees the var
                # truly missing rather than as the literal "" — which some
                # env-parsing patterns (?? vs ||) would mis-handle.
                for _key in _BACKEND_ENV_KEYS:
                    if run_env.get(_key, None) == "":
                        run_env.pop(_key, None)

            # Pick the backend-appropriate system prompt variant.
            # Anthropic gets the full prompt; Ollama/Qwen gets the
            # compact SYSTEM_PROMPT_QWEN (D.1).
            system_prompt = _select_system_prompt(self._backend)

            cmd = [CLAUDE_BIN, "-p", prompt,
                   "--output-format", "stream-json", "--verbose",
                   "--append-system-prompt", system_prompt]
            if self._model_id:
                cmd.extend(["--model", self._model_id])

            proc = subprocess.Popen(
                cmd,
                cwd=str(_PROJECT_ROOT),  # must run from repo root so Claude finds .mcp.json
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,  # line-buffered
                text=True,
                env=run_env,
            )

            text_parts: list[str] = []
            active_tools: dict[int, str] = {}  # index → tool_name
            result_text = ""
            _text_buffer = ""  # Buffer for streaming text lines to progress

            # readline() is unbuffered per-line, unlike iterating proc.stdout
            while True:
                line = proc.stdout.readline()
                if not line:
                    # Process ended or pipe closed
                    break
                line = line.strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    # Not JSON — might be raw text output
                    text_parts.append(line)
                    continue

                ev_type = event.get("type", "")

                # ── Tool call started ─────────────────────────────────
                if ev_type == "content_block_start":
                    block = event.get("content_block", {})
                    if block.get("type") == "tool_use":
                        idx = event.get("index", 0)
                        tool_name = block.get("name", "unknown")
                        active_tools[idx] = tool_name
                        label = self._label_for_tool(tool_name)
                        self.progress.emit(f"{label}...")

                # ── Text chunk (API streaming format) ─────────────────
                elif ev_type == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        chunk = delta.get("text", "")
                        text_parts.append(chunk)
                        # Stream complete lines as progress (Vision3D log, etc.)
                        _text_buffer += chunk
                        while "\n" in _text_buffer:
                            line_text, _text_buffer = _text_buffer.split("\n", 1)
                            line_text = line_text.strip()
                            if line_text:
                                self.progress.emit(line_text)

                # ── Tool finished ─────────────────────────────────────
                elif ev_type == "content_block_stop":
                    idx = event.get("index", 0)
                    if idx in active_tools:
                        del active_tools[idx]
                        if active_tools:
                            remaining = list(active_tools.values())
                            self.progress.emit(
                                f"{self._label_for_tool(remaining[0])}..."
                            )
                        else:
                            self.progress.emit("Processing response...")

                # ── Result event (Claude Code CLI wraps final text) ───
                elif ev_type == "result":
                    # Claude Code CLI emits {"type":"result","result":"..."}
                    r = event.get("result", "")
                    if r:
                        result_text = r

                # ── Message event with content (alternative format) ───
                elif ev_type == "message":
                    content = event.get("content", [])
                    for block in content:
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))

                # ── Assistant text event (another CLI variant) ────────
                elif ev_type == "assistant":
                    msg = event.get("message", event.get("text", ""))
                    if msg:
                        text_parts.append(msg)

            proc.wait(timeout=TIMEOUT_SECONDS)

            # Prefer result_text if available, else join text parts
            response = result_text or "".join(text_parts).strip()

            # Fallback: if stream gave nothing, try stderr
            if not response:
                stderr_out = proc.stderr.read().strip()
                if stderr_out:
                    response = stderr_out

            if not response:
                response = "No response from Claude."

            is_error = proc.returncode != 0
            self.finished.emit(response, is_error)

        except subprocess.TimeoutExpired:
            if proc:
                proc.kill()
            self.finished.emit(
                "Timeout: Claude did not respond within 15 min.", True
            )
        except Exception as exc:
            self.finished.emit(f"Error: {exc}", True)
