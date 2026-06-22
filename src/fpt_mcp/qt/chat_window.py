"""Native Qt chat window for the FPT-MCP console.

Advantages over the HTML AMI console:
  - Renders markdown, images, thumbnails natively
  - No dependency on a running HTTP server
  - Protocol handler launches directly from ShotGrid AMI links
"""

from __future__ import annotations

import html
import random
import re
from typing import Optional

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .claude_worker import AVAILABLE_EFFORTS, AVAILABLE_MODELS, ClaudeWorker
from .project_detect import detect_recent_project, resolve_page_project


class _ProjectDetector(QThread):
    """One-shot, off-main-thread resolution of the session project.

    Emits ``resolved(project_id, project_name, authoritative)`` when a project is
    found; silent otherwise (best-effort). Two sources, in priority order:
      1. the AMI's ``page_id`` → the Page's project = the project the user is
         VIEWING (``authoritative=True``);
      2. else the user's most-recent-activity project (``authoritative=False`` —
         a guess the gate confirms).
    """

    resolved = Signal(int, str, bool)

    def __init__(self, page_id=None, user_login=None, parent=None):
        super().__init__(parent)
        self._page_id = page_id
        self._login = user_login

    def run(self):  # noqa: D102 — QThread override
        if self._page_id:
            r = resolve_page_project(self._page_id)
            if r and r.get("id"):
                self.resolved.emit(int(r["id"]), r.get("name") or "", True)
                return
        if self._login:
            r = detect_recent_project(self._login)
            if r and r.get("id"):
                self.resolved.emit(int(r["id"]), r.get("name") or "", False)


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------

DARK_STYLE = """
QMainWindow, QWidget#central {
    background-color: #1c1c1c;
}
QLabel#title {
    color: #ffff00;
    font-size: 15px;
    font-weight: 700;
}
QLabel#contextBadge {
    background-color: #2f2f2f;
    color: #9a9a9a;
    padding: 3px 10px;
    border-radius: 10px;
    font-size: 12px;
}
QLabel#contextBadge[active="true"] {
    background-color: #2a2a2a;
    color: #cccccc;
}
QLabel#statusDot {
    min-width: 10px;
    max-width: 10px;
    min-height: 10px;
    max-height: 10px;
    border-radius: 5px;
    background-color: #22c55e;
}
QTextBrowser#chat {
    background-color: #1c1c1c;
    color: #cccccc;
    border: none;
    font-size: 14px;
    selection-background-color: #3a3a3a;
}
QLineEdit#input {
    background-color: #252525;
    border: 1px solid #3a3a3a;
    color: #e0e0e0;
    padding: 10px 14px;
    border-radius: 10px;
    font-size: 14px;
}
QLineEdit#input:focus {
    border-color: #ffff00;
}
QPushButton#sendBtn {
    background-color: #ffff00;
    color: #1c1c1c;
    border: none;
    padding: 10px 22px;
    border-radius: 10px;
    font-size: 14px;
    font-weight: 600;
}
QPushButton#sendBtn:hover {
    background-color: #cccc00;
}
QPushButton#sendBtn:disabled {
    background-color: #3a3a3a;
}
QWidget#header {
    background-color: #202020;
    border-bottom: 1px solid #2f2f2f;
}
QWidget#inputBar {
    background-color: #202020;
    border-top: 1px solid #2f2f2f;
}
"""


# ---------------------------------------------------------------------------
# Minimal markdown → HTML converter (no external deps)
# ---------------------------------------------------------------------------

def _md_to_html(text: str) -> str:
    """Convert simple markdown to HTML for QTextBrowser.

    Handles: **bold**, *italic*, `code`, ```code blocks```,
    headings (#), bullet lists, and image paths.
    """
    # Collapse runs of 3+ consecutive blank lines into max 2 so the rendered
    # output does not accumulate enormous vertical gaps when the model emits
    # extra blank separators. Single and double blank lines are preserved as-is.
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = text.split("\n")
    out: list[str] = []
    in_code = False
    prev_was_blank = False

    for line in lines:
        # Fenced code blocks
        if line.strip().startswith("```"):
            if in_code:
                out.append("</pre>")
                in_code = False
            else:
                out.append('<pre style="background:#1c1c1c;color:#cccccc;'
                           'padding:10px;border-radius:6px;font-size:13px;'
                           'overflow-x:auto;margin:4px 0;">')
                in_code = True
            prev_was_blank = False
            continue

        if in_code:
            out.append(html.escape(line))
            continue

        # Headings
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            sizes = {1: "18px", 2: "16px", 3: "14px"}
            out.append(f'<p style="font-size:{sizes[level]};font-weight:700;'
                       f'color:#e0e0e0;margin:6px 0 2px;">{html.escape(m.group(2))}</p>')
            prev_was_blank = False
            continue

        # Bullet points (already compact at margin:2px 0 2px 16px)
        if re.match(r"^\s*[-*]\s+", line):
            content = re.sub(r"^\s*[-*]\s+", "", line)
            content = _inline_fmt(content)
            out.append(f'<p style="margin:2px 0 2px 16px;">&#8226; {content}</p>')
            prev_was_blank = False
            continue

        # Normal paragraph — tight margin to avoid stacking default <p> gaps.
        if line.strip():
            out.append(f'<p style="margin:3px 0;">{_inline_fmt(line)}</p>')
            prev_was_blank = False
        else:
            # A single blank line inside a paragraph run adds a small gap,
            # but consecutive blanks collapse to at most one gap (paragraph
            # margins already provide separation between prose blocks).
            if not prev_was_blank:
                out.append('<div style="height:6px;"></div>')
            prev_was_blank = True

    if in_code:
        out.append("</pre>")

    return "\n".join(out)


def _inline_fmt(text: str) -> str:
    """Apply inline markdown formatting."""
    text = html.escape(text)
    # Code spans
    text = re.sub(r"`([^`]+)`",
                  r'<code style="background:#252525;padding:2px 5px;'
                  r'border-radius:3px;color:#cccccc;">\1</code>', text)
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    # Links
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                  r'<a style="color:#cccccc;" href="\2">\1</a>', text)
    return text


# ---------------------------------------------------------------------------
# Chat Window
# ---------------------------------------------------------------------------

class ChatWindow(QMainWindow):
    """Native chat window that routes messages through Claude Code CLI."""

    # ── Whimsical "thinking" gerunds shown as a rotating orange header
    # while the worker is busy. Intentionally AGNOSTIC and GENERIC —
    # they must never hint at any real tool, server, protocol or process
    # happening under the hood. Think Claude-classic "pondering /
    # musing / vibing": pure flavor, no engineering leakage.
    _THINKING_VERBS = (
        "pondering",
        "musing",
        "vibing",
        "cogitating",
        "ruminating",
        "marinating",
        "brewing",
        "percolating",
        "simmering",
        "noodling",
        "tinkering",
        "scheming",
        "conjuring",
        "weaving",
        "wrangling",
        "churning",
        "crunching",
        "philosophizing",
        "hypothesizing",
        "daydreaming",
        "chin-stroking",
        "meandering",
        "mulling",
        "contemplating",
        "deliberating",
        "improvising",
        "jazz-handing",
        "wizarding",
        "alchemizing",
        "finagling",
        "frolicking",
        "twiddling thumbs",
        "doodling",
        "fiddling",
        "puttering",
        "whisking",
        "juggling",
        "hustling",
        "bustling",
        "flourishing",
        "puzzling",
        "head-scratching",
        "hand-waving",
        "galaxy-braining",
    )

    def __init__(
        self,
        entity_type: str | None = None,
        entity_id: int | None = None,
        entity_code: str | None = None,
        project_id: int | None = None,
        project_name: str | None = None,
        user_login: str | None = None,
    ):
        super().__init__()
        self._history: list = []
        self._context: dict = {}
        if entity_type and entity_id:
            self._context["entity_type"] = entity_type
            self._context["entity_id"] = entity_id
        if entity_code:
            self._context["entity_code"] = entity_code
        if project_id:
            self._context["project_id"] = project_id
        if project_name:
            self._context["project_name"] = project_name
        if user_login:
            self._context["user_login"] = user_login

        self._worker: Optional[ClaudeWorker] = None
        # Multi-backend: default to first model (Claude Opus 4.7 / anthropic)
        self._selected_model_idx = 0
        self._selected_effort_idx = 0
        # Whimsical thinking-bubble rotator state
        self._thinking_verb: str = ""
        self._progress_lines: list[str] = []
        self._thinking_timer = QTimer(self)
        self._thinking_timer.setInterval(2500)  # ms between verb rotations
        self._thinking_timer.timeout.connect(self._rotate_thinking_verb)
        self._setup_ui()
        self.setStyleSheet(DARK_STYLE)

        # Resolve a session project ONCE, at launch, when none was supplied
        # (e.g. opened from the global user menu). AMI / DCC-engine context is
        # authoritative and already in self._context; otherwise detect the
        # user's most recent project off-thread and pin it for the session — the
        # gate confirms a *detected* project before the first write. Chat 69.
        self._project_detector: Optional[_ProjectDetector] = None
        self._maybe_start_detector()

    # ---- Thinking bubble helpers ----

    def _pick_thinking_verb(self) -> str:
        """Return a random verb that differs from the current one."""
        if len(self._THINKING_VERBS) <= 1:
            return self._THINKING_VERBS[0]
        while True:
            v = random.choice(self._THINKING_VERBS)
            if v != self._thinking_verb:
                return v

    def _rotate_thinking_verb(self):
        """Pick a new verb and redraw the thinking bubble. Called by QTimer."""
        if self._worker is None:
            return
        self._thinking_verb = self._pick_thinking_verb()
        self._refresh_thinking_bubble()

    def _refresh_thinking_bubble(self):
        """Render the thinking bubble with the orange header + progress lines."""
        header_html = (
            f'<div style="color:#fb923c;font-style:italic;font-size:13px;'
            f'margin-bottom:4px;">{html.escape(self._thinking_verb)}&hellip;</div>'
        )
        if self._progress_lines:
            visible = self._progress_lines[-12:]
            lines_html = "<br>".join(html.escape(line) for line in visible)
            if len(self._progress_lines) > 12:
                lines_html = (
                    f"<i style='color:#4a4a4a;'>... "
                    f"({len(self._progress_lines) - 12} previous lines)</i><br>"
                    + lines_html
                )
            body_html = (
                f'<div style="font-family:monospace;font-size:12px;'
                f'line-height:1.5;color:#888888;">{lines_html}</div>'
            )
        else:
            body_html = ""
        self._update_last_bubble(header_html + body_html, "thinking")

    # ---- Project context ----

    def _maybe_start_detector(self):
        """Start the recent-project detector once — if we have a user but no
        project yet.

        Called from ``__init__`` AND from ``update_context``: on macOS the AMI
        context (including ``user_login``) arrives via an Apple Event AFTER
        ``__init__``, so triggering only at ``__init__`` would always miss it
        (the Chat-69 "launched from the AMI but no context" symptom).
        """
        if self._project_detector is not None:
            return  # already started this session
        if self._context.get("project_id"):
            return  # an authoritative project is already set
        page_id = self._context.get("page_id")
        login = self._context.get("user_login")
        if not (page_id or login):
            return  # no page and no user identity → nothing to resolve from
        self._project_detector = _ProjectDetector(page_id, login, parent=self)
        self._project_detector.resolved.connect(self._on_project_resolved)
        self._project_detector.start()

    def _on_project_resolved(self, project_id: int, project_name: str, authoritative: bool):
        """Pin the resolved session project (only if none arrived meanwhile).

        ``authoritative`` (the AMI page → its project = the project you are
        viewing) → bound silently, no confirm. Otherwise it is the
        activity-heuristic guess → ``project_detected=True`` so the SYSTEM_PROMPT
        gate confirms it before the first write (it may be stale).
        """
        if self._context.get("project_id"):
            return
        self._context["project_id"] = project_id
        if not authoritative:
            self._context["project_detected"] = True
        if project_name:
            self._context["project_name"] = project_name
        label = project_name or f"Project {project_id}"
        self._context_badge.setText(label if authoritative else f"{label} · detected")
        self._context_badge.setProperty("active", True)
        self._context_badge.style().unpolish(self._context_badge)
        self._context_badge.style().polish(self._context_badge)

    # ---- UI Setup ----

    def _setup_ui(self):
        self.setWindowTitle("FPT-MCP Console")
        self.setMinimumSize(700, 500)
        self.resize(800, 600)

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setObjectName("header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 10, 16, 10)

        title = QLabel("FPT-MCP Console")
        title.setObjectName("title")
        header_layout.addWidget(title)

        ctx_text = "No context"
        ctx_active = False
        if self._context.get("entity_type") and self._context.get("entity_id"):
            ctx_text = f"{self._context['entity_type']} #{self._context['entity_id']}"
            ctx_active = True

        self._context_badge = QLabel(ctx_text)
        self._context_badge.setObjectName("contextBadge")
        self._context_badge.setProperty("active", ctx_active)
        header_layout.addWidget(self._context_badge)

        header_layout.addStretch()

        # Model selector combo
        self._model_combo = QComboBox()
        for label, _, _ in AVAILABLE_MODELS:
            self._model_combo.addItem(label)
        self._model_combo.setCurrentIndex(self._selected_model_idx)
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        self._model_combo.setStyleSheet(
            "QComboBox { background: #252525; color: #e0e0e0; border: 1px solid #3a3a3a; "
            "border-radius: 6px; padding: 3px 8px; font-size: 12px; }"
        )
        header_layout.addWidget(self._model_combo)

        # Effort selector combo (mirrors the model combo styling)
        self._effort_combo = QComboBox()
        for label, _ in AVAILABLE_EFFORTS:
            self._effort_combo.addItem(label)
        self._effort_combo.setCurrentIndex(self._selected_effort_idx)
        self._effort_combo.currentIndexChanged.connect(self._on_effort_changed)
        self._effort_combo.setStyleSheet(
            "QComboBox { background: #252525; color: #e0e0e0; border: 1px solid #3a3a3a; "
            "border-radius: 6px; padding: 3px 8px; font-size: 12px; }"
        )
        header_layout.addWidget(self._effort_combo)

        status = QLabel()
        status.setObjectName("statusDot")
        header_layout.addWidget(status)

        layout.addWidget(header)

        # Chat area
        self._chat = QTextBrowser()
        self._chat.setObjectName("chat")
        self._chat.setOpenExternalLinks(True)
        self._chat.setReadOnly(True)
        self._chat.setFont(QFont("SF Pro", 13))
        layout.addWidget(self._chat, 1)

        # Input bar
        input_bar = QWidget()
        input_bar.setObjectName("inputBar")
        input_layout = QHBoxLayout(input_bar)
        input_layout.setContentsMargins(16, 10, 16, 10)

        self._input = QLineEdit()
        self._input.setObjectName("input")
        self._input.setPlaceholderText("Type here...")
        self._input.returnPressed.connect(self._send)
        input_layout.addWidget(self._input, 1)

        self._send_btn = QPushButton("Send")
        self._send_btn.setObjectName("sendBtn")
        self._send_btn.clicked.connect(self._send)
        input_layout.addWidget(self._send_btn)

        layout.addWidget(input_bar)

        self._input.setFocus()

    # ---- Model selection ----

    def _on_model_changed(self, index: int):
        """Called when the user picks a different model in the combo."""
        self._selected_model_idx = index

    def _get_selected_model(self) -> tuple[str, str]:
        """Return (model_id, backend) for the currently selected model."""
        _, model_id, backend = AVAILABLE_MODELS[self._selected_model_idx]
        return model_id, backend

    def _on_effort_changed(self, index: int):
        """Called when the user picks a different effort level."""
        self._selected_effort_idx = index

    def _get_selected_effort(self) -> str:
        """Return the effort value for the currently selected combo entry."""
        return AVAILABLE_EFFORTS[self._selected_effort_idx][1]

    # ---- Context update (for protocol handler late-arriving URLs) ----

    def update_context(self, ctx: dict):
        """Update the ShotGrid context after window creation.

        Called by FPTApplication when a protocol URL arrives via Apple Event.
        Resets conversation history when the entity changes so Claude does
        not carry stale context from the previous entity.
        """
        old_entity = (
            self._context.get("entity_type"),
            self._context.get("entity_id"),
        )

        if ctx.get("entity_type"):
            self._context["entity_type"] = ctx["entity_type"]
        if ctx.get("entity_id"):
            self._context["entity_id"] = ctx["entity_id"]
        if ctx.get("entity_code"):
            self._context["entity_code"] = ctx["entity_code"]
        elif ctx.get("entity_type") and ctx.get("entity_id"):
            # Entity changed but caller did not include the code; drop the
            # stale code so the next prompt does not reference the wrong
            # asset name.
            self._context.pop("entity_code", None)
        if ctx.get("project_id"):
            self._context["project_id"] = ctx["project_id"]
            # AMI / engine context is authoritative — drop the "detected" flag
            # so the gate stops treating the project as a guess to confirm.
            self._context.pop("project_detected", None)
        if ctx.get("project_name"):
            self._context["project_name"] = ctx["project_name"]
        if ctx.get("user_login"):
            self._context["user_login"] = ctx["user_login"]
        if ctx.get("page_id"):
            self._context["page_id"] = ctx["page_id"]

        # The AMI context (esp. user_login) may have just arrived via the Apple
        # Event, after __init__ — now that we have it, start the detector if it
        # did not start earlier. Chat 69 (the "launched from AMI, no context"
        # timing fix).
        self._maybe_start_detector()

        new_entity = (
            self._context.get("entity_type"),
            self._context.get("entity_id"),
        )

        # Reset history and chat display when switching to a different entity
        if old_entity != new_entity and any(old_entity):
            self._history = []
            self._chat.clear()

        # Update the badge
        if self._context.get("entity_type") and self._context.get("entity_id"):
            self._context_badge.setText(
                f"{self._context['entity_type']} #{self._context['entity_id']}"
            )
            self._context_badge.setProperty("active", True)
            self._context_badge.style().unpolish(self._context_badge)
            self._context_badge.style().polish(self._context_badge)

        # Bring window to front
        self.raise_()
        self.activateWindow()

    # ---- Conversation history (passed to Claude for multi-turn context) ----

    _history: list  # list of {"role": "user"|"assistant", "text": str}

    # ---- Chat logic ----

    def _append_bubble(self, html_content: str, role: str):
        """Add a message bubble to the chat."""
        colors = {
            "user":      ("text-align:right;", "#2f2f2f", "#e0e0e0"),
            "assistant": ("text-align:left;",  "#252525", "#cccccc"),
            "error":     ("text-align:left;",  "#7f1d1d", "#fca5a5"),
            "thinking":  ("text-align:left;",  "#252525", "#888888"),
        }
        align, bg, fg = colors.get(role, colors["assistant"])
        bubble = (
            f'<div style="{align}margin:6px 4px;">'
            f'<div style="display:inline-block;background:{bg};color:{fg};'
            f'padding:10px 14px;border-radius:12px;max-width:85%;'
            f'text-align:left;font-size:14px;line-height:1.6;">'
            f'{html_content}'
            f'</div></div>'
        )
        self._chat.append(bubble)

    def _send(self):
        text = self._input.text().strip()
        if not text:
            return

        self._input.clear()
        self._send_btn.setEnabled(False)
        self._append_bubble(html.escape(text), "user")

        # Record user message in history
        self._history.append({"role": "user", "text": text})

        # Status bubble that will be updated with progress events + rotating verb
        self._status_id = self._chat.document().blockCount()
        self._progress_lines = []
        self._thinking_verb = self._pick_thinking_verb()
        self._append_bubble("", "thinking")  # placeholder, _refresh fills it in
        self._refresh_thinking_bubble()
        self._thinking_timer.start()

        model_id, backend = self._get_selected_model()
        effort = self._get_selected_effort()
        self._worker = ClaudeWorker(
            text, self._context, history=self._history[:-1],
            model_id=model_id, backend=backend, effort=effort, parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_response)
        self._worker.start()

    def _on_progress(self, status: str):
        """Append a real progress line and redraw the thinking bubble."""
        self._progress_lines.append(status)
        self._refresh_thinking_bubble()

    def _update_last_bubble(self, html_content: str, role: str):
        """Replace the last bubble in the chat with new content."""
        colors = {
            "user":      ("text-align:right;", "#2f2f2f", "#e0e0e0"),
            "assistant": ("text-align:left;",  "#252525", "#cccccc"),
            "error":     ("text-align:left;",  "#7f1d1d", "#fca5a5"),
            "thinking":  ("text-align:left;",  "#252525", "#888888"),
        }
        align, bg, fg = colors.get(role, colors["assistant"])
        bubble = (
            f'<div style="{align}margin:6px 4px;">'
            f'<div style="display:inline-block;background:{bg};color:{fg};'
            f'padding:10px 14px;border-radius:12px;max-width:85%;'
            f'text-align:left;font-size:14px;line-height:1.6;">'
            f'{html_content}'
            f'</div></div>'
        )
        # Remove last block and append updated one
        cursor = self._chat.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.movePosition(cursor.MoveOperation.StartOfBlock, cursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        cursor.deletePreviousChar()  # remove trailing newline
        self._chat.setTextCursor(cursor)
        self._chat.append(bubble)

    def _on_response(self, text: str, is_error: bool):
        role = "error" if is_error else "assistant"
        # Stop the rotating "thinking" verb timer before replacing the bubble.
        if self._thinking_timer.isActive():
            self._thinking_timer.stop()
        # Replace the thinking/status bubble with the final response
        self._update_last_bubble(_md_to_html(text), role)
        self._send_btn.setEnabled(True)
        self._input.setFocus()

        # Record assistant response in history (keep last 10 exchanges max)
        if not is_error:
            self._history.append({"role": "assistant", "text": text})
        if len(self._history) > 20:
            self._history = self._history[-20:]

        self._worker = None
