# Changelog

All notable changes to **fpt-mcp** are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Qt console runs read-only (recording-safe)** — the spawned `claude`
  subprocess is now launched with `--disallowedTools Edit Write MultiEdit
  NotebookEdit Bash`, so it can no longer modify the repository (it had
  self-edited `shotgrid.py` mid-session). MCP tools and Read stay available, so
  the demo pipeline is unaffected. Code-improvement ideas are captured, not
  applied: the agent emits `@@SUGGESTION@@ <title> :: <detail>` lines that
  `claude_worker.capture_suggestions` appends to the git-ignored
  `CONSOLE_IMPROVEMENTS.md` backlog (for a later dev session / PR) and strips
  from the reply. Covered by `tests/test_suggestion_capture.py`.

### Fixed
- **Console mirrors the user's language per message** — the spawned `claude`
  subprocess inherited the global `CLAUDE.md` "Spanish by default" bias and
  replied in Spanish to English orders. The console system prompts now carry an
  explicit LANGUAGE directive that overrides any inherited default and re-detects
  the latest message's language every turn (`default.txt` + `qwen.txt`).
- **Float fields accept a JSON integer** — ShotGrid rejects an integer sent to a
  Float-typed field (e.g. `Cut.fps = 25`) with `expected [BigDecimal, Float,
  NilClass] ... but got Integer`. `shotgrid._coerce_float_fields` now parses that
  Fault, coerces the offending field to float, and `sg_create`/`sg_update` retry
  automatically — so creating a Cut with `fps = 25` (or any other float field on
  any entity) no longer fails. Pure helper + Fault regex covered by
  `tests/test_float_coercion.py`.

## [1.19.0] — 2026-06-22

### Added
- **AMI from a project page now binds to that project, AUTHORITATIVELY** (Chat
  69). The launch diagnostics revealed that the ShotGrid AMI URL fired from a
  project page carries `page_id` (a saved Page), NOT `project_id`. The console
  now parses `page_id` and resolves it to the Page's project
  (`project_detect.resolve_page_project` — the project being *viewed*) and binds
  to it with no confirmation. So opening the console from a project's
  Assets/Shots/etc. page targets that exact project — not the activity guess.
  Resolution priority: explicit `project_id` → `page_id`→project → recent-activity
  heuristic (gate-confirmed). Verified live: page 10740 → project 1310.

### Fixed
- **AMI URLs with HTML-encoded separators (`&amp;`) are parsed** —
  `parse_protocol_url` normalises `&amp;`→`&` before `parse_qs`, so every param
  (`user_login`, `page_id`, …) is extracted even if ShotGrid HTML-encodes the URL.
- **Page/user-only AMI context is no longer dropped** — `_process_url` only
  forwarded the parsed context to the window when it carried
  `entity_type`+`entity_id`, so an AMI fired from a project *page* (carrying
  `user_login`+`page_id` but no selected entity) was silently discarded →
  "No context", no detection. It now forwards ANY non-empty context. This is the
  bug that stopped the `page_id` binding (and the user-login detection) from ever
  reaching the console.
- **`sg_create` no longer auto-injects `project` for global entities** —
  `TaskTemplate` and `Step` use `projects` (multi-entity), not `project`
  (single-entity), so the project auto-inject made creating them fail. `sg_create`
  now skips the inject for those entity types, and honours an explicit
  `project: None` to suppress the inject for any other type.

## [1.18.2] — 2026-06-22

### Fixed
- **Launch-time detection ran too early for AMI launches** (Chat 69). The
  recent-project detector started in `ChatWindow.__init__`, but on macOS the
  ShotGrid AMI context (incl. `user_login`) arrives via a `QFileOpenEvent` Apple
  Event AFTER `__init__` — so the detector saw an empty context and never ran
  (the "launched from the AMI but no [ShotGrid context]" symptom). The trigger
  is extracted to `_maybe_start_detector` and now also fires from
  `update_context` when the AMI URL is processed, so detection runs once
  `user_login` actually arrives.

### Added
- **Launch diagnostics** — the console mirrors its launch context (`sys.argv`,
  the `fpt-mcp://` Apple-Event URL, parsed context, light-payload) to
  `~/Library/Logs/fpt-console-launch.log`, since the console's stdout is
  invisible when launched from the AMI (macOS LaunchServices). Lets us see
  exactly what an AMI delivers.

## [1.18.1] — 2026-06-22

### Fixed
- **Launch-time project detection now finds the ShotGrid creds** (Chat 69). The
  console-side detector read creds from `os.environ`, but the Qt console parses
  `.env` into a private dict (NOT `os.environ`, see `qt/app.py`) — so
  `detect_recent_project` silently found no creds and never detected a project
  (the gate always saw `SHOTGRID_PROJECT_ID=0`, i.e. the v1.18.0 launch detection
  was effectively dead). It now resolves creds from the repo-root `.env` with
  `os.environ` precedence (`_resolve_creds`). Verified live: resolves the user's
  most-recent-activity project (which the gate still proposes for confirmation —
  it can be stale).

## [1.18.0] — 2026-06-22

### Added
- **Session project resolved once at launch, console-side** (Chat 69). When the
  Qt console starts without a project (e.g. from the global ShotGrid user menu),
  it detects the user's most-recent-activity project itself — off the UI thread
  via `qt/project_detect.py` (`HumanUser` → recent `EventLogEntry` with a
  `project`; attachment views count) — and pins it as `SHOTGRID_PROJECT_ID` for
  the whole session (inherited by every per-message MCP server). The header badge
  shows the detected project. A project-scoped AMI (or a DCC engine) stays
  authoritative and always wins. New `tests/test_project_detect.py` (7 tests).

### Changed
- **Project-context gate is now 3-way.** Authoritative `project_id` (AMI/engine)
  → proceed; `project_id` + `project_detected` (the console's launch guess) →
  confirm once before the first write, then pinned for the session; no
  `project_id` → detect-and-ask fallback (unchanged). Changing project = relaunch
  the console (a launch-time decision, like Maya/Flame). Both system prompts
  updated in lockstep; `test_system_prompts` invariants green (qwen/full 0.604).

## [1.17.1] — 2026-06-22

### Changed
- **Project-context gate now proposes a smart default** (Chat 69). When the
  console has no launch project, the gate first DETECTS the user's
  most-recent-activity project from `EventLogEntry` (most recent entry with a
  `project` — attachment views included) using the `user_login` in context, and
  proposes it as a suggested default to confirm — alongside the user's project
  list — instead of asking blank. It is inferred from the last *logged* action,
  so it may be stale: never auto-applied, always confirmed (deliberately **no
  project-selector UI**). Both system prompts updated in lockstep; the
  `test_system_prompts` invariants (quality block + qwen ≤0.65 ratio) stay green.

## [1.17.0] — 2026-06-22

### Changed
- **Console never silently defaults to a project (option B, Chat 69).** The Qt
  console resolves its ShotGrid project ONLY from the launch context: an AMI
  fired from within a project binds to it; launched from the global user menu or
  standalone it injects `SHOTGRID_PROJECT_ID=0` ("no project") instead of the
  `.env` value. A new **project-context gate** in both system prompts then makes
  the assistant list projects and ASK which to use before any
  create/update/delete/publish, operating only on the confirmed project (passed
  explicitly). Prevents the Chat-69 incident where work landed on the default
  project. `project_env_override` reworked; `tests/test_project_env_override.py`
  rewritten (4 tests).

### Fixed
- **Injected `SHOTGRID_PROJECT_ID` now wins over `.env`** — `client.py` called
  `load_dotenv(override=True)`, which clobbered the per-launch project id back to
  the `.env` value, so the v1.16.0 project binding never actually took effect.
  The injected value is captured before and restored after the dotenv load
  (credentials keep `override=True`).

## [1.16.0] — 2026-06-22

### Added
- **Console binds the MCP servers to the launched project** — when the Qt
  console is opened from a ShotGrid AMI / user menu, the loaded project's id
  (carried in the launch context) is injected as `SHOTGRID_PROJECT_ID` into the
  spawned `claude` subprocess env, which the MCP servers it spawns inherit at
  startup. `sg_create` / `sg_find` therefore auto-link to the project the user
  has loaded in the web, **not** the static value in `.env`. A standalone
  `fpt-console` launch (no AMI context) is unchanged — the `.env` project
  stands. New `project_env_override` helper in `qt/claude_worker.py`, applied in
  `ClaudeWorker.run`, with `tests/test_project_env_override.py` (4 tests).

## [1.15.1] — 2026-06-22

### Changed
- **Console colour scheme → Autodesk palette** — both consoles (Qt and AMI
  browser) move from a dark blue/slate base with a red accent to a neutral
  grayscale base with Autodesk yellow (`#ffff00`) for accents, titles and
  primary buttons (dark `#1c1c1c` text on yellow for contrast). Status colours
  (green / red / orange) are unchanged. Aligns fpt-mcp with the maya-mcp and
  flame-mcp panels.

## [1.15.0] — 2026-06-22

### Added
- **Reasoning-effort selector in the Qt console** — a header combo
  (`AVAILABLE_EFFORTS`: Auto / Low / Medium / High / Max, default **Auto**)
  controls the reasoning effort of the spawned `claude` subprocess via
  `build_backend_env`. **Auto** clears both
  `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING` and `CLAUDE_CODE_EFFORT_LEVEL`
  (CLI adaptive-thinking default); a fixed level (Low/Medium/High/Max) forces
  adaptive thinking off at that effort. Only the MCP-spawned subprocess is
  affected, never the top-level user session. (PR #13)

### Changed
- **Qt console default model → Claude Opus 4.8** — Fable 5 kept as a selectable
  option, Sonnet 4.6 retained; remaining console UI strings translated to
  English. `WRITE_ALLOWED_MODELS` unchanged (Fable stays write-allowed). (PR #12)

### Docs
- **LLM backends positioning** — README gains an "LLM backends" subsection
  (Qt Console). The local Ollama backends (🍎 Mac-local, 🖥 LAN) are now
  labelled **experimental** — recommended for offline / lightweight single-tool
  use — while the Anthropic backend is recommended for the full agentic
  pipeline. `config.example.json` and `CLAUDE.md` aligned; the internal
  root-cause note (context overflow + client timeout) lives in `CLAUDE.md` §12.
- **TK_API**: documented the `maya_shot_render` template + `rendered_image`
  publish source. (PR #11)
- **SG_API (RAG corpus)**: corrected the `TaskTemplate` recipe. (PR #14)

## [1.14.0] — 2026-06-15

### Added
- **Copy-source exfiltration guard for `tk_publish`** (`paths.py`
  `enforce_read_containment`) — the publish copy *source* (`local_path`) is now
  refused when it points at a credential-bearing location (`~/.ssh`, `~/.aws`,
  `~/.gnupg`, `~/.gcloud`, `~/.azure`, `~/.docker`, `~/.kube`,
  `~/.password-store`, the unambiguous key/cred basenames, and `/etc`). This
  closes the read-side exfiltration vector where an LLM steered by hostile
  production data copies a secret into ShotGrid-managed storage and registers it
  as a PublishedFile. Unlike the write *destination* guard it is **not** an
  allowlist (a publish source legitimately comes from anywhere — `~/Desktop`,
  `/tmp`) but a credential **denylist**, matched on the `realpath` (defeats
  `..`/symlink tricks), **always on and non-breaking** (it only ever blocks
  reads that cannot be legitimate). Resolves the PR #8 deferred follow-up.
- **`sg_download` auto-discovers the project root** for write containment —
  it now resolves `TkConfig.project_root` from `SHOTGRID_PROJECT_ID`
  (via `_get_tk_config`) and folds it into the allowed roots, so a
  single-project install gets containment for free without configuring
  `FPT_MCP_ALLOWED_WRITE_ROOTS`. Discovery is **best-effort**: a missing
  `PROJECT_ID` or any discovery failure silently falls back to the env-only
  allowlist and never blocks the download. Resolves the PR #8 deferred design.
- **Shared OPSEC error-sanitisation helper** (`error_scrub.py`) — the
  scrub-credential-tokens + truncate-to-300-chars primitive is extracted into a
  byte-identical ecosystem module (canonical `~/Projects/error_scrub_canonical.py`)
  so flame-mcp / maya-mcp apply the exact same guard at their error boundaries.
  `sg_errors.py` now consumes it (behaviour unchanged; `_safe_msg`/`_MAX_MSG`
  preserved as thin aliases). The ShotGrid `Fault` *taxonomy* stays fpt-specific
  (flame/maya never raise `Fault`s). Resolves the PR #9 "port as shared helper"
  follow-up. New `tests/test_error_scrub.py` (10 cases) + 8 new
  containment/discovery cases in `tests/test_path_containment.py`.

### CI / Docs
- **Code knowledge graph auto-publishes to GitHub Pages** on push to `src/**`
  (`.github/workflows/graphify-pages.yml` + `scripts/graphify/`) using the
  original force-directed layout and deterministic file-based community names
  (no LLM key); README links the live graph. `src/graphify-out/` is gitignored.

## [1.13.0] — 2026-06-15

### Added
- **Structured ShotGrid fault → JSON at the tool boundary** (`sg_errors.py`) —
  `shotgun_api3` faults (`AuthenticationFault`, `Fault`,
  `MissingTwoFactorAuthenticationFault`,
  `UserCredentialsNotAllowedForSSOAuthenticationFault`, `ProtocolError`,
  `ResponseError`, `ShotgunFileDownloadError`) and the underlying
  socket/`urllib`/SSL/timeout errors — plus the credential `EnvironmentError`
  raised by `client._validate_config` — are now translated into the repo's
  standard structured-error JSON `{error, error_type, hint, retryable}` instead
  of propagating as an opaque `ToolError` string. A new `@sg_errors_to_json`
  async decorator is applied to the 16 str-returning `*_impl` / `*_do_*`
  tool-boundary functions across `shotgrid.py`, `reporting.py`,
  `toolkit_tools.py`, and `launcher.py`. `client._sg_call` is left **untouched**
  (it returns raw SDK data mid-pipeline, so catching there would corrupt the
  data contract — it keeps logging the traceback and re-raising).
  - `error_type` is a stable machine-readable class; `hint` carries concrete
    remediation guidance; `retryable` is an **advisory** label only (no
    auto-retry).
  - **OPSEC**: the echoed server message is scrubbed of credential-shaped
    tokens (`api_key` / `script_key` / `password` / `secret` / `token` / `key`
    `=`/`:` values) and truncated to 300 chars; a message that merely *names* a
    field (e.g. the common `AuthenticationFault` string) is left intact.
  - Unrecognised exceptions are **re-raised** with their traceback (a genuine
    `KeyError`/`TypeError` bug is never swallowed).
  - Free correctness win: faults now flow through the dispatcher `_count_turn`,
    so auth/connection failures are finally counted toward `p_fallo` (they
    previously bypassed it by unwinding the wrapper).
  - New `tests/test_sg_errors.py` (31 cases) and an `sg_fault_error_contract`
    entry in `.concepts.yml`. Kept fpt-specific for now; porting the decorator
    to flame-mcp / maya-mcp as a shared helper is a documented follow-up.
- **Write-path containment for `tk_publish` / `sg_download`** (`paths.py`) —
  the two tools that write attacker-influenceable bytes to
  attacker-influenceable locations now anchor every write destination on a
  legitimate project root before the `shutil.copy2` (tk_publish) / attachment
  download (sg_download). Containment is computed on the *real* path
  (`os.path.realpath` + `Path.is_relative_to`), so it catches dot-dot
  traversal, **absolute escapes with no `..`** (`/etc/passwd`), and symlink
  escapes that the detection-only `safety.py` regex cannot. The copy *source*
  (`local_path`) is intentionally not contained yet (documented follow-up),
  and `safety.py` keeps its traversal regex as a detection-only pre-filter.
  - **`FPT_MCP_ALLOWED_WRITE_ROOTS`** (`os.pathsep`-separated absolute roots) —
    the operator allowlist. Allowed roots = the discovered
    `TkConfig.project_root` (when a PipelineConfiguration resolves) UNION this
    list. Mode-1 publishes pass by construction.
  - **`FPT_MCP_STRICT_PATHS`** — enforcement switch. **Default is WARN**: an
    out-of-root destination logs a structured warning via the existing logger
    and is *allowed*, so no current workflow breaks and the existing Mode-2
    tests stay green. Set `FPT_MCP_STRICT_PATHS=1` to turn it into a hard
    refusal (`{"error": ...}`, nothing written, no directory chain fabricated).
  - New `tests/test_path_containment.py` (predicate + WARN/STRICT policy +
    `tk_publish`/`sg_download` integration) and a `path_containment_guard`
    entry in `.concepts.yml`.

### Changed
- CI: Python 3.13 added to the test matrix.

## [1.12.0] — 2026-06-15

### Added
- **`fpt_launch_app` launches Flame in context** (P3, Chat 65) — closing
  the gap where the resolver only scanned for Maya and every
  `app="flame"` call returned "not installed". New direct route composes
  `startApplication --start-project=<name> [--start-workspace=<ws> |
  --create-workspace] --closed-libs` (the user-validated formula; flags
  verified identical across Flame 2024–2027) with three guard rails:
  the SG project name is slugified with tk-flame's exact convention
  (`\W+` → `_`) and validated against the local Stone+Wire project list
  (`sw_listProjects`, fallback `/opt/Autodesk/project`); an unknown
  project is refused with `route='toolkit'` suggested (tk-flame
  pre-creates projects via Wiretap — the direct route cannot); a running
  Flame-family instance refuses the launch (single instance per
  framestore + exclusive project locks) unless `force=true`. New
  `FptLaunchAppInput` params: `route` (`auto`/`direct`/`toolkit`),
  `workspace`, `force`.
- **FPT-selected version is authoritative** — `resolve_app` now matches
  SG `Software.version_names` against local installs and prefers the
  FPT selection over "newest installed" (held-back versions are
  intentional in this pipeline); a warning names both versions when the
  FPT-selected one is missing locally. Flame OS scan added
  (`/opt/Autodesk/flame_*/bin/startApplication`, full-version sort,
  version-symlink dedup).
- **Rotating file logger at the ShotGrid client boundary**
  (`logging_config.py`) — captures the op + sanitized args before each call
  and the full traceback on failure, so an AuthenticationFault / connection
  timeout is reconstructable instead of vanishing into the `to_thread`
  worker. Idempotent, falls back to `NullHandler` on a read-only FS, and sets
  `propagate=False` so the stdio MCP transport stays clean.
- **Per-call socket/request timeout** on the shared ShotGrid connection via
  `sg.config.timeout_secs` (`SHOTGRID_TIMEOUT_SECS`, default 30s) so a
  non-responding server can no longer hang a worker thread forever.
- **Dispatcher sub-models accept native and serialized forms** —
  `SgBatchInput.requests`, `SgSummarizeInput.filters/summary_fields/grouping`
  and `SgTextSearchInput.entity_types` now take either a JSON string or a
  native list/dict (a `mode="before"` validator normalises to the string the
  handler `json.loads`), removing the opaque "must be a valid string" error.
- **Test coverage**: `tests/test_reporting.py` (100% of the four
  `fpt_reporting` handlers), real-serialized-payload safety tests, and batch
  safety tests.
- **CI**: Python 3.13 added to the matrix (3.14 documented, pending a stable
  setup-python image) and a `--cov-fail-under=50` floor so a regression that
  zeroes a module's coverage fails CI.

### Changed
- **TK_API.md: `texture_asset_publish` now documents the multi-format
  definition** — the pipeline config (toolkit_config_custom_template
  `e1fe35e`) replaced the hard-coded `.png` with the `texture_extension`
  template key (png default, exr/tif). RAG index rebuilt;
  `verify_templates` 55/55 against the live config.
- **Safety scanner is no longer partly dead** — `check_dangerous()` takes the
  originating tool name and prepends it to the scanned payload, so the
  tool-name-prefixed dangerous patterns (`sg_update.*"project":null`,
  `sg_delete.*PublishedFile`, `sg_update.*PublishedFile.*"path"`, …) can fire
  for the first time. Batch sub-operations are scanned per sub-request.
- **Shared ShotGrid connection is thread-safe** — every SDK call routes
  through one locked chokepoint (`client._sg_call`) that serializes use of the
  single httplib2 socket (no more interleaved reads/writes under concurrent
  dispatch) and logs each op; args/exceptions pass through unchanged so batch
  all-or-nothing semantics are preserved.
- **`learn_pattern` status `learned` → `appended_pending_index`** — honest
  reporting that an appended pattern is not retrievable until `build_index`
  regenerates the corpus; `rag.search.clear_cache()` now also drops the BM25
  singletons.
- **`LearnPatternInput.api` is now `Literal["shotgun_api3","toolkit","rest_api"]`**
  (was free-form `str`) so an invalid value is rejected instead of silently
  misrouting the pattern into `SG_API.md`.
- **RAG savings baseline corrected** — `_FULL_DOC_TOKENS` / `FULL_DOC_TOKENS`
  13000 → 34000 to match the real ~103k-char corpus
  (SG_API + TK_API + REST_API), so `session_stats` no longer understates
  tokens saved by ~2.6×.
- **install.sh** derives the pre-approved tool-count summary from
  `len(TOOLS)` instead of a hardcoded `14` (15 tools are registered).
- **Docs** — README self-learning section corrected (Opus/Fable write, not
  Sonnet; removed the non-existent `rag/failed.json` feature) and
  `reset_session_stats` added to the permissions block; CLAUDE.md corpus
  "3 collections" → "1 collection" and retired M5 Pro references removed;
  SG_API.md gains a complete operator whitelist (adds `not_between`,
  `name_starts_with`, `name_ends_with`, `name_is`); TK_API.md duplicate
  `nuke_shot_render_pub_stereo` line removed; CHANGELOG compare-link footer
  refreshed to v1.11.0.

### Fixed
- **`_do_sg_batch` JSON contract** — a safety-blocked batch now returns the
  standard `{"safety_warning": ...}` envelope instead of a raw string, so
  `_result_is_error()` counts the block as a failed turn (it was deflating
  `p_fallo`).

### Security
- **OPSEC** — `tests/test_qt_protocol_url.py` fixture scrubbed to synthetic
  values (no real instance URL, admin login, or project metadata);
  `src/fpt_mcp/config.json` and `src/fpt_mcp/rag/candidates.json` added to
  `.gitignore`. (Untracking the already-committed `config.json` and any git
  history scrub are index/history operations performed outside the working
  tree.)

## [1.11.0] — 2026-06-10

### Added
- **`scripts/verify_templates.py`** — validates the pipeline's Toolkit
  templates (fixture/config) against the RAG doc `TK_API.md` (7 checks); wired
  as a `--strict` pre-commit hook. Documents 7 previously-undocumented templates
  in `TK_API.md`. Adds 52 tests.

### Changed
- **Cloud model selector refreshed** — the Qt console now offers Claude Fable 5
  (`claude-fable-5`), Claude Opus 4.8 (`claude-opus-4-8`) and Claude Sonnet 4.6
  (Opus 4.7 and Haiku 4.5 removed). Self-learning (`learn_pattern` write-trust)
  is now reserved for **Opus + Fable** — Sonnet and local models are read-only
  (`WRITE_ALLOWED_MODELS`, `config.json` default + example, README/CLAUDE.md
  updated in lockstep). `config.json` default model bumped 4.7 → 4.8.

### Fixed
- **README "Project Structure" drift** — the section listed two launchd plists
  (`com.abrahamadsk.fpt-mcp.plist`, `com.abrahamadsk.fpt-ami.plist`) that do not
  exist in the repo, plus `paths.py` (removed long ago) and a `tools/` package
  that does not exist; the real module list and `scripts/` were missing. The
  section now mirrors the actual tree and documents that the launchd plist
  (`com.fpt-mcp.server`) is generated machine-locally by `setup_venv.sh` at
  install time — no machine-specific names or paths are tracked in the repo.

## [1.10.0] — 2026-05-21

### Added
- **F0 session-stats telemetry (3C Wave 2)** — new `src/fpt_mcp/_session_stats.py`
  module: `persist_timing`/`persist_turn` JSONL streams with 5 MB rotation,
  30-minute idle auto-reset, and the `turns_total`/`failed_turns` counters that
  drive `p_fallo = failed_turns / turns_total` over the `fpt_bulk`/`fpt_reporting`
  dispatchers (the error-prone batch/mutation/reporting path). `session_stats`
  now reports `p_fallo`; new `reset_session_stats` tool (tool inventory 14 → 15).
  Cross-session timing baselines persist to `logs/timings.jsonl`. New
  `stats_keys_schema_shared` concept invariant locks `_stats` to
  `make_empty_stats()`. Ported from flame-mcp for ecosystem parity.
- **Golden RAG regression dataset (3C Wave 3)** — `tests/golden/fpt_queries.jsonl`
  (40 queries, 16 adversarial) + `tests/test_golden.py`.
- **Ollama `keep_alive` 30 m + `config.json` knob (3C Wave 1)**.

### Changed
- **Trimmed `CLAUDE.md` operator sections → `docs/DEPLOY.md` (3C Wave 5)** so the
  LLM system prompt no longer carries install/deploy shell recipes.
- **server.py line budget 700 → 800** — F0 telemetry is a new architectural
  concern; all pure logic lives in `_session_stats.py`, only the irreducible
  global-mutating glue + the `reset_session_stats` tool stay in server.py.

## [1.9.3] — 2026-04-28

### Fixed
- `qt/app.py` — AMI URL parser took the WRONG entity ID. ShotGrid AMI
  URLs ship two ID fields with very different semantics:
    - `selected_ids` — the entity (or entities) the user actually clicked
    - `ids` — every entity visible in the column / page (often dozens)
  The loop in `parse_protocol_url` and the dict access in
  `fetch_ami_payload` both probed `ids` first and `break`-ed before
  reaching `selected_ids`. Result: the badge showed `Asset #ids[0]`
  instead of `Asset #selected_ids[0]`. Reproduced from
  `/tmp/fpt-console.log`: user clicked Asset 1480 (URL had
  `selected_ids=1480` and `ids=1479,1480,1481,1482,1511,1512,1545`),
  the badge showed Asset #1479 (the first element of `ids`). Both
  parsers now prefer `selected_ids` and only fall back to `ids` when
  `selected_ids` is missing.
- `tests/test_qt_protocol_url.py` — new file, 7 tests pinning the
  parser contract: `selected_ids` wins over `ids`, multi-value
  selected_ids takes the first, fallback to `ids` when selected_ids
  is absent, URL-encoded commas decode correctly, ShotGrid placeholder
  braces (`{selected_ids}`) are skipped, `event_log_entry_id` is
  captured for Light Payload mode, and the full real-world URL from
  the Chat 49 reproduction returns Asset 1480.

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

[Unreleased]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.11.0...HEAD
[1.11.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.10.0...v1.11.0
[1.10.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.9.3...v1.10.0
[1.9.3]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.9.2...v1.9.3
[1.9.2]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.9.1...v1.9.2
[1.9.1]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.9.0...v1.9.1
[1.9.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.8.0...v1.9.0
[1.8.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.7.1...v1.8.0
[1.7.1]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.7.0...v1.7.1
[1.7.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.5.2...v1.6.0
[1.5.2]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.5.1...v1.5.2
[1.5.1]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.5.0...v1.5.1
[1.5.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.4.2...v1.5.0
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
