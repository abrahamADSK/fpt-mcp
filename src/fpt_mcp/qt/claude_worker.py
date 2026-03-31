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

from PySide6.QtCore import QThread, Signal


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

3. SEARCH REFERENCES: sg_find on Versions (image, sg_uploaded_movie), \
PublishedFiles (Image/Texture/Concept), Notes with attachments. ALL in parallel.

4. PRESENT EVERYTHING IN A SINGLE RESPONSE — references + method + quality:
   Numbered list of references, followed by EXACTLY this block (copy it as-is):

   "Which reference and method would you like to use?

   Method:
    • [number] + Vision3D AI Server (image-to-3D with AI generation)
    • [number] + Maya Modeling (primitives and transforms, geometric)
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
     a) shape_generate_text(text_prompt=..., preset='medium') → returns job_id
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
        parent=None,
    ):
        super().__init__(parent)
        self._message = message
        self._context = context or {}
        self._history = history or []

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
            proc = subprocess.Popen(
                [CLAUDE_BIN, "-p", prompt,
                 "--output-format", "stream-json", "--verbose",
                 "--append-system-prompt", SYSTEM_PROMPT],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,  # line-buffered
                text=True,
                env={**os.environ, "CLAUDE_NO_TELEMETRY": "1"},
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
