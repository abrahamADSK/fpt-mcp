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


class ClaudeWorker(QThread):
    """Runs ``claude -p "prompt" --output-format stream-json`` and emits
    progress events plus the final result.

    Signals:
        progress(str)          — status updates ("Llamando sg_find...", etc.)
        finished(str, bool)    — (final_text, is_error)
    """

    progress = Signal(str)  # status text for the UI
    finished = Signal(str, bool)  # (text, is_error)

    def __init__(self, message: str, context: dict | None = None, parent=None):
        super().__init__(parent)
        self._message = message
        self._context = context or {}

    # ---- nice names for MCP tools ----

    _TOOL_LABELS = {
        "sg_find": "Buscando en ShotGrid",
        "sg_create": "Creando entidad en ShotGrid",
        "sg_update": "Actualizando en ShotGrid",
        "sg_delete": "Eliminando en ShotGrid",
        "sg_schema": "Consultando schema ShotGrid",
        "sg_upload": "Subiendo archivo a ShotGrid",
        "sg_download": "Descargando desde ShotGrid",
        "tk_resolve_path": "Resolviendo ruta Toolkit",
        "tk_publish": "Publicando en ShotGrid",
        "shape_generate_remote": "Generando geometría 3D en GPU remota",
        "texture_mesh_remote": "Texturizando mesh en GPU remota",
        "maya_ping": "Verificando conexión con Maya",
        "maya_create_primitive": "Creando primitiva en Maya",
        "maya_assign_material": "Asignando material en Maya",
        "maya_transform": "Transformando objeto en Maya",
        "maya_list_scene": "Consultando escena Maya",
        "maya_delete": "Eliminando objeto en Maya",
        "maya_execute_python": "Ejecutando Python en Maya",
        "maya_new_scene": "Creando nueva escena Maya",
        "maya_save_scene": "Guardando escena Maya",
    }

    def _label_for_tool(self, tool_name: str) -> str:
        """Return a human-friendly label for an MCP tool name."""
        # Strip mcp__server__ prefix if present
        short = tool_name
        for prefix in ("mcp__fpt-mcp__", "mcp__maya-mcp__"):
            if tool_name.startswith(prefix):
                short = tool_name[len(prefix):]
                break
        return self._TOOL_LABELS.get(short, f"Ejecutando {short}")

    # ---- main thread body ----

    def run(self):  # noqa: D102 — QThread override
        if not CLAUDE_BIN or not os.path.isfile(CLAUDE_BIN):
            self.finished.emit(
                "Claude Code CLI no encontrado.\n"
                "Instala con:  npm install -g @anthropic-ai/claude-code",
                True,
            )
            return

        prompt = self._message
        if self._context:
            prompt += f" [Contexto ShotGrid: {json.dumps(self._context)}]"

        try:
            proc = subprocess.Popen(
                [CLAUDE_BIN, "-p", prompt, "--output-format", "stream-json"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "CLAUDE_NO_TELEMETRY": "1"},
            )

            text_parts: list[str] = []
            active_tools: dict[int, str] = {}  # index → tool_name

            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
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

                # ── Text chunk ────────────────────────────────────────
                elif ev_type == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text_parts.append(delta.get("text", ""))

                # ── Tool finished ─────────────────────────────────────
                elif ev_type == "content_block_stop":
                    idx = event.get("index", 0)
                    if idx in active_tools:
                        del active_tools[idx]
                        if active_tools:
                            # Still running other tools
                            remaining = list(active_tools.values())
                            self.progress.emit(
                                f"{self._label_for_tool(remaining[0])}..."
                            )
                        else:
                            self.progress.emit("Procesando respuesta...")

                # ── Result event (Claude Code CLI specific) ───────────
                elif ev_type == "result":
                    result_text = event.get("result", "")
                    if result_text:
                        text_parts.append(result_text)

            proc.wait(timeout=TIMEOUT_SECONDS)

            response = "".join(text_parts).strip()

            # Fallback: if stream-json gave no text, try stderr
            if not response:
                stderr_out = proc.stderr.read().strip()
                if stderr_out:
                    response = stderr_out

            if not response:
                response = "Sin respuesta de Claude."

            is_error = proc.returncode != 0
            self.finished.emit(response, is_error)

        except subprocess.TimeoutExpired:
            if proc:
                proc.kill()
            self.finished.emit(
                "Timeout: Claude no respondió en 15 min.", True
            )
        except Exception as exc:
            self.finished.emit(f"Error: {exc}", True)
