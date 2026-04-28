# Changelog

All notable changes to **fpt-mcp** are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.9.2] — 2026-04-28

### Fixed
- `qt/claude_worker.py` — Vision3D progress regression. Since the
  March 2026 design (commits `80cc581` + `10e32dc`), the "thinking"
  bubble's incremental progress lines depended on Claude **echoing**
  the `new_log_lines` array from each `maya_vision3d(action='poll')`
  result back as text. Newer Opus 4.7 + the Chat 40 reasoning-hardening
  env vars (`CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1` +
  `CLAUDE_CODE_EFFORT_LEVEL=max`) make the model reason silently
  between poll calls, swallowing the lines. Result: only the dispatcher
  action label "Polling Vision3D progress..." appeared, repeated
  uninformatively, while the actual `[1/5] Loading shape pipeline...`
  / `═══ PHASE 1/2: SHAPE GENERATION ═══` etc. were lost.
  The worker now intercepts `user`-role stream-json events carrying
  `tool_result` content, parses any JSON payload that includes a
  `new_log_lines` field (the Vision3D poll contract), and emits each
  line as a progress event directly — no dependency on the model
  choosing to echo. Model-agnostic (works with Anthropic and Ollama
  backends), strictly defensive (malformed JSON or missing field →
  silent skip), additive (does not change the existing text_delta
  path; if the model also echoes, both paths emit and the duplicate is
  visually capped at 12 visible lines anyway).

## [1.9.1] — 2026-04-28

### Fixed
- SYSTEM_PROMPT — three corrections after in-vivo Chat 49 user feedback
  on the v1.9.0 redesign:
  - **Hardcoded Vision3D hostnames removed**. v1.9.0 introduced
    `http://localhost:8000` and `http://glorfindel:8000` as labelled
    examples in the Step 0b URL question. This violated the
    MASTER_HISTORY policy ("Vision3D URL is per-session, runtime-only,
    zero persistence; no hardcoded defaults, no whitelist"). v1.9.1
    reverts to showing only the URL FORMAT (`http://<hostname>:<port>`)
    plus the per-session `suggested_default` from the
    `vision3d_url_required` error payload (only present when
    `GPU_API_URL` is set in env). The
    `test_no_fabricated_urls_in_examples` test reverted to the strict
    hostname-blocklist enforcement.
  - **Method menu is now filtered by target**. Picking `vision3d` in
    Step 0 now hides the `manual` keyword from Step 4 (the user
    already chose generative; offering manual again is contradictory).
    Picking `maya` in Step 0 hides the version / texture / description
    / prompt keywords AND the AI Quality block entirely — Maya goes
    straight to a free-text "what do you want me to model?" prompt
    with the references inline as visual guides.
  - **Maya is no longer labelled "no AI"**. The Step 0 description now
    reads "AI-assisted modeling (LLM builds geometry with primitives /
    transforms / materials guided by references — NOT generative)".
    The Step 5 bullet renamed from "Direct Maya modeling" to "Maya
    assisted modeling" and the Step 4 maya branch is described as
    "AI-ASSISTED, not 'no AI'".

### Changed
- `tests/test_system_prompts.py::test_no_fabricated_urls_in_examples`
  re-tightened to the original strict contract (forbid
  `glorfindel:8000`, `localhost:8000`, `127.0.0.1:8000` literals in
  the prompt) AND keep the prose checks for "never fabricate / never
  auto-select / always ask".

## [1.9.0] — 2026-04-28

### Changed
- **SYSTEM_PROMPT redesign — target/server selection FIRST** (Chat 49,
  user-requested). The 3D-creation workflow now asks the user *where*
  to generate BEFORE searching references or rendering the quality
  block. Two new mandatory steps replace the old silent health probe:
  - **Step 0 — Target selection**: ask "maya | vision3d?" once. If
    `maya` → skip all Vision3D steps and the quality block entirely.
    If `vision3d` → continue to Step 0b. FAST PATH still short-circuits
    when the user's first message names the target.
  - **Step 0b — Server selection**: only on `vision3d`. Probe
    `maya_vision3d(action='health')`; on `vision3d_url_required`,
    ask the user for the URL with labelled hints
    (`http://localhost:8000` → MPS, `http://glorfindel:8000` → CUDA).
    Validate format, call `select_server`, then `health` again to
    verify AND learn `device` (mps/cuda/cpu) for Step 4.
  Step 4's quality block is now device-aware: on CUDA/CPU the labels
  show turbo for low/medium; on MPS they show fast (with an explicit
  "turbo unavailable on Apple Silicon → auto-resolves to fast" note).
  This stops the prior misleading flow where Mac users saw "low —
  turbo model" labels but the server (vision3d v1.6.7+) silently
  downgraded to fast. The legacy "Vision3D URL gate" inside Step 5 is
  removed (the gate now runs once in Step 0b).
- `tests/test_system_prompts.py` — three structural tests adapted to
  the new contract:
  - `test_quality_block_has_both_device_branches` (renamed from
    `test_quality_block_identical_both_prompts`): both variants must
    document both CUDA and MPS branches; byte-identical enforcement
    relaxed because the device-conditional structure is intentional.
  - `test_quality_block_contains_required_fields`: now scans the
    whole prompt for required terms (turbo+fast+all octree/steps/faces
    values) instead of one regex-captured 4-bullet block.
  - `test_no_fabricated_urls_in_examples`: the protective intent
    (no LLM-fabricated defaults) is now enforced via prose checks for
    "never fabricate / never auto-select / always ask the user" rather
    than a literal hostname blocklist; this allows Step 0b to surface
    `localhost:8000` and `glorfindel:8000` as labeled examples while
    preserving the never-auto-select contract.
- `CLAUDE.md` — Section 4 (SYSTEM_PROMPT) updated with Step 0,
  Step 0b, the device-aware quality block layout, and a "Step 1
  (legacy)" note pointing readers to where the old probe moved.

## [1.8.0] — 2026-04-28

### Added
- `tk_publish` — path-based context derivation. `entity_type`, `entity_id`,
  and `step` are now Optional when `local_path` points to a Toolkit-managed
  file. New `context_from_path()` (in `tk_config.py`) reverse-matches the
  path against `templates.yml` to extract Asset/Shot/Step tokens, then
  resolves `entity_id` via a single `sg_find` by code. Zero `sg_find`
  round-trips needed before calling `tk_publish` from a saved workfile.
  (commit `223250d`)
- `src/fpt_mcp/tk_config.py` — `context_from_path()` and `_match_template_path()`
  for reverse template matching (Toolkit token extraction).
- `src/fpt_mcp/server.py` — `_resolve_step_short_name()` canonicalises
  Step names (e.g. `model → MDL`).
- `src/fpt_mcp/qt/app.py` — `--entity-code` CLI argument, `_resolve_entity_code`
  + `_enrich_with_entity_code` helpers, 4-hop dirname fix.
- `src/fpt_mcp/qt/chat_window.py` — `entity_code` parameter and
  `update_context` propagation.
- `src/fpt_mcp/qt/system_prompts/` — FAST PATH and WORKFILE HANDLING
  PATH X/Y instructions for path-based publish.
- `tests/` — 7 new tests (`context_from_path` ×4, path-derived publish ×3).
  Total: 402 passed, 3 skipped.

### Changed
- `src/fpt_mcp/qt/claude_worker.py` — default model bumped to
  Opus 4.7; alternates: Sonnet 4.6, Haiku 4.5.
- `src/fpt_mcp/models.py` — `TkPublishInput.entity_type/entity_id/step`
  made Optional with auto-derivation documented in field descriptors.
- `src/fpt_mcp/toolkit_tools.py` — path derivation block at the start
  of `tk_publish_impl`; uses `effective_*` variables throughout to
  preserve precedence (explicit param > derived value).

## [1.7.1] — 2026-04-22

### Added
- `scripts/invariant_types.py` — `_write_subset` handler registered in
  WRITERS (Phase C + D, Chat 48). Covers two shapes:
  - `b_source.type: anchor_list` (no `item_pattern`) → appends missing
    items as bullets inside the concept block.
  - `b_source.type: file_regex_matches` with YAML opt-in
    `b_source.writer.line_template` → appends template-formatted
    lines after the last existing regex match (default) or at
    end_of_file.
  Other shapes report `WRITER UNSUPPORTED`. Enables `/propagate-change`
  Path A to auto-fix the common subset-drift pattern.
- `.github/workflows/ci.yml` — Codecov coverage upload step
  (`codecov/codecov-action@v4`), gated to `matrix.python-version ==
  '3.12'` so the upload runs once per PR. `fail_ci_if_error: false`
  so Codecov outages don't block merges.

### Fixed
- `scripts/invariant_types.py` — `version_match` handler now honors
  opt-in `tolerate_release_in_progress: true`. When set, a drift of
  the form `a == CUT_RELEASE_VERSION != b` is tolerated so
  `cut-release.sh` can commit a pyproject bump before the matching
  git tag exists. Also applied to `.concepts.yml` on the
  `pyproject_matches_latest_tag` invariant to unblock the release
  flow under strict mode (was blocking in Chat 48 until fixed).

## [1.7.0] — 2026-04-22

### Added
- `src/fpt_mcp/suggestions.py` — O3 `next_suggested_actions` Phase 1
  (Chat 47). Stub module with empty `SUGGESTION_RULES` registry +
  `maybe_annotate_with_suggestions` helper + kill switch
  `FPT_MCP_DISABLE_SUGGESTIONS=1`. Cap of 3 suggestions per response.
  Wired into `sg_find` as the first consumer.
- `src/fpt_mcp/suggestions.py` — Phase 2 (Chat 47): 5 active rules
  covering `sg_find` (Asset+image → sg_download + maya_vision3d;
  Task/Version → fpt_reporting activity), `sg_download` (image-ext →
  maya_vision3d generate_image), `tk_publish` (success → note_thread +
  sibling Shot discovery), `fpt_bulk` (soft-delete → revive offer).
  `default.txt` and `qwen.txt` prompts teach the LLM to surface hints
  as a soft aside once the user's explicit request is satisfied.
- `.concepts.yml` — Phase 3 (Chat 47): `next_suggested_actions_contract`
  concept with `every_rule_is_wired` invariant (ast_dict_keys
  `SUGGESTION_RULES` ⊂ regex capture of
  `maybe_annotate_with_suggestions("<tool>", …)` call-sites).
  Pre-commit fails if a rule is registered without wiring.
- `docs/O3_NEXT_SUGGESTED_ACTIONS.md` — design doc for the feature
  (Chat 46). Covers motivation, response schema, rules registry
  (static Python dict v1 → YAML v2), candidate rules, invariant, and
  4-phase implementation sequence.
- `.github/workflows/ci.yml` — GitHub Actions CI workflow. Four
  blocking jobs: pytest (3.10/3.11/3.12 matrix, Qt forced to
  `QT_QPA_PLATFORM=offscreen`), ruff lint, mypy, verify_concepts.
  Pytest coverage reported inline.
- `.github/workflows/pr-review.yml` — automated Claude PR review
  (`anthropics/claude-code-action@v1`). Byte-identical across the 4
  ecosystem repos. Uses `claude_code_oauth_token`. Requires the
  Claude Code GitHub App installed on the repo + workflow permission
  `id-token: write` + `--model claude-sonnet-4-6` pin so the OAuth
  token (Sonnet-scoped on Max/Pro) works against the default-Opus
  action.
- `scripts/verify_concepts.py --write` — WRITER MODE (Chat 46).
  Requires the triple flag `--accept-current-as-truth
  --i-reviewed-diff --write`. Dispatches to per-type writers in
  `invariant_types.py::WRITERS`. Currently supports `tool_count` and
  `review_expiry`; other types report `WRITER UNSUPPORTED`. No
  auto-commit.
- `scripts/cut-release.sh` — ecosystem-shared release orchestrator.
  Validates clean tree + semver arg + non-empty `[Unreleased]`, edits
  CHANGELOG + `pyproject.toml`, commits with
  `CUT_RELEASE_VERSION=X.Y.Z` so the `changelog_tag_sync` invariant
  tolerates the transient pre-commit drift, tags, pushes, and creates
  a GitHub release. Byte-identical across the 4 MCP-ecosystem repos.
- `scripts/invariant_types.py` — `changelog_tag_sync` handler
  replaces `changelog_tag_coherence`. Release-in-progress tolerance
  anchored to env `CUT_RELEASE_VERSION` OR `pyproject.toml`'s
  `version` field.
- `scripts/invariant_types.py` — `version_match` canonical (Chat 48)
  honors opt-in `tolerate_release_in_progress: true`. Lets
  `cut-release.sh` commit a version bump before the matching git
  tag exists under strict mode.
- `scripts/verify_concepts.py` — `ci_skip: true` flag on individual
  invariants + auto-skip of `review_expiry` under `GITHUB_ACTIONS`.

### Changed
- `.concepts.yml` — `strict: false → true`. The pre-commit hook now
  blocks commits on any unresolved invariant drift instead of only
  reporting it. Ecosystem-wide flip on 2026-04-20 (Chat 46).
- CI pipeline cleanup (Chat 47): ruff baseline cleared with
  `[tool.ruff.lint.per-file-ignores]` for server.py + conftest.py
  (re-export hub pattern — ruff `--fix` was silently deleting used
  re-exports until the ignore was added). mypy baseline cleared via
  `[tool.mypy]` block with `shotgun_api3` `follow_imports=skip`
  override to handle over-strict `BaseEntity` TypedDict stubs. Both
  jobs flipped to blocking.
- `.concepts.yml` — `every_rule_is_wired` regex widened `[a-z_]` →
  `[a-z0-9_]` (Chat 48) so tool names with digits (none today but
  future-proof) are captured.

### Fixed
- `install.sh` — removed two unused-variable shellcheck warnings
  (`exit_code` local, `MCP_ARGS` JSON) (Chat 46).
- `.github/workflows/pr-review.yml` — added `id-token: write` workflow
  permission (Chat 48). Without it the action errored with "Unable to
  get ACTIONS_ID_TOKEN_REQUEST_URL env variable" in 3 retries.
- `.github/workflows/pr-review.yml` — pinned `--model claude-sonnet-4-6`
  via `claude_args` (Chat 48). OAuth tokens from `claude setup-token`
  are scoped to Sonnet on Max/Pro; the action's default model (Opus
  after v1.0.100) returned `401 Invalid bearer token` against those
  credentials (see anthropics/claude-code-action#584).

## [1.6.0] - 2026-04-20

### Changed
- **Bucket F refactor — `server.py` split into focused subject modules.**
  No behaviour change; MCP tool surface is identical (14 tools, same
  parameters, same returns). Goal: reduce the orchestrator from 1677
  lines to a slim dispatcher (final: 648 lines, -61 %).

  New modules under `src/fpt_mcp/`:
  - `filters.py` (Phase 2a) — `_validate_filter_triples`,
    `_VALID_FILTER_OPERATORS`, `_PROJECT_SCOPED_ENTITIES`,
    `_MAX_FILTER_DEPTH`.
  - `models.py` (Phase 2a) — all 17 Pydantic input models + 2 enums +
    `_STRICT_CONFIG`.
  - `launcher.py` (Phase 2b) — `fpt_launch_app_impl` +
    `_project_id_for_entity`.
  - `toolkit_tools.py` (Phase 2c) — `tk_resolve_path_impl` +
    `tk_publish_impl`.
  - `shotgrid.py` (Phase 2d) — `sg_*_impl` × 6 + `_do_sg_delete` +
    `_do_sg_batch` + `_do_sg_revive`.
  - `reporting.py` (Phase 2d) — `_do_sg_text_search`, `_do_sg_summarize`,
    `_do_sg_note_thread`, `_do_sg_activity`.
  - `rag_tools.py` (Phase 2e) — `search_sg_docs_impl`,
    `learn_pattern_impl`.

  `@mcp.tool` decorators stay in `server.py` as thin wrappers so
  `install.sh` ast-extraction continues to find them and the
  `mcp_tool_inventory` invariant stays green. `_stats` bookkeeping lives
  in wrappers (impls are pure) so `test_telemetry`'s AST scan of
  `server.py` still sees every increment. Impls lazy-import test-patched
  symbols (`get_sg`, `sg_find`, `_get_tk_config`, etc.) from
  `fpt_mcp.server` so existing test patches keep intercepting.

### Added
- `tests/test_server_line_budget.py` — regression guard asserting
  `src/fpt_mcp/server.py` stays under 700 lines. Any future growth back
  toward the old 1677-line state fails this test with a pointer to the
  right extraction target module.
- Re-exports in `server.py` for backwards compatibility with tests that
  import `_project_id_for_entity`, `_do_sg_batch`, `_do_sg_delete`,
  `_do_sg_revive`, `_do_sg_text_search`, `_do_sg_summarize`,
  `_do_sg_note_thread`, `_do_sg_activity` from the `fpt_mcp.server`
  namespace.
- `docs/BUCKET_F_PLAN.md` (landed before the refactor in commit
  `18073ae`) documents the phased approach used to land this.

## [1.5.2] - 2026-04-20

### Fixed
- `README.md` — the `qwen3.5-mcp` setup block referenced `Modelfile.qwen35mcp`
  as if it existed in the repo, but no Modelfile is tracked here. Replaced
  with an inline heredoc (matching the MODEL_STRATEGY.md recipe) so the
  documented command works in a fresh clone.

### Added
- `scripts/verify_concepts.py` — `--accept-current-as-truth` + `--i-reviewed-diff` double-flag escape hatch (REPORT MODE ONLY). When both flags are passed, the runner inspects every failing invariant and prints a human-readable "would update \<mirror\>" line describing what a hypothetical writer mode would change, then exits 0 without touching any file. Single-flag usage is rejected with exit code 2 by design — the double-flag requirement prevents accidental drift acceptance. Intended for repos that drifted while dormant and need a one-shot review before flipping `strict: true`. Writer mode is deferred to a future pass with explicit user sign-off. Chat 44 ultraplan Q5.

## [1.5.1] - 2026-04-20

### Fixed
- **`ollama_mac` `num_ctx` preflight** (`src/fpt_mcp/qt/claude_worker.py`).
  Ollama's Anthropic-compatible endpoint (`/v1/messages`) silently ignores
  Modelfile `num_ctx` and defaults to 4096 tokens, truncating every Mac-
  local inference spawned from the Qt console. Added a module-level
  `_preload_ollama_mac_model(model, url, num_ctx)` helper that POSTs an
  empty-prompt request to `/api/generate` with `options.num_ctx=8192`,
  `keep_alive="10m"`, `stream=False` before `subprocess.Popen(claude ...)`
  runs. Uses `urllib.request` (stdlib — no new dependency). Non-fatal on
  failure: logged, not raised, so the Qt console still spawns `claude`
  even if the daemon is briefly unreachable. Only `ollama_mac` is wired;
  LAN `ollama` (operator-managed) and `ollama_cloud` (cloud runners
  manage context) are deliberately excluded. New `OLLAMA_MAC_NUM_CTX =
  8192` constant tuned for 4B/9B models on Mac 24 GB unified memory.
  Ships with 5 new tests in `tests/test_ollama_mac_preflight.py`
  (constant pinning, payload shape, custom URL, transport error
  swallow, timeout swallow). Parity with flame-mcp's existing Option A
  fix (`hooks/flame_mcp_bridge.py::_preload_ollama_model`).

### Added
- **`github_release_per_tag` invariant** (`.concepts.yml`). Every `vX.Y.Z`
  tag from `v1.0.0` onwards must have a corresponding published GitHub
  Release (pre-1.0 tags excluded — `v0.x` was pre-release noise). The
  invariant uses `command_lines` on `git tag --list 'v*'` vs
  `gh release list --limit 200`. Backfilled missing GitHub releases for
  `v1.0.0` and `v1.1.0` in the same commit to land at 29/29 green.
  Soft-launch drift only — matches the repo's existing `strict: false`.
- **`ollama_preflight_parity` invariant** (`.concepts.yml`). Pins that
  the `ollama_mac` branch of `claude_worker.py` continues to call
  `_preload_ollama_mac_model(...)` before spawning `claude`; a future
  refactor that silently drops the preflight will fail `verify_concepts`.

## [1.5.0] - 2026-04-20

### Added
- Cross-cutting concept registry (`.concepts.yml`) with 17 load-bearing
  invariants covering: MCP tool inventory (code ↔ README ↔ install.sh ↔
  CLAUDE.md), `fpt_bulk` / `fpt_reporting` dispatcher action sets, Pydantic
  `_STRICT_CONFIG` extra=forbid guard, `_VALID_FILTER_OPERATORS` whitelist
  rejecting hallucinated `like` / `matches`, `_PROJECT_SCOPED_ENTITIES` +
  `SHOTGRID_PROJECT_ID` scoping, Vision3D zero-persistence policy (no
  `vision3d_servers` field anywhere, no hardcoded defaults), Qt reasoning
  hardening env injection (`CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1`,
  `CLAUDE_CODE_EFFORT_LEVEL=max`), `no DERIVED_TEMPLATES` (Chat 43),
  tk-multi-launchapp command-naming docs (Chat 40), pyproject ↔ latest
  tag coherence, single install script at root, release cadence
  (`commits_since_tag`), CHANGELOG ↔ tag bidirectional, and ecosystem
  `review_expiry` oracle against `~/Projects/.external_versions.yml`
  (anthropic_models 14d, ollama_local_models 30d, shotgrid_api 90d).
- `scripts/invariant_types.py` + `scripts/verify_concepts.py` (verbatim
  copy from flame-mcp with a small `name_kwarg` extension for
  `ast_decorator_functions` so `@mcp.tool(name="sg_find")` resolves to the
  canonical public name instead of the `sg_find_tool` Python identifier).
- `.pre-commit-config.yaml` invoking `verify_concepts.py` on every commit
  (soft-launch `strict: false` — drifts reported but NOT blocking).
- `install_sh_tools_list` anchor on the `TOOLS = [...]` block inside
  `install.sh`, and `mcp_tool_count` + `mcp_tool_table` +
  `fpt_bulk_actions` + `fpt_reporting_actions` anchors in `README.md` and
  `CLAUDE.md` for machine-checkable bidirectional invariants.

### Changed
- Updated CLAUDE.md section 6 permissions to match actual maya-mcp dispatcher tool names
- Updated README to reflect dispatcher pattern and install.sh as primary installer
- `src/fpt_mcp/config.example.json` — removed `glorfindel` hardcoded LAN host
  and added an `_comment` field documenting that `ollama_url` is REQUIRED
  when `backend="ollama"` (closes Chat 43 pending item #5).
- `pyproject.toml` version bumped from the long-stale `0.1.0` placeholder
  to `1.5.0` to match the release cadence documented in CHANGELOG / git
  tags (closes a Chat 43 drift and a `pyproject_matches_latest_tag`
  invariant simultaneously).

### Added (earlier, rolled into this release)
- `install.sh --doctor` subcommand for environment health checks
- Bucket E structural tests for tool labels, Pydantic models, and trust gates
- This CHANGELOG.md

## [1.4.2] - 2026-04-14

### Fixed
- Hardened reasoning pipeline on Qt console Claude subprocess
- Documented tank auth and bundle_cache prerequisites for launcher

## [1.4.1] - 2026-04-14

### Fixed
- Launcher builds version-specific tank command name (`<app>_<version>` preference)

## [1.4.0] - 2026-04-14

### Added
- `fpt_launch_app` MCP tool: launch a DCC scoped to a ShotGrid entity with OS-first
  discovery and Toolkit tank routing
- `software_resolver` module for OS-first application discovery

### Fixed
- `load_dotenv(override=True)` to beat stale parent env in client

## [1.3.0] - 2026-04-14

### Added
- Bucket E structural regression suite (20 tests) for SYSTEM_PROMPT invariants
- Placeholder `.env` credential detection in 3 defense layers
- Backend-specific SYSTEM_PROMPT variant for Qwen (40% smaller, same workflow)
- RAG soft-warning, missing telemetry, and filter list validator
- Safety module catches hallucinated status codes and bare integer entity refs
- RAG cache-hit telemetry and HyDE query expansion sanitization
- Trust gate runtime model for `learn_pattern` and `tk_publish` guard
- `Asset.description` as text-to-3D fallback with user-awareness rules
- Pre-approved MCP tools in `install.sh`

### Changed
- Extracted SYSTEM_PROMPT variants to standalone text files
- Dispatch pattern reduces visible tools from 18 to 13 for small LLMs
- Pinned `sentence-transformers` and `pydantic` to safe major ranges
- Relative sibling paths in `.mcp.json` for cross-machine portability

### Fixed
- Qt console uses maya-mcp dispatcher API and handles `vision3d_url_required` correctly
- Removed fabricated glorfindel hostname from Vision3D URL prompt
- Deferred Vision3D URL question with adaptive method bullets
- Disambiguated method options and compact markdown rendering
- QA review follow-ups across Buckets B, C, D (VRAM math, defensive backend match)
- Deterministic RRF tiebreaker via stable secondary sort
- Scrubbed Anthropic env vars when switching to Claude backend
- Removed deprecated dead code and fixed `.gitignore` drift
- Added `cwd` to Popen and updated `.mcp.json` paths
- Added `ulimit` warning in conftest.py for ChromaDB file descriptor exhaustion
- Generic agnostic "thinking" verbs in Qt console (no real process leakage)
- Action-aware progress labels for maya-mcp dispatcher tools

## [1.2.0] - 2026-04-07

### Added
- MODEL_STRATEGY.md with Ollama setup, Modelfile, and KEEP_ALIVE config
- Ollama as optional prerequisite in READMEs and `install.sh`
- Dispatch pattern: reduce visible MCP tools from 18 to 13 for small LLMs

## [1.1.0] - 2026-04-07

### Added
- Multi-backend support: Ollama and Anthropic model selection, Qt menu, `config.example.json`
- `app_store` and `git` descriptor resolution in `tk_config.py`
- Comprehensive test suite (137 tests: sg_ops, toolkit_paths, rag_search, safety, tk_publish)
- `install.sh` automated installer with venv, deps, RAG index, MCP registration
- `.mcp.json` configuration for Claude Desktop and Claude Code
- Ecosystem section with cross-repo links in README

### Changed
- Expanded documentation: Features, Usage, Self-Learning, Token Tracking, Requirements,
  Project Structure, Troubleshooting sections

### Fixed
- Replaced deprecated `get_event_loop` with `asyncio.run` in tests (Python 3.12+)
- Removed `.DS_Store` from tracking, added to `.gitignore`
- Fixed chromadb version range and `asyncio.run()` in `test_tk_publish`

## [1.0.0] - 2026-03-31

### Changed
- Translated all Spanish content to English (i18n)
- Removed pipeline-specific templates; generic publish path resolution
- Expanded RAG corpus to 311 chunks with complete SG_API, TK_API, REST_API reference

### Added
- 6 direct SG API tools: `sg_find`, `sg_create`, `sg_update`, `sg_schema`,
  `sg_upload`, `sg_download`

## [0.3.0] - 2026-03-30

### Added
- RAG anti-hallucination engine with ChromaDB semantic + BM25 lexical + HyDE + RRF fusion
- Safety module with 12+ regex patterns detecting dangerous operations
- Toolkit integration (`tk_resolve_path`, `tk_publish`) with dynamic PipelineConfiguration
- Granular Vision3D workflow with poll labels in Qt console
- Detailed quality presets in 3D creation prompt (low/medium/high/ultra)
- Conversation history for multi-turn context in Qt console
- Asset-creation workflow skill with reference discovery
- Text-to-3D option in system prompt
- Real-time progress log in console thinking bubble

### Fixed
- `tk_resolve_path`: `next_version_number` expects `Path`, not 4 strings
- Thumbnail URL handling in `sg_download_attachment`
- Full preset parameters shown in console

## [0.2.0] - 2026-03-27

### Added
- Native Qt console with `fpt-mcp://` protocol handler
- HTTP transport with stateless JSON mode, proxy, CORS
- Claude Code CLI integration via AMI console
- `launchd` service plists for macOS

### Fixed
- Working directory in setup script for `.env` loading
- MCP HTTP transport: stateless JSON mode, proxy, CORS

## [0.1.0] - 2026-03-27

### Added
- Initial MCP server for Autodesk Flow Production Tracking (ShotGrid) with 8 tools
- stdio transport for Claude Desktop and Claude Code

[Unreleased]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.4.2...HEAD
[1.4.2]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.4.1...v1.4.2
[1.4.1]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v0.3.0...v1.0.0
[0.3.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/abrahamADSK/fpt-mcp/releases/tag/v0.1.0
