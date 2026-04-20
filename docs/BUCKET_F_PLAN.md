# Bucket F — split `server.py` into focused modules

**Status**: Phase 1 (audit + plan) — complete and ready for review.
**Author**: Chat 45 main session (read-only audit, no code edits).
**Related memory**: `feedback_agent_file_safety.md` — server.py is off-limits
to subagents; all edits must land from the main session with `Edit` tool only
(never `Write`), re-reading before and after.

---

## 1. Why split

`src/fpt_mcp/server.py` is **1 677 lines** with 14 `@mcp.tool` decorators, 2
dispatchers (`fpt_bulk`, `fpt_reporting`), 6 ShotGrid tools, 2 Toolkit tools,
1 launcher, 3 RAG/session tools, 17 Pydantic input models, filter safety, and
the `FastMCP` boot. Four concrete problems today:

1. **Navigation cost** — finding a helper means scrolling through 1 700 lines.
2. **Agent blast radius** — subagents have silently emptied `server.py`
   before (Chat 31). A smaller file is a smaller mistake.
3. **Cohesion** — ShotGrid wiring, Toolkit wiring, RAG wiring, and launcher
   live together despite being orthogonal subsystems.
4. **Incremental refactor cost** — any future change that touches two
   subsystems re-reads the whole file. Splitting pays back from commit 1.

---

## 2. Hard constraints that shape the design

Each constraint removes a refactor path and forces a specific shape:

- **`install.sh` ast-parses `@mcp.tool`**. `install.sh` line 6 reads
  `src/fpt_mcp/server.py` via `ast.parse` to build the pre-approval list.
  **Every `@mcp.tool(name="...")` must stay in `server.py`**, or the
  installer stops picking up new tools.
- **`.concepts.yml` has ~30 invariants pointing at `src/fpt_mcp/server.py`**.
  Most check AST facts (decorator count, `_STRICT_CONFIG` presence,
  `_PROJECT_SCOPED_ENTITIES`, `SHOTGRID_PROJECT_ID`, filter-operator
  whitelist, reject-like / reject-matches). Each referenced symbol that
  moves requires a simultaneous `.concepts.yml` update (three-leg rule).
  Easier to keep hot symbols in `server.py`.
- **MCP `FastMCP` instance is module-global** — `@mcp.tool` decorators
  need the same `mcp` object. Two options: (a) keep decorators in
  `server.py` and import helpers from new modules, or (b) move decorators
  out and pass `mcp` as a parameter to a `register()` function in each
  module. Option (a) is simpler and keeps `install.sh` happy.
- **`MANDATORY WORKFLOW` prose** (lines 181-199) lives inside a
  `FastMCP(...)` constructor argument. Must stay where `FastMCP` is
  instantiated, i.e. `server.py`.

---

## 3. Target layout

```
src/fpt_mcp/
├── __init__.py
├── server.py              (slim — ~450 lines: imports, FastMCP boot,
│                           MANDATORY WORKFLOW text, 14 @mcp.tool decorators
│                           as thin wrappers that call helper modules,
│                           main() entry point)
│
├── safety.py              (existing, 171 lines — unchanged)
├── client.py              (existing, 274 lines — unchanged)
├── software_resolver.py   (existing, 284 lines — unchanged)
├── tk_config.py           (existing, 470 lines — unchanged)
│
├── models.py              (NEW — all 17 Pydantic input models +
│                           BulkAction / ReportingAction enums +
│                           _STRICT_CONFIG. ~280 lines.)
│
├── filters.py             (NEW — _validate_filter_triples,
│                           _VALID_FILTER_OPERATORS, _PROJECT_SCOPED_ENTITIES,
│                           _MAX_FILTER_DEPTH. ~140 lines.)
│
├── shotgrid.py            (NEW — sg_find_impl, sg_create_impl,
│                           sg_update_impl, sg_schema_impl,
│                           sg_upload_impl, sg_download_impl,
│                           _do_sg_delete, _do_sg_batch, _do_sg_revive.
│                           ~400 lines.)
│
├── reporting.py           (NEW — _do_sg_text_search, _do_sg_summarize,
│                           _do_sg_note_thread, _do_sg_activity. ~180 lines.)
│
├── toolkit_tools.py       (NEW — tk_resolve_path_impl, tk_publish_impl,
│                           _get_tk_config, _build_template_fields.
│                           ~300 lines.)
│
├── launcher.py            (NEW — fpt_launch_app_impl,
│                           _project_id_for_entity. ~150 lines.)
│
├── rag_tools.py           (NEW — search_sg_docs_impl, learn_pattern_impl,
│                           _model_can_write + RAG skipped warning helpers.
│                           ~180 lines.)
│
└── qt/                    (existing — unchanged)
```

### server.py shape after the split

```python
# server.py — SLIM orchestrator
from fpt_mcp.filters import _validate_filter_triples, _PROJECT_SCOPED_ENTITIES
from fpt_mcp.models import (
    SgFindInput, SgCreateInput, SgUpdateInput, SgSchemaInput,
    SgUploadInput, SgDownloadInput, TkResolvePathInput, TkPublishInput,
    BulkDispatchInput, ReportingDispatchInput, FptLaunchAppInput,
    SearchSgDocsInput, LearnPatternInput,
)
from fpt_mcp import shotgrid, toolkit_tools, launcher, rag_tools
from fpt_mcp.reporting import REPORTING_DISPATCH
from fpt_mcp.shotgrid import BULK_DISPATCH

mcp = FastMCP(
    name="fpt-mcp",
    instructions="""## MANDATORY WORKFLOW ...""",
)

@mcp.tool(name="sg_find")
async def sg_find_tool(params: SgFindInput) -> str:
    return await shotgrid.sg_find_impl(params)

@mcp.tool(name="sg_create")
async def sg_create_tool(params: SgCreateInput) -> str:
    return await shotgrid.sg_create_impl(params)

# ... 12 more @mcp.tool decorators, each a 1-2 line wrapper

def main():
    mcp.run()

if __name__ == "__main__":
    main()
```

Estimate: **~450 lines** (down from 1 677). `@mcp.tool` decorators retain
their `name="..."` kwargs so `install.sh` ast extraction keeps working.

---

## 4. Phasing

Each phase is ONE commit on `feat/bucket-f-<phase>` merged `--no-ff` into
`main`. Patch releases cut only at the end unless an intermediate release
is useful (I'd bundle into one `v1.6.0` bump at the end — it's a minor-bump
because of the file reorg, not a breaking API change).

### Phase 2a — `models.py` + `filters.py` (PILOT)

- **Why first**: pure data / pure logic, no side effects, no import cycles.
  Every other module will import from here, so bootstrapping `models.py`
  and `filters.py` first means the remaining phases are pure moves.
- **What moves**: all `class *Input(BaseModel)`, both enums
  (`BulkAction`, `ReportingAction`), `_STRICT_CONFIG`,
  `_validate_filter_triples`, `_VALID_FILTER_OPERATORS`,
  `_PROJECT_SCOPED_ENTITIES`, `_MAX_FILTER_DEPTH`.
- **What stays**: everything else in `server.py`. `server.py` imports back
  from `models` and `filters`.
- **`.concepts.yml` updates**: 5-6 invariants need new file paths
  (`_STRICT_CONFIG` mirror, filter-operator whitelist, entity-format regex,
  reject-like / reject-matches — these check file_pattern on server.py
  today; change to `src/fpt_mcp/models.py` or `src/fpt_mcp/filters.py`).
- **Test gate**: `pytest tests/` stays 369/369 green. `verify_concepts.py`
  returns 29/29 after the `.concepts.yml` path fixes.
- **Estimated diff size**: ~400 lines moved, ~5 lines added (imports).
  No behaviour change.

### Phase 2b — `launcher.py`

- **Why second**: smallest self-contained slice. `fpt_launch_app_tool`
  uses `software_resolver` (already a separate module) and has one
  private helper `_project_id_for_entity`. Low dependency surface.
- **What moves**: `fpt_launch_app_impl` (body of the current
  `fpt_launch_app_tool`), `_project_id_for_entity`.
- **What stays in `server.py`**: `@mcp.tool(name="fpt_launch_app")`
  decorator as a thin wrapper.
- **`.concepts.yml` updates**: none (no invariants currently point at
  the launcher body).
- **Estimated diff size**: ~175 lines moved.

### Phase 2c — `toolkit_tools.py`

- **What moves**: `tk_resolve_path_impl`, `tk_publish_impl`,
  `_get_tk_config`, `_build_template_fields`.
- **Imports back to server.py**: only the decorators + model types from
  `models.py`.
- **Risk**: `_build_template_fields` is ~50 lines and reads the SG client
  context. Verify no implicit module-level state leaks.
- **Estimated diff size**: ~300 lines moved.

### Phase 2d — `shotgrid.py` + `reporting.py`

- **What moves**: bodies of all 6 `sg_*_tool` decorators + all 6 `_do_sg_*`
  handlers (3 for bulk, 4 for reporting — but `_do_sg_delete` is shared
  between the direct tool flow and the bulk dispatcher, so it belongs in
  `shotgrid.py` and `reporting.py` imports from there).
- **Dispatch tables**: `BULK_DISPATCH: dict[BulkAction, Callable]` in
  `shotgrid.py`, `REPORTING_DISPATCH: dict[ReportingAction, Callable]`
  in `reporting.py`. `fpt_bulk` and `fpt_reporting` decorators in
  `server.py` look up the callable and invoke.
- **Risk**: biggest slice. Many helpers cross-reference. Keep first
  iteration purely mechanical (rename-and-move), no simplification.
- **`.concepts.yml` updates**: verify that `bulk_dispatcher_actions` and
  `reporting_dispatcher_actions` invariants still point at the enum
  definitions (which moved to `models.py` in Phase 2a — already handled).
- **Estimated diff size**: ~580 lines moved.

### Phase 2e — `rag_tools.py` + cleanup

- **What moves**: `search_sg_docs_impl`, `learn_pattern_impl`,
  `_rag_skipped_warning`, `_model_can_write`, `_get_current_model`,
  `_get_config`, `_tok`, `_rating`.
- **Stays in server.py**: `session_stats_tool` (wafer-thin, ~40 lines) —
  not worth its own module.
- **Cleanup**: remove unused imports in the slim `server.py`, tidy
  `__init__.py`, ensure `python -m fpt_mcp.server` still boots.
- **Estimated diff size**: ~220 lines moved + ~50 lines of import cleanup.

### Phase 2f — release `v1.6.0`

- Minor bump (reorg is backwards-compatible for library users; MCP tool
  surface is untouched).
- CHANGELOG entry listing each new module.
- Tag + `gh release create --generate-notes`.
- `.concepts.yml` adds one new concept: `server_py_line_budget` with a
  `claim_verifies` that `wc -l src/fpt_mcp/server.py < 600` — prevents
  a future regression where someone slowly refills `server.py`.

---

## 5. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Import cycle (e.g. `shotgrid.py` → `models.py` → back) | Strict layering: `models.py` and `filters.py` import NOTHING from other fpt_mcp modules (except `client.py` for types). Every other module imports FROM them, never the reverse. |
| `install.sh` breaks (ast.parse missing tools) | Decorators stay in `server.py`. Phase 2a–2e preserve every `@mcp.tool(name=...)`. Sanity check: `bash -n install.sh && grep -c "mcp.tool" src/fpt_mcp/server.py` before each commit. |
| `.concepts.yml` invariants drift | Each phase updates `.concepts.yml` in the SAME commit. Three-leg rule. Pre-commit hook catches misses. |
| Pydantic model discovery changes | No — FastMCP binds models at decorator evaluation, which still happens when `server.py` is imported. Moving a model definition to another file is transparent to FastMCP as long as `server.py` imports the class. |
| Subagent silently truncates `server.py` | **No subagents this session.** Every edit is main-session `Edit` tool with read-before and read-after. |
| Tests cover only the tool wrappers and not the new impls | Keep test inputs identical — `pytest tests/` calls the `*_tool` functions which now delegate. Existing tests cover the impls transitively. Post-split, consider direct unit tests for each `*_impl` in a future session. |
| Pre-commit hook tries to block the release commit (soft-launch quirk) | Soft-launch stays `strict: false` through Bucket F. Flip to strict only AFTER Bucket F lands, so the two refactors don't intersect. Re-evaluate strict flip date (originally ≥ 2026-05-01). |

---

## 6. Acceptance criteria per phase

Each phase must pass ALL of these before the commit is considered done:

1. `python -m pytest tests/ -p no:warnings` — exact same pass count as the
   baseline before the phase started (369/369 today).
2. `python scripts/verify_concepts.py` — same invariant count passing
   (29/29 today), updated to the new file paths as needed.
3. `bash -n install.sh` — syntax OK.
4. `grep -c "@mcp.tool" src/fpt_mcp/server.py` — exactly **14** (no tools
   gained or lost).
5. `python -c "from fpt_mcp.server import mcp; print(len(mcp._tools))"`
   (or equivalent) — same tool count registered at import time.
6. Manual smoke test: `python -m fpt_mcp.server` starts without tracebacks
   and prints the MCP banner.

---

## 7. What is NOT in scope for Bucket F

To keep the diff reviewable, these are explicitly deferred:

- Adding new tests (move tests untouched, don't extend them).
- Renaming any function or class (mechanical move only).
- Simplifying logic inside the moved code (resist the temptation).
- Touching `qt/`, `client.py`, `safety.py`, `tk_config.py`,
  `software_resolver.py` — these are already well-separated.
- Changing the MCP tool surface (same 14 tools, same parameters, same
  behaviour).
- Renaming `_do_sg_*` to `do_sg_*` (public-ish) — keep underscores.
- Adding type hints where none exist today.

---

## 8. Expected outcome

- `server.py` ~450 lines (from 1 677).
- 7 new focused modules, each ≤ 400 lines.
- 29/29 invariants PASS post-refactor.
- 369/369 tests PASS post-refactor.
- Zero change to tool behaviour.
- One `v1.6.0` release (minor bump because the reorg is a noticeable
  improvement, though not breaking for MCP clients).
- A new invariant (`server_py_line_budget`) that prevents regression.

---

## 9. Go / no-go recommendation

**Recommend**: proceed with **Phase 2a only** in the next session, evaluate
the result before committing to 2b–2f. Phase 2a is the highest information
value (validates the pattern, proves import layering works, touches
`.concepts.yml` non-trivially) at the lowest risk (pure data types, no
behaviour).

If Phase 2a goes smoothly (all 6 acceptance criteria green), 2b–2e are
lower-risk repeats of the same pattern. If 2a uncovers an unexpected
coupling, we stop and re-plan before attempting the larger slices.
