# HANDOFF — fpt-mcp

**Nivel de completitud: Alto (~80%)**. 18 tools, RAG completo (311 chunks), Toolkit integration funcional.

---

## Estado actual

**Funciona**:
- 18 MCP tools: 13 ShotGrid API, 2 Toolkit (tk_resolve_path, tk_publish), 3 RAG
- RAG completo con 311 chunks en 3 docs: SG_API.md (131 chunks), TK_API.md (87 chunks), REST_API.md (93 chunks)
- HyDE adaptativo por dominio (Toolkit, REST, SG Python SDK)
- Toolkit path resolution con PipelineConfiguration auto-discovery (Mode 1) o explicit path (Mode 2)
- tk_publish con auto-increment de versión, find-or-create de PublishedFileType, Task linking, file copy
- Safety module con 12 regex patterns
- Token tracking
- Self-learning con model trust gates
- Qt console con markdown rendering, dark theme, protocol handler (fpt-mcp://)
- AMI handler para ShotGrid Action Menu Items
- Tres transportes: stdio, HTTP, Qt console
- Launchd autostart en macOS
- macOS .app bundle generator

**Limitaciones**:
- Distributed config (app_store, git descriptors) — solo dev type soportado (Mode 1)
- Maya Command Port a veces no responde desde console — potencial fix: retry logic

---

## Relación con FPT_MCP (proyecto de test)

`FPT_MCP/` (en `~/FPT_MCP/`) es un **proyecto real de Flow Production Tracking** que sirve como entorno de pruebas para validar las APIs del fpt-mcp:

```
FPT_MCP/
├── mcp_project_abraham/   # Proyecto SG (Project ID 1244)
│   ├── assets/            # Assets de test
│   ├── editorial/         # Editorial data
│   ├── reference/         # Reference files
│   └── sequences/         # Sequences/Shots
└── setup/
    ├── cache/             # SG toolkit cache
    ├── config/            # tk-config-default2
    ├── install/           # Toolkit install
    ├── tank               # tank CLI
    └── tank.bat           # tank CLI (Windows)
```

Es el **dataset de referencia** para testear:
- `tk_resolve_path` contra templates.yml reales de tk-config-default2
- `tk_publish` para publicar ficheros y registrarlos en ShotGrid
- `sg_find`, `sg_create`, etc. contra entidades reales del proyecto

**No es redundante** con fpt-mcp. fpt-mcp es el server MCP; FPT_MCP es el proyecto de ShotGrid contra el que se testea.

---

## Tests existentes

| File | Phase | Tests | Status |
|---|---|---|---|
| `tests/test_safety.py` | 3.4 | 49 | ✅ 49/49 pass |
| `tests/test_sg_operations.py` | 3.1 | 16 | ✅ 16/16 pass |
| `tests/test_toolkit_paths.py` | 3.2 | 23 | ✅ 23/23 pass |
| `tests/test_rag_search.py` | 3.3 | 27 | ✅ 27/27 pass |
| `tests/test_tk_publish.py` | 3.5 | 22 | ✅ 22/22 pass |

**Total: 137 tests, 137/137 pasan.**

### test_toolkit_paths.py — Phase 3.2 detail (added 2026-04-05)

7 test classes with 23 individual tests covering:
1. `TestTkDiscoverConfig` (1 test) — discover_config finds PipelineConfiguration via mock SG, builds TkConfig with correct project_root
2. `TestTkResolvePathAsset` (4 tests) — maya_asset_publish, asset_alembic_cache resolve correctly; unresolved keys and unknown templates raise TkConfigError
3. `TestTkResolvePathShot` (3 tests) — maya_shot_work, nuke_shot_publish, flame_shot_batch (no alias) resolve correctly
4. `TestTkNextVersionEmpty` (2 tests) — returns 1 when parent dir missing or empty (uses tmp_path)
5. `TestTkNextVersionExisting` (3 tests) — returns max+1 with contiguous/non-contiguous versions; ignores unrelated files (uses tmp_path)
6. `TestTkTemplatesYmlParsing` (7 tests) — aliases expanded, shot_root/asset_root correct, all templates present, count=18, filter works, keys_raw loaded
7. `TestTkFallbackNoConfig` (3 tests) — returns None with empty SG result, queries correct entity/filters, passes custom pipeline_config_name

Supporting files:
- `tests/fixtures/templates.yml` — subset of tk-config-default2 (keys, 3 aliases, 18 templates for Maya/Nuke/Flame/Houdini/Hiero, strings)
- `tests/conftest.py` — extended with `templates_yml_path`, `templates_yml_raw`, `tk_config`, `mock_pipeline_config` fixtures

### test_rag_search.py — Phase 3.3 detail (added 2026-04-05)

6 test classes with 27 individual tests covering the hybrid RAG search pipeline:
1. `TestRagSearchBasic` (6 tests) — search returns (str, int), top results mention sg_find, formatted with ### headers, relevance in [0,100], n_results limits output, A12 cache returns same result
2. `TestRagSearchHydeExpansion` (6 tests) — _hyde_expand detects Toolkit domain (template, sgtk, pipeline config), REST domain (REST API, bearer, oauth), defaults to shotgun_api3; original query preserved in output
3. `TestRagSearchBm25Exact` (3 tests) — BM25 ranks sg_batch chunk highly for "sg_batch" query, BM25Okapi scores exact token highest, filter operators found
4. `TestRagSearchRrfFusion` (6 tests) — _rrf_fuse merges disjoint lists, boosts overlapping docs, preserves order with single ranker, handles empty inputs, k parameter affects scores, full integration search uses RRF
5. `TestRagSearchEmptyIndex` (3 tests) — empty ChromaDB returns message + relevance 0, missing index dir returns "not found" message, relevance exactly 0
6. `TestRagSearchNoMatch` (3 tests) — irrelevant query returns nearest neighbours, garbage query returns properly formatted output, single-char query doesn't crash

Supporting infrastructure:
- `tests/conftest.py` — extended with Phase 3.3 RAG fixtures:
  - `MINI_RAG_CORPUS` — 12 chunks across 3 API domains (shotgun_api3, toolkit, rest_api) + changelog filler
  - `_make_deterministic_embedding_fn()` — ChromaDB-compatible SHA-256 hash embedding (64-dim, no model download)
  - `mini_rag_corpus` — fixture returning deep copy of MINI_RAG_CORPUS
  - `rag_chroma_collection` — builds temporary ChromaDB with mini corpus in tmp_path
  - `rag_corpus_json` — writes corpus.json for BM25 in tmp_path
  - `rag_empty_collection` — empty ChromaDB for edge-case tests
  - `patch_rag_singletons` — patches search.py module singletons (_collection, _bm25, _bm25_docs, INDEX_DIR, CORPUS_PATH, _search_cache)

### test_tk_publish.py — Phase 3.5 detail (added 2026-04-05)

6 test classes with 22 individual tests covering the tk_publish workflow:
1. `TestPublishMode1Full` (5 tests) — Full Mode 1: creates PublishedFile, path uses template, response includes template/project_root, sets project, includes comment
2. `TestPublishMode2Explicit` (4 tests) — Mode 2: uses explicit path, error without path, no template in response, copies source file
3. `TestPublishAutoVersion` (3 tests) — auto-version starts at 1 when empty, returns max+1 with existing v001/v002, explicit version overrides auto
4. `TestPublishFindOrCreateType` (3 tests) — existing type reused (no create call), missing type created automatically, created type linked to PublishedFile
5. `TestPublishTaskLinking` (3 tests) — task linked when found, task omitted when not found, task queried with correct step filter
6. `TestPublishFileCopy` (4 tests) — file copied in Mode 1, no copy when no local_path, parent dirs created automatically, binary content preserved

Key design decisions:
- `publish_type` for Mode 1 tests uses "maya" (not "Maya Scene") to match template naming convention (`maya_asset_publish`). Mode 2 tests use "Maya Scene" since no template resolution occurs.
- All fixtures defined within test file (mock_sg_find_one dispatcher, mock_sg_create, patch_publish_deps, patch_publish_no_config). Reuses `tk_config` from conftest.py (Phase 3.2).
- AsyncMock dispatcher for sg_find_one extracts filter values to return context-appropriate entities.

---

## Bugs conocidos

- **`.env` con credenciales reales existe solo en local** — VERIFICADO: `.env` está en `.gitignore`, nunca fue commiteado, `.env.example` con placeholders ya existe. **No hay riesgo de exposición.**
- `paths.py` DEPRECATED eliminado en esta sesión (2026-04-05)

---

## Rutas hardcodeadas

### En código ejecutable (.py)

| Archivo | Ruta | Uso | Impacto |
|---|---|---|---|
| `src/fpt_mcp/qt/build_app_bundle.py` | `/tmp/fpt-console.log` | Console log | Bajo |
| `src/fpt_mcp/qt/claude_worker.py` | `~/.npm-global/bin/claude`, `~/.local/bin/claude` | Claude CLI search | Bajo (búsqueda) |

Todos los demás paths son relativos via `Path(__file__).parent`. **Buena práctica general**.

### En documentación (.md)

| Archivo | Rutas |
|---|---|
| `CLAUDE.md` | `~/Claude_projects/maya-mcp/`, `~/Claude_projects/vision3d/` |
| `src/fpt_mcp/docs/TK_API.md` | `/Users/Shared/FPT_MCP` (en ejemplos de código de la documentación RAG) |

### En configuración

| Archivo | Ruta | Notas |
|---|---|---|
| `.env` | URLs/keys de ShotGrid | Credenciales reales — solo en local, en `.gitignore` |

---

## Pendiente

- ~~Crear tests automatizados (prioritario)~~ → **EN PROGRESO**:
  - `tests/test_safety.py` creado (2026-04-05) — 49 tests para los 12 safety patterns, 49/49 pasan.
  - `tests/test_sg_operations.py` creado (2026-04-05) — 16 tests para Fase 3.1 (Mock SG API: sg_find, sg_create, sg_update, sg_batch, sg_delete + safety integration), 16/16 pasan.
  - `tests/test_toolkit_paths.py` creado (2026-04-05) — 23 tests para Fase 3.2 (Toolkit path resolution: discover_config, resolve_path asset/shot, next_version, templates.yml parsing, fallback), 23/23 pasan.
  - `tests/test_rag_search.py` creado (2026-04-05) — 27 tests para Fase 3.3 (RAG search: basic, HyDE, BM25, RRF, empty index, no match), 27/27 pasan.
  - `tests/test_tk_publish.py` creado (2026-04-05) — 22 tests para Fase 3.5 (tk_publish workflow: Mode 1 full, Mode 2 explicit, auto-version, find-or-create type, task linking, file copy), 22/22 pasan.
  - `tests/conftest.py` ampliado (2026-04-05) — fixtures reutilizables: mock_sg, patch_sg_client, sample_assets/shots/tasks/published_file_types/project, templates_yml_path, templates_yml_raw, tk_config, mock_pipeline_config, MINI_RAG_CORPUS, rag_chroma_collection, rag_corpus_json, rag_empty_collection, patch_rag_singletons.
  - `tests/fixtures/templates.yml` creado (2026-04-05) — subset de tk-config-default2: 18 templates, 3 aliases, keys con format_spec.
  - **Total: 137 tests, 137/137 pasan.** Fase 3 completa.
- Implementar distributed config (app_store, git descriptors) para Phase 2
- Documentar relación con FPT_MCP/ en README

---

## Hallazgo: Safety patterns no alcanzan desde tool functions

Durante la implementación de test_sg_operations.py se detectó que los safety patterns que incluyen el nombre del tool (`sg_find.*filters`, `sg_delete.*PublishedFile`) **no se disparan** desde las funciones `sg_find_tool` / `sg_delete_tool` porque el `params_str` serializado no incluye el nombre del tool. Los patterns funcionan correctamente cuando `check_dangerous()` recibe el string completo con contexto del tool. No se modificó código fuente — solo se documentó aquí para consideración futura.

---

## Última actualización: 2026-04-05 — test_tk_publish.py creado (22 tests, Fase 3.5, 22/22 pasan). Fase 3 completa. Total suite: 137/137 pasan
