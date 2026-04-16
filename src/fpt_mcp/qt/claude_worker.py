"""Background worker that runs Claude Code CLI and emits the response.

Runs in a QThread so the UI stays responsive.  Uses --output-format
stream-json to provide real-time progress feedback for long-running
operations (shape generation, texturing, etc.).
"""

from __future__ import annotations

import json
import os
import re
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

    Also hardens reasoning quality on every claude subprocess spawned
    from the Qt console: adaptive thinking off, effort level max. Set
    unconditionally so the behavior is identical regardless of backend
    switch order (Ollama ignores the vars in practice). The user
    controls their own top-level claude session via /effort — these
    overrides apply to the MCP-spawned subprocess only.
    """
    cfg = _load_config()
    env: dict[str, str] = {
        "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING": "1",
        "CLAUDE_CODE_EFFORT_LEVEL": "max",
    }

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

# System prompts are stored as standalone text files under qt/system_prompts/
# to enable structural regression tests (Bucket E) without parsing Python
# source. Both variants MUST stay in lockstep for workflow semantics —
# tests in tests/test_system_prompts.py enforce the structural invariants
# (identical quality block, same step skeleton, compressed ratio, etc.).
_PROMPTS_DIR = Path(__file__).resolve().parent / "system_prompts"
SYSTEM_PROMPT = (_PROMPTS_DIR / "default.txt").read_text(encoding="utf-8")
SYSTEM_PROMPT_QWEN = (_PROMPTS_DIR / "qwen.txt").read_text(encoding="utf-8")


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

    # Flat tool labels for non-dispatcher tools (one label regardless of params).
    _TOOL_LABELS = {
        # fpt-mcp
        "sg_find": "Searching ShotGrid",
        "sg_create": "Creating entity in ShotGrid",
        "sg_update": "Updating ShotGrid",
        "sg_delete": "Retiring entity in ShotGrid",
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
        "fpt_launch_app": "Launching DCC application",
        "session_stats": "Fetching session statistics",
        # maya-mcp — direct tools (not dispatchers)
        "maya_create_primitive": "Creating primitive in Maya",
        "maya_assign_material": "Assigning material in Maya",
        "maya_transform": "Transforming object in Maya",
        "maya_mesh_operation": "Editing mesh in Maya",
        "maya_set_keyframe": "Setting keyframe in Maya",
        "maya_create_light": "Creating light in Maya",
        "maya_create_camera": "Creating camera in Maya",
        "maya_import_file": "Importing file into Maya",
        "maya_viewport_capture": "Capturing Maya viewport",
        "search_maya_docs": "Searching Maya documentation",
        # maya-mcp — dispatchers (generic fallback; refined per-action below)
        "maya_session": "Maya scene operation",
        "maya_vision3d": "Vision3D operation",
    }

    # Tools that take an `action` param and deserve action-aware labels so
    # long-running dispatch loops (e.g. poll) still produce a visible
    # heartbeat in the Qt console progress bubble. Keyed by (tool, action).
    _DISPATCHER_ACTION_LABELS = {
        # maya_session actions
        ("maya_session", "launch"): "Launching Maya",
        ("maya_session", "ping"): "Checking Maya connection",
        ("maya_session", "new_scene"): "Creating new Maya scene",
        ("maya_session", "save_scene"): "Saving Maya scene",
        ("maya_session", "list_scene"): "Querying Maya scene",
        ("maya_session", "delete"): "Deleting object in Maya",
        ("maya_session", "execute_python"): "Running Python in Maya",
        ("maya_session", "scene_snapshot"): "Snapshotting Maya scene state",
        ("maya_session", "shelf_button"): "Creating Maya shelf button",
        ("maya_session", "viewport_capture"): "Capturing Maya viewport",
        # maya_vision3d actions
        ("maya_vision3d", "select_server"): "Selecting Vision3D server",
        ("maya_vision3d", "health"): "Checking Vision3D availability",
        ("maya_vision3d", "generate_image"): "Starting image-to-3D (Vision3D)",
        ("maya_vision3d", "generate_text"): "Starting text-to-3D (Vision3D)",
        ("maya_vision3d", "texture"): "Starting texturing (Vision3D)",
        ("maya_vision3d", "poll"): "Polling Vision3D progress",
        ("maya_vision3d", "download"): "Downloading Vision3D results",
    }

    _DISPATCHER_TOOLS = frozenset(("maya_session", "maya_vision3d"))

    def _short_tool_name(self, tool_name: str) -> str:
        """Strip the mcp__<server>__ prefix from a raw tool name."""
        for prefix in ("mcp__fpt-mcp__", "mcp__maya-mcp__"):
            if tool_name.startswith(prefix):
                return tool_name[len(prefix):]
        return tool_name

    def _label_for_tool(self, tool_name: str, tool_input: dict | None = None) -> str:
        """Return a human-friendly label for an MCP tool call.

        For dispatcher tools (``maya_session``, ``maya_vision3d``) that take
        an ``action`` param, a refined label keyed by ``(tool, action)`` is
        preferred. When the action is not yet known (the stream still hasn't
        delivered the full input JSON), the flat fallback is used.
        """
        short = self._short_tool_name(tool_name)
        if short in self._DISPATCHER_TOOLS and tool_input:
            action = tool_input.get("action")
            if action:
                refined = self._DISPATCHER_ACTION_LABELS.get((short, action))
                if refined:
                    return refined
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
            tool_input_buffers: dict[int, str] = {}  # index → partial input JSON
            tool_refined_emitted: set[int] = set()  # indices that already got a refined label
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
                        tool_input_buffers[idx] = ""
                        # Some CLI variants populate the full input here.
                        # If so, use it to compute a refined label immediately.
                        initial_input = block.get("input") or {}
                        label = self._label_for_tool(tool_name, initial_input if isinstance(initial_input, dict) else None)
                        self.progress.emit(f"{label}...")
                        if initial_input and isinstance(initial_input, dict) and initial_input.get("action"):
                            tool_refined_emitted.add(idx)

                # ── Delta event (text OR tool input JSON chunks) ──────
                elif ev_type == "content_block_delta":
                    delta = event.get("delta", {})
                    dtype = delta.get("type", "")
                    if dtype == "text_delta":
                        chunk = delta.get("text", "")
                        text_parts.append(chunk)
                        # Stream complete lines as progress (Vision3D log, etc.)
                        _text_buffer += chunk
                        while "\n" in _text_buffer:
                            line_text, _text_buffer = _text_buffer.split("\n", 1)
                            line_text = line_text.strip()
                            if line_text:
                                self.progress.emit(line_text)
                    elif dtype == "input_json_delta":
                        # Tool input arrives in JSON fragments; accumulate
                        # per-index and try to surface the action as soon as
                        # it is parseable. This lets long-running dispatcher
                        # loops (maya_vision3d action=poll) show "Polling
                        # Vision3D progress..." on every poll tick.
                        idx = event.get("index", 0)
                        partial = delta.get("partial_json", "")
                        tool_input_buffers[idx] = tool_input_buffers.get(idx, "") + partial
                        if idx in active_tools and idx not in tool_refined_emitted:
                            # Try a cheap regex first to avoid paying json.loads
                            # on every chunk until the action key is at least
                            # textually present.
                            buf = tool_input_buffers[idx]
                            if '"action"' in buf:
                                m = re.search(r'"action"\s*:\s*"([^"]+)"', buf)
                                if m:
                                    action = m.group(1)
                                    refined = self._DISPATCHER_ACTION_LABELS.get(
                                        (self._short_tool_name(active_tools[idx]), action)
                                    )
                                    if refined:
                                        self.progress.emit(f"{refined}...")
                                        tool_refined_emitted.add(idx)

                # ── Tool finished ─────────────────────────────────────
                elif ev_type == "content_block_stop":
                    idx = event.get("index", 0)
                    if idx in active_tools:
                        del active_tools[idx]
                        tool_input_buffers.pop(idx, None)
                        tool_refined_emitted.discard(idx)
                        if active_tools:
                            remaining_idx = next(iter(active_tools))
                            remaining_name = active_tools[remaining_idx]
                            self.progress.emit(
                                f"{self._label_for_tool(remaining_name)}..."
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
