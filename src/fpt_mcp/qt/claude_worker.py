"""Background worker that runs Claude Code CLI and emits the response.

Runs in a QThread so the UI stays responsive during the 10-120 s
that Claude may take to answer.
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
    # Common npm global install locations
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


class ClaudeWorker(QThread):
    """Runs ``claude -p "prompt"`` in a subprocess and emits the result."""

    finished = Signal(str, bool)  # (text, is_error)

    def __init__(self, message: str, context: dict | None = None, parent=None):
        super().__init__(parent)
        self._message = message
        self._context = context or {}

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
            result = subprocess.run(
                [CLAUDE_BIN, "-p", prompt, "--output-format", "text"],
                capture_output=True,
                text=True,
                timeout=120,
                env={**os.environ, "CLAUDE_NO_TELEMETRY": "1"},
            )
            response = result.stdout.strip()
            if not response and result.stderr:
                response = result.stderr.strip()
            if not response:
                response = "Sin respuesta de Claude."
            self.finished.emit(response, False)

        except subprocess.TimeoutExpired:
            self.finished.emit("Timeout: Claude no respondió en 120 s.", True)
        except Exception as exc:
            self.finished.emit(f"Error: {exc}", True)
