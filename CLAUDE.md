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
- `tk_resolve_path` — resolver rutas de publicación desde la PipelineConfiguration real del proyecto (o fallback a defaults)
- `tk_publish` — publicar fichero: resolver path, copiar archivo, find/create PublishedFileType, enlazar Task, registrar PublishedFile en ShotGrid

**Herramientas RAG** (Retrieval-Augmented Generation):
- `search_sg_docs` — búsqueda híbrida (ChromaDB semántica + BM25 léxica + HyDE + RRF fusion) en la documentación de las 3 APIs de ShotGrid. **OBLIGATORIO** antes de queries complejas o desconocidas.
- `learn_pattern` — persistir patrones validados en la base de conocimiento. Model trust gates: solo Sonnet/Opus escriben directamente; otros modelos stagen candidatos.
- `session_stats` — estadísticas de sesión: tokens usados, tokens ahorrados por RAG, patrones aprendidos, bloqueos de seguridad.

**Módulo de seguridad** (`safety.py`):
- 12+ regex patterns que detectan operaciones peligrosas antes de ejecutarlas
- Integrado en sg_find y sg_delete (los tools con mayor riesgo)
- Detecta: bulk delete, filtros vacíos sin límite, path traversal, schema modification, entity format errors, invalid filter operators

### Tecnologías RAG

| Componente | Tecnología | Propósito |
|---|---|---|
| Vector DB | ChromaDB (persistent) | Búsqueda semántica por similitud coseno |
| Embeddings | BAAI/bge-large-en-v1.5 (~570 MB) | Codificación de documentos y queries |
| Lexical search | rank_bm25 (BM25Okapi) | Match exacto de nombres de métodos y operadores |
| Query expansion | HyDE adaptativo | Expande queries cortas con templates de código por API |
| Rank fusion | RRF (k=60) | Combina rankings semántico + léxico sin calibración |
| Token tracking | Integrado en _stats | Mide tokens usados/ahorrados en cada sesión |
| Self-learning | learn_pattern + model gates | Acumula patrones validados entre sesiones |
| Cache | In-session dict (A12) | Evita búsquedas ChromaDB repetidas |

### Corpus indexado (3 colecciones, ~340 chunks)

- `docs/SG_API.md` — shotgun_api3 Python SDK: métodos, filtros, operadores, anti-patterns
- `docs/TK_API.md` — Toolkit sgtk: templates, tokens, PipelineConfiguration, derived templates
- `docs/REST_API.md` — REST API: referencia comparativa para evitar confusión con Python SDK

---

## 2. Toolkit — Resolución Dinámica de Paths (tk_config.py)

**Archivo**: `src/fpt_mcp/tk_config.py` (reemplaza al anterior `paths.py`, que está deprecated)

### Dos modos de operación

**Modo 1 — Descubrimiento automático** (proyectos con Advanced Setup):
- Consulta la entidad `PipelineConfiguration` de ShotGrid
- Lee `roots.yml` → obtiene el `project_root` (path local del storage primario)
- Lee `templates.yml` → carga todos los templates de path del proyecto
- Compatible con configs locales y configs distribuidas tipo `dev`
- Entry point: `discover_config(project_id, sg_find_func)`

**Modo 2 — Fallback** (proyectos sin Advanced Setup):
- Si no hay `PipelineConfiguration` o no es resoluble, usa templates estándar de `tk-config-default2`
- El `project_root` viene de `PUBLISH_ROOT` en `.env`, o se deriva del `tank_name` del proyecto: `~/ShotGrid/{tank_name}/`
- Los paths generados son compatibles con Toolkit loaders si el proyecto luego recibe Advanced Setup
- Entry point: `_build_fallback_config(project_root)`

**Entry point recomendado**: `discover_or_fallback()` — intenta Modo 1, fallback a Modo 2.

### Templates derivados (Vision3D pipeline)

Para tipos de fichero que no existen en tk-config-default2, se inyectan templates derivados:

```python
DERIVED_TEMPLATES = {
    "usd_asset_publish":         "@asset_root/publish/usd/{name}.v{version}.usd",
    "fbx_asset_publish":         "@asset_root/publish/fbx/{name}.v{version}.fbx",
    "texture_asset_publish":     "@asset_root/publish/textures/{name}.v{version}.png",
    "review_asset_mov":          "@asset_root/review/{Asset}_{name}_v{version}.mov",
    "usd_shot_publish":          "@shot_root/publish/usd/{name}.v{version}.usd",
    "fbx_shot_publish":          "@shot_root/publish/fbx/{name}.v{version}.fbx",
    "camera_shot_fbx_publish":   "@shot_root/publish/camera/{name}.v{version}.fbx",
    "exr_shot_render":           "@shot_root/publish/renders/{name}/v{version}/{name}.v{version}.{SEQ}.exr",
    "review_shot_mov":           "@shot_root/review/{Shot}_{name}_v{version}.mov",
}
```

Estos solo se añaden si no existen ya en la config real del proyecto.

### PUBLISH_TYPE_MAP — mapping de tipo de publicación a template

```python
PUBLISH_TYPE_MAP = {
    "Maya Scene":   {"asset": "maya_asset_publish",     "shot": "maya_shot_publish"},
    "USD Scene":    {"asset": "usd_asset_publish",      "shot": "usd_shot_publish"},
    "FBX Model":    {"asset": "fbx_asset_publish",      "shot": "fbx_shot_publish"},
    "Texture":      {"asset": "texture_asset_publish",  "shot": None},
    "Alembic Cache":{"asset": "asset_alembic_cache",    "shot": None},
    "Camera FBX":   {"asset": None,                     "shot": "camera_shot_fbx_publish"},
    "EXR Render":   {"asset": None,                     "shot": "exr_shot_render"},
    "Review MOV":   {"asset": "review_asset_mov",       "shot": "review_shot_mov"},
}
```

### Clases y funciones clave

- **`TkConfig`**: Clase principal. Almacena project_root, templates parseados, aliases resueltos.
  - `resolve_path(template_name, fields)` → Path completo en disco
  - `next_version(template_name, fields)` → siguiente versión escaneando filesystem
  - `get_template(name)` → template string con aliases expandidos
  - `list_templates(pattern)` → listar templates filtrados
- **`discover_or_fallback()`**: Entry point principal, usa caché por project_id
- **`_build_template_fields()`** (en server.py): Construye el dict de fields consultando SG (código entidad, sg_asset_type, sg_sequence)
- **`_resolve_template_name()`** (en server.py): Mapea publish_type + entity_type → template name

### Pipeline de publicación (tk_publish)

```
1. discover_or_fallback → TkConfig
2. _resolve_template_name → template correcto según publish_type + entity_type
3. _build_template_fields → SG entity context (code, asset_type, sequence)
4. tk_config.next_version → auto-incremento escaneando filesystem
5. tk_config.resolve_path → path completo
6. shutil.copy2 → copiar archivo fuente si se proporciona local_path
7. sg_find_one("PublishedFileType") → find or create si no existe
8. sg_find_one("Task") → enlazar con task del pipeline step
9. sg_create("PublishedFile") → registrar en ShotGrid
```

### Configuración de pipeline personalizada (futuro)

Para un pipeline customizado, el wizard de Advanced Project Setup en ShotGrid Desktop soporta tres fuentes:
- **Default** — `tk-config-default2` desde App Store
- **Git** — clonar desde URL (e.g. `https://github.com/yourorg/tk-config-custom.git`)
- **Proyecto existente** — copiar config de otro proyecto

Los templates específicos de Vision3D (USD, FBX, textures, etc.) se inyectan como derived templates. Pueden añadirse a un repo de tk-config custom para integración completa con Toolkit. El repo de config custom se creará aparte.

### Distributed configs (TODO fase 2)

Actualmente solo se soporta el tipo `dev` de descriptor. Pendiente:
- `app_store` — resolver desde el bundle cache: `~/Library/Caches/Shotgun/<site>/bundle_cache/`
- `git` — resolver desde el bundle cache con formato diferente

---

## 3. Consola Qt (chat_window.py + claude_worker.py)

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

## 4. SYSTEM_PROMPT (Obligatorio leerlo antes de modificar)

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

**Text-to-3D** (pipeline completo con textura):
1. `shape_generate_text(text_prompt=..., preset='medium')` → retorna job_id
2. `vision3d_poll(job_id=...)` → repetir hasta completed (3 fases: text→image, shape, texture)
3. `vision3d_download(job_id=..., output_subdir=..., files=['textured.glb', 'mesh.glb', 'mesh_uv.obj', 'texture_baked.png'])`
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

## 5. Historial de Conversación

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

## 6. Permisos Necesarios

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
- Todos los tools RAG (search_sg_docs, learn_pattern, session_stats)

---

## 7. Bugs Conocidos / Historial

### Resueltos
- **"Pensando..." sin progreso real** → Corregido con streaming de text_delta + acumulación de líneas en _progress_lines
- **System prompt simplificaba opciones de calidad** → Corregido con bloque "OBLIGATORIO" que muestra parámetros completos
- **paths.py incompatible con tk-config-default2** → Reemplazado por `tk_config.py` con descubrimiento dinámico de PipelineConfiguration
- **tk_resolve_path crash: `next_version_number() takes 1 positional argument but 4 were given`** → Causado por el antiguo paths.py. Resuelto con el nuevo tk_config.py
- **PublishedFileType no se creaba para tipos nuevos** → tk_publish ahora hace find-or-create automático
- **Task no se enlazaba al PublishedFile** → tk_publish ahora busca Task por entity + step automáticamente
- **Archivo no se copiaba al publish path** → tk_publish ahora hace shutil.copy2 si se proporciona local_path

### Pendiente
- **Maya Command Port a veces no responde desde la consola** → Considerar retry logic o timeout más largo
- **Distributed config: app_store y git descriptors** → Solo dev type soportado actualmente

---

## 8. Relación con Otros Proyectos

Los tres repos están en `~/Claude_projects/` en el Mac local:

- **maya-mcp**: servidor MCP que la consola usa para Maya + Vision3D
  - Repo: `~/Claude_projects/maya-mcp-project/`
  - Contiene tools para maya_launch, maya_create_primitive, maya_execute_python, etc.
  - Internamente llama a vision3d (servidor GPU remoto) vía HTTP REST (puerto 8000)
  - Incluye `vision3d_health` para verificar disponibilidad antes de ofrecer opciones

- **vision3d**: servidor GPU remoto accesible vía maya-mcp
  - Repo: `~/Claude_projects/vision3d/` (Mac) / `/home/flame/ai-studio/vision3d/` (glorfindel)
  - Maneja shape_generate_remote, shape_generate_text, texture_mesh_remote
  - Text-to-3D: pipeline de 3 fases (HunyuanDiT → rembg → shape → paint → textured.glb)
  - Retorna job_id para polling

- **fpt-mcp**: este repo (ShotGrid + Toolkit + consola Qt)
  - Tools ShotGrid API (sg_find, sg_create, sg_update, sg_delete, sg_schema, sg_upload, sg_download)
  - Tools Toolkit (tk_resolve_path, tk_publish) con descubrimiento dinámico de config
  - Consola Qt nativa que ejecuta Claude Code CLI

### Flujo típico cross-MCP (pipeline completo)
```
1. Usuario → Consola Qt (fpt-mcp) → Claude Code CLI
2. sg_find → buscar Asset/Shot y referencias en ShotGrid
3. sg_download → descargar imagen de referencia
4. shape_generate_remote (maya-mcp) → iniciar generación 3D en Vision3D
5. vision3d_poll (maya-mcp) → monitorizar progreso
6. vision3d_download (maya-mcp) → descargar resultados (GLB, OBJ, textura)
7. maya_execute_python (maya-mcp) → importar mesh en Maya, normalizar
8. maya_save_scene (maya-mcp) → guardar escena Maya
9. tk_publish (fpt-mcp) → resolver path + copiar archivo + registrar PublishedFile
```

---

## 9. Notas para Desarrollo

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

## 10. Timeout y Limits

- **TIMEOUT_SECONDS**: 900 segundos (15 min) para shape generation que puede tomar ~15 minutos
- **Max líneas de progreso visibles**: 12 líneas en la burbuja "pensando"

---

## 11. Checklist para Cambios

Antes de commitear cambios en este proyecto:

- [ ] ¿Afecta al SYSTEM_PROMPT? → Actualizar este documento (sección 4)
- [ ] ¿Cambio en _TOOL_LABELS? → Documentar aquí (sección 3)
- [ ] ¿Cambio en streaming/progress logic? → Describir en sección 3
- [ ] ¿Cambio en tk_config.py o templates? → Actualizar sección 2
- [ ] ¿Nuevo tool o integración? → Mencionar en secciones 1 y 8
- [ ] Reinstalar: `cd fpt-mcp && pip install -e .`
- [ ] Probar con ConsoleKit si es cambio en Qt

---

**Última actualización**: 2026-03-30
**Autor**: Claude Agent
**Proyecto**: fpt-mcp (Autodesk Flow Production Tracking + Qt Console)
