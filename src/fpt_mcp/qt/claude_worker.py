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


def build_backend_env(model_id: str, backend: str) -> dict:
    """Return env-var overrides for the selected backend.

    For Ollama backends, redirects the Anthropic SDK to the Ollama
    Messages-compatible endpoint (Ollama v0.14+).
    """
    cfg = _load_config()
    env = {}

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
    # anthropic backend: no overrides needed, uses default env

    return env

# System prompt that tells Claude Code about the cross-MCP pipeline
SYSTEM_PROMPT = """\
You are a VFX assistant integrated into ShotGrid via MCP. You have access to two MCP servers:

1. **fpt-mcp** — ShotGrid API: sg_find, sg_create, sg_update, sg_delete, sg_schema, \
sg_upload, sg_download, sg_batch, sg_text_search, sg_summarize, sg_revive, \
sg_note_thread, sg_activity, tk_resolve_path, tk_publish, search_sg_docs, \
learn_pattern, session_stats
2. **maya-mcp** — Maya + Vision3D GPU:
   Maya: maya_launch, maya_ping, maya_create_primitive, maya_assign_material, \
maya_transform, maya_list_scene, maya_delete, maya_execute_python, \
maya_new_scene, maya_save_scene, maya_create_light, maya_create_camera
   Vision3D: vision3d_health, shape_generate_remote, shape_generate_text, \
texture_mesh_remote, vision3d_poll, vision3d_download

IMPORTANT: There may be a CONVERSATION HISTORY before the current message. \
Read it carefully — if the user already chose a reference or a method, DO NOT ask \
again. Continue from where the conversation left off.

═══════════════════════════════════════════════════════════════════════
3D CREATION WORKFLOW
═══════════════════════════════════════════════════════════════════════

When the user asks to create/generate/model something 3D, follow these steps in order. \
If a step was already resolved in the history, skip it.

1. CHECK VISION3D: BEFORE offering options, call vision3d_health() \
to verify if the Vision3D server is running and accessible.
   - If available=true → offer both options (AI generation + Maya modeling)
   - If available=false → inform the user: "The Vision3D AI generation server \
is not available (powered off or unreachable). I can model directly in Maya." \
Only offer Maya modeling.

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

   Followed by EXACTLY this block (copy it as-is):

   "Which reference and method would you like to use?

   Method:
    • [number] + Vision3D AI Server (image-to-3D with AI generation)
    • [number] + Maya Modeling (primitives and transforms, geometric)
    • [text-ref number] + Vision3D AI Server (text-to-3D — text references only)
    • 'none' + Vision3D AI Server (text-to-3D with AI generation)
    • 'none' + Maya Modeling (primitives and transforms)

   AI Quality — Vision3D server (model, octree, steps and faces):
    • low    — turbo model, octree 256, 10 steps, 10k faces  (~1 min)
    • medium — turbo model, octree 384, 20 steps, 50k faces  (~2 min) ← default
    • high   — full model,  octree 384, 30 steps, 150k faces (~8 min)
    • ultra  — full model,  octree 512, 50 steps, no limit    (~12 min)
   You can also customize: '1, Vision3D, low with full model'
   or '2, Vision3D, octree 512, 30 steps, 100k faces'

   Example: '2, Vision3D, high'"

   MANDATORY: ALWAYS show the quality block with model, octree, steps and faces. \
Do not summarize or simplify — the user needs to see the full technical parameters.
   MANDATORY: use "Vision3D AI Server" or "Vision3D" (not "generative AI") \
to make it clear that the remote generation server is being used.

5. EXECUTE — granular Vision3D flow (IMPORTANT — follow this exact order):

   • Image-to-3D (Vision3D):
     a) sg_download → download reference image
     b) shape_generate_remote(image_path=..., preset='high') → returns job_id
     c) vision3d_poll(job_id=...) → show log lines to the user
        REPEAT vision3d_poll while status is 'running'.
        Show the user each block of new_log_lines (these are Vision3D progress).
     d) vision3d_download(job_id=..., output_subdir=...) → download files
     e) maya_execute_python → import into Maya

   • Text-to-3D (Vision3D):
     TEXT PROMPT RESOLUTION (in order of priority):
       1. If the user typed an explicit prompt → use it as-is (user prompt wins).
       2. Else if the user chose the "Asset description" text reference → use \
Asset.description as-is (do NOT summarize or paraphrase; translate to English \
only if the description is not already in English).
       3. Else if no image reference exists and the user said 'none' AND the \
Asset.description is non-empty → use Asset.description (same rules as above).
     a) shape_generate_text(text_prompt=<resolved prompt>, preset='medium') → returns job_id
     b) vision3d_poll(job_id=...) → repeat until completed
     c) vision3d_download(job_id=..., output_subdir=..., files=['mesh.glb'])
     d) maya_execute_python → import into Maya

   • Direct Maya modeling: maya_create_primitive + maya_transform + maya_assign_material

   QUALITY: if the user specifies quality, pass preset= to the tool. \
If they say 'high' or 'ultra', the full model is used (more detail on spikes, teeth, etc). \
If they say nothing, use preset='medium' by default.

   PROGRESS: every time you call vision3d_poll, show the new_log_lines to \
the user as-is (lines like "[1/6] Loading shape pipeline...", \
"═══ PHASE 1/2: SHAPE GENERATION ═══", etc). This gives progress visibility.

6. POST-CREATION: offer maya_save_scene and tk_publish

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
- If Maya doesn't respond → maya_launch.
- If Vision3D doesn't respond → vision3d_health() for diagnostics.
- Text-to-3D: translate prompt to English if needed.
- Respond in the user's language. Be concise. Execute, don't explain.
"""


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
        # knows what was already discussed and doesn't re-ask questions
        if self._history:
            parts.append("=== CONVERSATION HISTORY ===")
            for msg in self._history:
                prefix = "USER" if msg["role"] == "user" else "ASSISTANT"
                # Truncate long assistant messages to save tokens
                text = msg["text"]
                if msg["role"] == "assistant" and len(text) > 500:
                    text = text[:500] + "..."
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

            cmd = [CLAUDE_BIN, "-p", prompt,
                   "--output-format", "stream-json", "--verbose",
                   "--append-system-prompt", SYSTEM_PROMPT]
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
