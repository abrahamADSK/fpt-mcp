"""Background worker that runs Claude Code CLI and emits the response.

Runs in a QThread so the UI stays responsive.  Uses --output-format
stream-json to provide real-time progress feedback for long-running
operations (shape generation, texturing, etc.).
"""

from __future__ import annotations

import datetime
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
    # Default = Opus 4.8 (index 0). Fable kept as an option — make it the
    # default again when it is available.
    ("Claude Opus 4.8",       "claude-opus-4-8",           "anthropic"),
    ("Claude Fable 5",        "claude-fable-5",            "anthropic"),
    ("Claude Sonnet 4.6",     "claude-sonnet-4-6",         "anthropic"),
    # ── Self-hosted Ollama (glorfindel RTX 3090, LAN) ────────────────
    ("Qwen3.5 9B 🖥",         "qwen3.5-mcp",               "ollama"),
    ("GLM-4.7 Flash 🖥",      "glm-4.7-flash",             "ollama"),
    # ── Mac-local Ollama (offline, no LAN) ───────────────────────────
    ("Qwen3.5 9B 🍎",         "qwen3.5-mcp",               "ollama_mac"),
    ("Qwen3.5 4B 🍎",         "qwen3.5:4b",                "ollama_mac"),
]

# Each entry: (display_label, effort_value). "auto" re-enables adaptive
# thinking (both hardening env vars cleared); the fixed levels force that
# effort with adaptive thinking off. Default = "auto" (index 0).
AVAILABLE_EFFORTS = [
    ("Auto", "auto"),
    ("Low", "low"),
    ("Medium", "medium"),
    ("High", "high"),
    ("Max", "max"),
]
DEFAULT_EFFORT = "auto"

# Models allowed to write RAG patterns (learn_pattern). Local models are read-only.
# Self-learning is reserved for the two top cloud tiers: Opus and Fable.
WRITE_ALLOWED_MODELS = ["claude-opus", "claude-fable"]

# Default Ollama URLs — overridden by config.json (ollama_url / ollama_mac_url).
# Remote Ollama has no default: users must configure config.json explicitly.
# Local Mac Ollama defaults to localhost which is always correct.
DEFAULT_OLLAMA_URL: str | None = None
DEFAULT_OLLAMA_MAC_URL = "http://localhost:11434"

# Context window forced when pre-loading the Mac-local Ollama model.
# Ollama's Anthropic-compat /v1/messages ignores Modelfile num_ctx and
# defaults to 4096 without an explicit preflight against /api/generate.
# Tuned for 4B/9B models on Mac unified memory (24 GB).
OLLAMA_MAC_NUM_CTX = 8192


def resolve_keep_alive(
    config_path: "str | Path | None" = None,
    *,
    default: "str | int" = "30m",
) -> "str | int":
    """Read the ``ollama_keep_alive`` knob from ``config.json`` (F1b).

    Mirrors ``flame_mcp._config.resolve_keep_alive``: reads the
    ``ollama_keep_alive`` key from the repo's ``config.json``, validates
    the type (must be ``str`` or ``int``, not ``bool`` / ``None`` / container),
    and falls back to *default* on any read or parse error so a typo
    cannot 400 the Ollama preflight.

    Parameters
    ----------
    config_path : str | Path | None
        Path to ``config.json``.  When ``None`` (the default) the helper
        locates the file relative to this module's own path — the same
        strategy used by :func:`_load_config`.
    default : str | int
        Returned when the key is absent, the file is unreadable, or the
        configured value has an unsupported type.  Defaults to ``"30m"``
        so 5–15 min reading gaps don't cold-reload the local model.

    Returns
    -------
    str | int
        A duration string (e.g. ``"30m"``, ``"1h"``) or integer seconds.
        Anything else collapses to *default*.
    """
    if config_path is None:
        config_path = Path(__file__).resolve().parent.parent / "config.json"
    try:
        with open(config_path) as _f:
            _cfg = json.load(_f)
        value = _cfg.get("ollama_keep_alive", default)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            return value
        return default
    except Exception:
        return default


def _preload_ollama_mac_model(model: str, url: str, num_ctx: int,
                               keep_alive: "str | int" = "30m") -> None:
    """Pre-load the Mac-local Ollama model with an explicit num_ctx.

    Ollama's Anthropic-compatible endpoint (``/v1/messages``) does NOT honour
    the ``num_ctx`` set in a model's Modelfile — it silently falls back to
    the global default of 4096 tokens. The native ``/api/generate`` endpoint
    DOES respect ``options.num_ctx``. By POSTing an empty-prompt request there
    first, we load (or reload) the model's runner with ``num_ctx`` tokens.
    Ollama then reuses that runner for the subsequent Anthropic-API call
    made by the ``claude`` CLI subprocess.

    Uses ``urllib.request`` (stdlib) — no third-party dependency. This is
    a standalone module-level helper (not a method) so it is independent
    of the Qt worker class and trivially unit-testable via monkeypatching.

    Only ``ollama_mac`` uses this preflight. ``ollama_cloud`` is deliberately
    skipped (cloud runners manage context) and LAN ``ollama`` is operator-
    managed. Exceptions are logged but not raised — the main call may still
    succeed, just with the default 4096-token ceiling.

    Parameters
    ----------
    model : str
        Ollama model tag (e.g. ``"qwen3.5:4b"``, ``"qwen3.5-mcp"``).
    url : str
        Base URL of the Mac-local Ollama daemon (e.g. ``http://localhost:11434``).
    num_ctx : int
        Context window in tokens (typically ``OLLAMA_MAC_NUM_CTX``).
    keep_alive : str | int
        How long Ollama should keep the model loaded after this request.
        Pass a duration string (``"30m"``, ``"1h"``) or integer seconds.
        Resolved from ``config.json → ollama_keep_alive`` at the call site
        via :func:`resolve_keep_alive`; defaults to ``"30m"`` (F1b).
    """
    import urllib.request as _urllib_req

    payload = json.dumps({
        "model":      model,
        "prompt":     "",          # empty — we only want to load the runner
        "options":    {"num_ctx": num_ctx},
        "keep_alive": keep_alive,
        "stream":     False,
    }).encode()

    api_url = f"{url}/api/generate"
    req = _urllib_req.Request(
        api_url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with _urllib_req.urlopen(req, timeout=120) as resp:
            resp.read()
        print(
            f"[claude_worker] Ollama Mac pre-load OK: model={model} "
            f"num_ctx={num_ctx} url={url}"
        )
    except Exception as exc:
        # Non-fatal — log and continue; the main call may still succeed
        # (just capped at Ollama's default 4096-token context).
        print(
            f"[claude_worker] Ollama Mac pre-load warning (non-fatal): {exc}"
        )


def _load_config() -> dict:
    """Load config.json from the fpt_mcp package directory."""
    cfg_path = Path(__file__).resolve().parent.parent / "config.json"
    try:
        return json.loads(cfg_path.read_text())
    except Exception:
        return {}


# Env keys that the Anthropic SDK reads. We always set ALL THREE on every
# backend switch so a stale value from a previous run cannot leak across.
_BACKEND_ENV_KEYS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING", "CLAUDE_CODE_EFFORT_LEVEL")


def project_env_override(context: dict | None) -> dict:
    """Resolve the ``SHOTGRID_PROJECT_ID`` the console session must operate on.

    ALWAYS returns an explicit value so the console NEVER silently inherits the
    static ``.env`` project (Chat 69, option B — zero silent defaults):

    - launched with a valid ``project_id`` (a ShotGrid AMI fired from *within*
      a project) → that project, so ``sg_create`` / ``sg_find`` auto-link to the
      loaded project (applied to the spawned ``claude`` subprocess env, which the
      MCP servers it spawns inherit at startup);
    - launched WITHOUT a project (global user menu, or standalone) or with a
      malformed id → ``"0"`` ("no project"). With ``PROJECT_ID == 0`` the server
      adds no project filter and a project-scoped ``sg_create`` fails instead of
      writing to a default, while read-only ``Project`` listing still works — the
      SYSTEM_PROMPT project-context gate then makes the assistant ASK the user
      which project (listing them) before any write.

    Note: client.py restores an injected ``SHOTGRID_PROJECT_ID`` after its
    ``load_dotenv(override=True)``, so this value wins over ``.env``.
    """
    pid = (context or {}).get("project_id")
    if pid:
        try:
            n = int(pid)
            if n > 0:
                return {"SHOTGRID_PROJECT_ID": str(n)}
        except (TypeError, ValueError):
            pass
    return {"SHOTGRID_PROJECT_ID": "0"}


def build_backend_env(model_id: str, backend: str, effort: str = "auto") -> dict:
    """Return env-var overrides for the selected backend.

    Always returns explicit values for all three Anthropic SDK env vars,
    even when switching back to the anthropic backend, so a stale
    ANTHROPIC_BASE_URL from a previous Ollama run cannot misroute the SDK.

    Also controls reasoning effort on every claude subprocess spawned
    from the Qt console via the ``effort`` param (default ``"auto"``):

    - ``"auto"`` clears BOTH hardening env vars
      (``CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING`` and
      ``CLAUDE_CODE_EFFORT_LEVEL``) so the CLI uses its adaptive-thinking
      default. They are emitted here as empty strings and scrubbed from
      the child env by the ``_BACKEND_ENV_KEYS`` empty-string pass in
      ``run()`` (so an inherited value from ``os.environ`` cannot leak).
    - a fixed level (``"low"``/``"medium"``/``"high"``/``"max"``) forces
      adaptive thinking OFF at that effort.

    Set unconditionally so the behavior is identical regardless of backend
    switch order (Ollama ignores the vars in practice). The user
    controls their own top-level claude session via /effort — these
    overrides apply to the MCP-spawned subprocess only.
    """
    cfg = _load_config()
    env: dict[str, str] = {}
    if effort and effort != "auto":
        # Fixed effort: force adaptive thinking off at the chosen level.
        env["CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING"] = "1"
        env["CLAUDE_CODE_EFFORT_LEVEL"] = effort
    else:
        # "auto": empty strings here are scrubbed from the child env by the
        # _BACKEND_ENV_KEYS empty-string pass in run(), so the CLI falls back
        # to its adaptive-thinking default.
        env["CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING"] = ""
        env["CLAUDE_CODE_EFFORT_LEVEL"] = ""

    if backend == "ollama":
        base_url = cfg.get("ollama_url") or DEFAULT_OLLAMA_URL
        if not base_url:
            raise ValueError(
                "Ollama remote URL not configured. Set 'ollama_url' in "
                "config.json (e.g. \"ollama_url\": \"http://hostname:11434\")."
            )
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


# ── Read-only console + improvement-suggestion capture ────────────────
# The spawned `claude` subprocess is denied every file-mutation tool
# (see DISALLOWED_TOOLS), so it cannot edit the repo (it once rewrote the
# server's own source). Instead it is instructed to surface code-improvement
# ideas as `@@SUGGESTION@@ <text>` lines; this trusted worker is the ONLY
# writer of CONSOLE_IMPROVEMENTS.md — it logs them for a later dev session /
# PR and strips the markers from what the user sees.
DISALLOWED_TOOLS = ["Edit", "Write", "MultiEdit", "NotebookEdit", "Bash"]
_IMPROVEMENTS_FILE = _PROJECT_ROOT / "CONSOLE_IMPROVEMENTS.md"
_SUGGESTION_RE = re.compile(r"(?m)^.*@@SUGGESTION@@[ \t]*(.+?)[ \t]*$")


def capture_suggestions(text: str, dest: Path = _IMPROVEMENTS_FILE) -> tuple[str, int]:
    """Pull ``@@SUGGESTION@@`` lines from *text*, append them to *dest*, and
    return ``(text_without_those_lines, count)``.

    The read-only console agent cannot edit code, so it logs improvement ideas
    via the marker. Each marked line is appended to the backlog file (created
    with a header on first use) with a timestamp, and removed from the reply so
    the marker never shows in the console UI. Best-effort: any write failure is
    swallowed (returns the cleaned text, count 0) and never breaks the reply.
    """
    matches = [m.group(1).strip() for m in _SUGGESTION_RE.finditer(text or "")]
    matches = [s for s in matches if s]
    clean = re.sub(r"\n{3,}", "\n\n", _SUGGESTION_RE.sub("", text or "")).strip()
    if not matches:
        return clean, 0
    try:
        new_file = not dest.exists()
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        with dest.open("a", encoding="utf-8") as fh:
            if new_file:
                fh.write(
                    "# Console improvement backlog\n\n"
                    "Auto-captured suggestions from the **read-only** fpt-mcp "
                    "console subprocess. The console agent cannot edit code; it "
                    "logs ideas here to pick up in a dev session or PR.\n"
                )
            for s in matches:
                fh.write(f"\n- [{stamp}] {s}")
            fh.write("\n")
    except OSError:
        return clean, 0
    return clean, len(matches)


# Per-call token-usage monitoring: one line per `claude -p` turn, shared across
# all MCP consoles, so the request weight (input context + reasoning output) is
# objectively visible. Tail: `tail -f ~/Library/Logs/mcp-console-usage.log`.
_USAGE_LOG = Path("~/Library/Logs/mcp-console-usage.log").expanduser()


def log_usage(usage: Optional[dict], console: str, dest: Path = _USAGE_LOG) -> None:
    """Append a per-call token-usage record (best-effort; never raises)."""
    if not usage:
        return
    try:
        inp = usage.get("input_tokens", 0) or 0
        cr = usage.get("cache_read_input_tokens", 0) or 0
        cc = usage.get("cache_creation_input_tokens", 0) or 0
        out = usage.get("output_tokens", 0) or 0
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = (
            f"[{stamp}] {console:<5} context~={inp + cr + cc} "
            f"(input={inp} cache_read={cr} cache_creation={cc}) output={out}\n"
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass


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
        effort: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._message = message
        self._context = context or {}
        self._history = history or []
        self._model_id = model_id
        self._backend = backend
        self._effort = effort or DEFAULT_EFFORT

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
            # Defer MCP tool schemas: only tool NAMES load upfront and the model
            # fetches a schema on demand via ToolSearch. The FPT console keeps all
            # three MCP servers (it orchestrates Maya + Flame), so this is its
            # main relief from the ~49k-token per-request bloat.
            run_env["ENABLE_TOOL_SEARCH"] = "true"
            # Bind the MCP servers (spawned as children of this claude
            # subprocess) to the project this console was launched for, so
            # sg_create / sg_find target the loaded project instead of the
            # static SHOTGRID_PROJECT_ID in .env. No-op for standalone launch.
            run_env.update(project_env_override(self._context))
            if self._model_id and self._backend:
                run_env.update(build_backend_env(self._model_id, self._backend, self._effort))
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

            # Force num_ctx on the Mac-local Ollama runner BEFORE spawning
            # the claude subprocess. Ollama's Anthropic-compat endpoint
            # ignores Modelfile num_ctx and defaults to 4096; the native
            # /api/generate endpoint DOES honour options.num_ctx. Non-fatal
            # on failure. LAN `ollama` (operator-managed) and `ollama_cloud`
            # are deliberately excluded.
            if self._backend == "ollama_mac" and self._model_id:
                _cfg = _load_config()
                _mac_url = _cfg.get("ollama_mac_url", DEFAULT_OLLAMA_MAC_URL)
                _preload_ollama_mac_model(
                    model=self._model_id,
                    url=_mac_url,
                    num_ctx=OLLAMA_MAC_NUM_CTX,
                    keep_alive=resolve_keep_alive(),
                )

            cmd = [CLAUDE_BIN, "-p", prompt,
                   "--output-format", "stream-json", "--verbose",
                   "--append-system-prompt", system_prompt]
            if self._model_id:
                cmd.extend(["--model", self._model_id])
            # Read-only console: deny every file-mutation tool so the
            # subprocess cannot modify the repo (it once rewrote the server's
            # own source). MCP tools + Read stay available; improvement ideas
            # are captured via capture_suggestions, not by editing files.
            cmd.extend(["--disallowedTools", *DISALLOWED_TOOLS])

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
                    log_usage(event.get("usage"), "fpt")

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

                # ── User event with tool_result (capture MCP tool progress) ──
                # Claude Code CLI feeds tool results back as user-role messages
                # before the next assistant turn. Without this branch, the
                # only progress visible during a long-running maya_vision3d
                # poll loop would be the dispatcher action label, repeated
                # uninformatively on every tick. Here we look for any tool
                # result whose JSON payload carries a `new_log_lines` array
                # (the Vision3D poll contract) and emit each line as
                # progress directly — no dependency on whether the model
                # chooses to echo the lines as text. Strictly defensive:
                # malformed JSON or missing field → silent skip.
                elif ev_type == "user":
                    msg = event.get("message", {}) or {}
                    for block in msg.get("content", []) or []:
                        if not isinstance(block, dict) or block.get("type") != "tool_result":
                            continue
                        content = block.get("content", "")
                        if isinstance(content, list):
                            content = "".join(
                                b.get("text", "")
                                for b in content
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                        if not isinstance(content, str) or "new_log_lines" not in content:
                            continue
                        try:
                            payload = json.loads(content)
                        except (json.JSONDecodeError, ValueError):
                            continue
                        for line in payload.get("new_log_lines") or []:
                            line_clean = str(line).strip()
                            if line_clean:
                                self.progress.emit(line_clean)

            proc.wait(timeout=TIMEOUT_SECONDS)

            # Prefer result_text if available, else join text parts
            response = result_text or "".join(text_parts).strip()
            # Read-only console: log any @@SUGGESTION@@ lines to the backlog
            # and strip the markers from what the user sees.
            response, _ = capture_suggestions(response)

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
