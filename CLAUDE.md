# Contexto del Proyecto fpt-mcp para Claude

Documento de referencia que persiste entre sesiones. Actualizar cuando cambien arquitectura o workflows.

---

## 1. Arquitectura General

El proyecto **fpt-mcp** es un servidor MCP integrado en un ecosistema VFX que orquesta workflows cross-MCP:

```
Claude Desktop / Claude Code / Terminal
├── maya-mcp    → 3D modeling, rendering, Vision3D GPU
├── flame-mcp   → compositing
└── fpt-mcp     → production tracking (ShotGrid)
        ├── stdio           → Claude Desktop / Claude Code
        ├── HTTP            → Maya, Flame, scripts, inter-service
        └── Qt console      → native chat app via fpt-mcp:// protocol handler
```

### Servidor MCP: fpt-mcp

**Herramientas ShotGrid API** (acceso sin restricciones a cualquier entidad):
- `sg_find` — buscar entidades con filtros y campos
- `sg_create` — crear entidades (project auto-enlazado)
- `sg_update` — actualizar cualquier campo
- `sg_delete` — soft-delete (retire) de entidades
- `sg_schema` — inspeccionar campos disponibles
- `sg_upload` — subir archivos (thumbnail, movie, attachment)
- `sg_download` — descargar adjuntos

**Herramientas Toolkit**:
- `tk_resolve_path` — resolver rutas usando tk-config-default2
- `tk_publish` — crear PublishedFile con auto-versionado

---

## 2. Consola Qt (chat_window.py + claude_worker.py)

Interfaz gráfica nativa que ejecuta Claude Code CLI como subproceso, con streaming de progreso en tiempo real.

### Arquitectura del Worker

**Archivo**: `src/fpt_mcp/qt/claude_worker.py`

- **QThread worker**: ejecuta `claude -p "prompt" --output-format stream-json --append-system-prompt`
- **SYSTEM_PROMPT**: define el workflow completo de creación 3D (obligatorio leerlo antes de modificar)
- **_TOOL_LABELS**: diccionario que mapea nombres de tools MCP → etiquetas legibles en español

```python
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
    "vision3d_health": "Comprobando disponibilidad de Vision3D",
    "shape_generate_remote": "Iniciando generación 3D desde imagen (Vision3D)",
    "shape_generate_text": "Iniciando generación 3D desde texto (Vision3D)",
    "texture_mesh_remote": "Iniciando texturizado (Vision3D)",
    "vision3d_poll": "Consultando progreso de Vision3D",
    "vision3d_download": "Descargando resultados de Vision3D",
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
```

### Streaming de Progreso

- **Text delta events**: se parsean línea a línea desde el stream JSON
- **_text_buffer**: buffer que acumula texto parcial hasta encontrar `\n`
- **_progress_lines**: lista que acumula líneas de progreso en la sesión actual (últimas 12 visibles)
- **Burbuja "pensando"**: acumula líneas en lugar de reemplazarlas, dando visibilidad del proceso

**Archivo**: `src/fpt_mcp/qt/chat_window.py`

---

## 3. SYSTEM_PROMPT (Obligatorio leerlo antes de modificar)

Ubicación: `src/fpt_mcp/qt/claude_worker.py` línea 40

El system prompt define el workflow completo para creación 3D. Estructura:

### Paso 1: Comprobar Vision3D
```
Llamar a vision3d_health() ANTES de ofrecer opciones
- Si available=true → ofrecer ambas opciones
- Si available=false → informar y ofrecer solo Maya
```

### Paso 2-4: Identificar entidad, buscar referencias, presentar opciones
```
- sg_find para buscar contexto ShotGrid
- sg_find en paralelo en Versions (image, sg_uploaded_movie), PublishedFiles
- PRESENTAR TODO EN UNA SOLA RESPUESTA con bloque de calidad obligatorio
```

### Bloque de Calidad (OBLIGATORIO mostrar siempre)
```
Calidad IA — servidor Vision3D (modelo, octree, steps y faces):
 • low    — modelo turbo, octree 256, 10 steps, 10k faces  (~1 min)
 • medium — modelo turbo, octree 384, 20 steps, 50k faces  (~2 min) ← default
 • high   — modelo full,  octree 384, 30 steps, 150k faces (~8 min)
 • ultra  — modelo full,  octree 512, 50 steps, sin límite  (~12 min)
```

**REGLAS**:
- NO resumir ni simplificar el bloque de calidad
- Usar siempre "Servidor Vision3D IA" o "Vision3D" (NO "IA generativa")
- El usuario necesita ver los parámetros técnicos completos

### Paso 5: Ejecutar flujo granular Vision3D

**Image-to-3D**:
1. `sg_download` → descargar imagen referencia
2. `shape_generate_remote(image_path=..., preset='high')` → retorna job_id
3. `vision3d_poll(job_id=...)` → REPETIR mientras status='running', mostrar new_log_lines
4. `vision3d_download(job_id=..., output_subdir=...)` → descargar archivos
5. `maya_execute_python` → importar en Maya

**Text-to-3D**:
1. `shape_generate_text(text_prompt=..., preset='medium')` → retorna job_id
2. `vision3d_poll(job_id=...)` → repetir hasta completed
3. `vision3d_download(job_id=..., output_subdir=..., files=['mesh.glb'])`
4. `maya_execute_python` → importar en Maya

**Modelado directo Maya**: `maya_create_primitive` + `maya_transform` + `maya_assign_material`

### Paso 6: Post-creación
```
Ofrecer maya_save_scene y tk_publish
```

### Reglas generales
- NUNCA repetir pregunta ya respondida en historial
- Usar SIEMPRE herramientas MCP, NUNCA decir "hazlo manualmente"
- Si Maya no responde → `maya_launch`
- Si Vision3D no responde → `vision3d_health()` para diagnóstico
- Text-to-3D: traducir prompt a inglés
- Responder en español, ser conciso, ejecutar no explicar

---

## 4. Historial de Conversación

El system prompt requiere pasar el historial como contexto para multi-turn:

```
IMPORTANTE: Puede haber un HISTORIAL DE CONVERSACIÓN antes del mensaje actual.
Léelo con atención — si el usuario ya eligió una referencia o un método,
NO vuelvas a preguntar. Continúa desde donde se quedó la conversación.
```

**Implementación**:
- `ClaudeWorker.__init__` recibe `history: list | None = None`
- El historial se pasa al prompt para que Claude pueda contextualizar

---

## 5. Permisos Necesarios

En `~/.claude/settings.json`, habilitar todos estos tools:

**maya-mcp**:
- `mcp__maya-mcp__vision3d_health`
- `mcp__maya-mcp__shape_generate_remote`
- `mcp__maya-mcp__shape_generate_text`
- `mcp__maya-mcp__texture_mesh_remote`
- `mcp__maya-mcp__vision3d_poll`
- `mcp__maya-mcp__vision3d_download`
- Todos los tools maya_* (maya_launch, maya_ping, maya_create_primitive, maya_assign_material, maya_transform, maya_list_scene, maya_delete, maya_execute_python, maya_new_scene, maya_save_scene, maya_create_light, maya_create_camera)

**fpt-mcp**:
- Todos los tools sg_* (sg_find, sg_create, sg_update, sg_delete, sg_schema, sg_upload, sg_download)
- Todos los tools tk_* (tk_resolve_path, tk_publish)

---

## 6. Bugs Conocidos / Historial

### Resueltos
- **"Pensando..." sin progreso real** → Corregido con streaming de text_delta + acumulación de líneas en _progress_lines
- **System prompt simplificaba opciones de calidad** → Corregido con bloque "OBLIGATORIO" que muestra parámetros completos

### Pendiente
- **Maya Command Port a veces no responde desde la consola** → Considerar retry lógic o timeout más largo

---

## 7. Relación con Otros Proyectos

Los tres repos están en `~/Developer/Claude_projects/` en el Mac local:

- **maya-mcp**: servidor MCP que la consola usa para Maya + Vision3D
  - Contiene tools para maya_launch, maya_create_primitive, maya_execute_python, etc.
  - Internamente llama a vision3d (servidor GPU remoto) para generación 3D

- **vision3d**: servidor GPU remoto accesible vía maya-mcp
  - Maneja shape_generate_remote, shape_generate_text, texture_mesh_remote
  - Retorna job_id para polling

- **fpt-mcp**: este repo (ShotGrid + consola Qt)
  - Tools ShotGrid API
  - Consola Qt nativa que executa Claude Code CLI

Flujo típico: Usuario → Consola Qt (fpt-mcp) → Claude Code CLI → maya-mcp (tools) + vision3d (GPU)

---

## 8. Notas para Desarrollo

### Reinstalación después de cambios

Después de modificar `claude_worker.py` o `chat_window.py`:
```bash
cd /path/to/fpt-mcp
pip install -e .
```

### Ambiente del usuario (Abraham)

- Usa ShotGrid para VFX pipeline
- Trabaja en Mac local con glorfindel (servidor remoto para GPU/Vision3D)
- **REGLA**: NUNCA mezclar comandos de Mac y glorfindel en el mismo bloque de código

### Respuestas de Claude

- SIEMPRE en español
- Ejecutar, no explicar
- Usar todas las herramientas MCP disponibles
- Mantener el user experience fluidó sin repetir preguntas

---

## 9. Timeout y Limits

- **TIMEOUT_SECONDS**: 900 segundos (15 min) para shape generation que puede tomar ~15 minutos
- **Max líneas de progreso visibles**: 12 líneas en la burbuja "pensando"

---

## 10. Checklist para Cambios

Antes de commitear cambios en este proyecto:

- [ ] ¿Afecta al SYSTEM_PROMPT? → Actualizar este documento
- [ ] ¿Cambio en _TOOL_LABELS? → Documentar aquí
- [ ] ¿Cambio en streaming/progress logic? → Describir en sección 2
- [ ] ¿Nuevo tool o integración? → Mencionar en sección 7 o 4
- [ ] Reinstalar: `cd fpt-mcp && pip install -e .`
- [ ] Probar con ConsoleKit si es cambio en Qt

---

**Última actualización**: 2026-03-30
**Autor**: Claude Agent
**Proyecto**: fpt-mcp (Autodesk Flow Production Tracking + Qt Console)
