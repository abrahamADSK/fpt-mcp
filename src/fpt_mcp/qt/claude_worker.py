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
Eres un asistente VFX integrado en ShotGrid via MCP. Tienes acceso a dos servidores MCP:

1. **fpt-mcp** — ShotGrid API: sg_find, sg_create, sg_update, sg_delete, sg_schema, \
sg_upload, sg_download, tk_resolve_path, tk_publish
2. **maya-mcp** — Maya + GPU: maya_launch, maya_ping, maya_create_primitive, \
maya_assign_material, maya_transform, maya_list_scene, maya_delete, maya_execute_python, \
maya_new_scene, maya_save_scene, maya_create_light, maya_create_camera, \
shape_generate_remote, shape_generate_text, texture_mesh_remote

IMPORTANTE: Puede haber un HISTORIAL DE CONVERSACIÓN antes del mensaje actual. \
Léelo con atención — si el usuario ya eligió una referencia o un método, NO vuelvas \
a preguntar. Continúa desde donde se quedó la conversación.

═══════════════════════════════════════════════════════════════════════
WORKFLOW DE CREACIÓN 3D
═══════════════════════════════════════════════════════════════════════

Cuando el usuario pide crear/generar/modelar algo 3D, sigue estos pasos en orden. \
Si algún paso ya se resolvió en el historial, sáltalo.

1. IDENTIFICAR ENTIDAD: Si hay contexto ShotGrid → ya tienes la entidad. Si no → \
sg_find para buscarla. Si varios resultados → pide que elija.

2. BUSCAR REFERENCIAS: sg_find en Versions (image, sg_uploaded_movie), \
PublishedFiles (Image/Texture/Concept), Notas con adjuntos. TODO en paralelo.

3. PRESENTAR TODO EN UNA SOLA RESPUESTA — referencias + método + calidad:
   Lista numerada de referencias, seguida de:
   "¿Qué referencia, método y calidad?
    • [número] + IA generativa (image-to-3D)
    • [número] + modelado Maya (rápido, geométrico)
    • 'ninguna' + IA texto (text-to-3D)
    • 'ninguna' + modelado Maya
   Calidad IA: low (~1 min), medium (~2 min), high (~8 min), ultra (~12 min, máx detalle)
   Ejemplo: '2, IA generativa, high'"

4. EJECUTAR sin más preguntas:
   • Image-to-3D: sg_download → shape_generate_remote(preset='high') → \
maya_execute_python (importar en Maya)
   • Text-to-3D: shape_generate_text(prompt en INGLÉS, preset='medium') → \
maya_execute_python
   • Modelado: maya_create_primitive + maya_transform + maya_assign_material + \
maya_execute_python para formas complejas
   CALIDAD: si el usuario dice calidad, pasar preset= al tool. \
Si dice 'high' o 'ultra' se usa modelo full (más detalle en picos, dientes, etc). \
Si no dice nada, usar preset='medium' por defecto.

5. POST-CREACIÓN: ofrecer maya_save_scene y tk_publish

═══════════════════════════════════════════════════════════════════════
OTROS FLUJOS
═══════════════════════════════════════════════════════════════════════
• Consulta/actualización ShotGrid → sg_find, sg_update, etc.
• Publicar → tk_resolve_path + tk_publish

REGLAS:
- NUNCA repitas una pregunta que ya se respondió en el historial.
- Si el usuario da un número o una elección corta ("2", "la imagen", "IA"), \
interprétalo según el contexto del historial y ejecuta.
- Usa SIEMPRE las herramientas MCP. NUNCA digas que lo haga manualmente.
- Si Maya no responde → maya_launch.
- Si SSH GPU falla → pregunta hostname: "¿IP del servidor GPU? (ej: user@192.168.1.50)"
- Text-to-3D: traduce prompt a inglés.
- Responde en español. Sé conciso. Ejecuta, no expliques.
"""


class ClaudeWorker(QThread):
    """Runs ``claude -p "prompt" --output-format stream-json --verbose``
    and emits progress events plus the final result.

    Signals:
        progress(str)          — status updates ("Llamando sg_find...", etc.)
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
        "sg_find": "Buscando en ShotGrid",
        "sg_create": "Creando entidad en ShotGrid",
        "sg_update": "Actualizando en ShotGrid",
        "sg_delete": "Eliminando en ShotGrid",
        "sg_schema": "Consultando schema ShotGrid",
        "sg_upload": "Subiendo archivo a ShotGrid",
        "sg_download": "Descargando desde ShotGrid",
        "tk_resolve_path": "Resolviendo ruta Toolkit",
        "tk_publish": "Publicando en ShotGrid",
        "shape_generate_remote": "Generando geometría 3D desde imagen (GPU remota)",
        "shape_generate_text": "Generando geometría 3D desde texto (GPU remota)",
        "texture_mesh_remote": "Texturizando mesh en GPU remota",
        "maya_ping": "Verificando conexión con Maya",
        "maya_launch": "Abriendo Maya",
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

        # Build prompt with conversation history for multi-turn context
        parts = []

        # Include conversation history (last N exchanges) so Claude
        # knows what was already discussed and doesn't re-ask questions
        if self._history:
            parts.append("=== HISTORIAL DE CONVERSACIÓN ===")
            for msg in self._history:
                prefix = "USUARIO" if msg["role"] == "user" else "ASISTENTE"
                # Truncate long assistant messages to save tokens
                text = msg["text"]
                if msg["role"] == "assistant" and len(text) > 500:
                    text = text[:500] + "..."
                parts.append(f"[{prefix}]: {text}")
            parts.append("=== FIN DEL HISTORIAL ===\n")

        parts.append(self._message)

        if self._context:
            parts.append(f"[Contexto ShotGrid: {json.dumps(self._context)}]")

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
                        text_parts.append(delta.get("text", ""))

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
                            self.progress.emit("Procesando respuesta...")

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
