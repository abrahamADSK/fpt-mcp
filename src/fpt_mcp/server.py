#!/usr/bin/env python3
"""FPT MCP Server — Full Flow Production Tracking (ShotGrid) + Toolkit + RAG.

General-purpose MCP server exposing the complete ShotGrid API, Toolkit
path conventions, and RAG-powered documentation search.

Features:
    - 8 Tier-1 ShotGrid/Toolkit tools (always visible)
    - 3 bulk tools (behind fpt_bulk dispatch: delete, revive, batch)
    - 4 reporting tools (behind fpt_reporting dispatch: text_search, summarize, note_thread, activity)
    - 4 RAG tools (search_sg_docs, learn_pattern, session_stats, reset_session_stats)
    - Dangerous pattern detection (safety.py)
    - Hybrid search: ChromaDB + BM25 + HyDE + RRF fusion
    - Token tracking with RAG savings measurement
    - Model trust gates for self-learning

Works standalone or alongside other MCP servers for cross-tool orchestration.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from fpt_mcp.client import (
    get_sg,
    get_project_filter,
    sg_find,
    sg_find_one,
    sg_create,
    sg_update,
    sg_upload,
    sg_upload_thumbnail,
    sg_download_attachment,
    sg_schema_field_read,
    sg_batch,
    sg_revive,
    sg_text_search,
    sg_summarize,
    sg_note_thread_read,
    sg_activity_stream_read,
    PROJECT_ID,
)
from fpt_mcp.tk_config import discover_or_fallback, TkConfigError
from fpt_mcp.safety import check_dangerous
from fpt_mcp.software_resolver import resolve_app
from fpt_mcp._session_stats import (
    apply_idle_reset,
    classify_result_error as _result_is_error,
    make_empty_stats,
    persist_timing as _persist_timing,
    reset_stats as _reset_stats_helper,
)

# Bucket F Phase 2a — filter validation + Pydantic input models live in
# dedicated modules. server.py keeps the @mcp.tool decorators so install.sh
# ast-extraction and the .concepts.yml invariants that grep for tool names
# by file path continue to work unchanged.
from fpt_mcp.filters import (
    _PROJECT_SCOPED_ENTITIES,
    _VALID_FILTER_OPERATORS,
    _MAX_FILTER_DEPTH,
    _validate_filter_triples,
)
from fpt_mcp.models import (
    _STRICT_CONFIG,
    # Direct ShotGrid tool inputs
    SgFindInput, SgCreateInput, SgUpdateInput, SgDeleteInput,
    SgSchemaInput, SgUploadInput, SgDownloadInput,
    # Toolkit tool inputs
    TkResolvePathInput, TkPublishInput,
    # Dispatcher enums + wrappers
    BulkAction, BulkDispatchInput,
    ReportingAction, ReportingDispatchInput,
    # Launcher tool input
    FptLaunchAppInput,
    # Bulk sub-models
    SgBatchInput, SgReviveInput,
    # Reporting sub-models
    SgTextSearchInput, SgSummarizeInput, SgNoteThreadInput, SgActivityInput,
    # RAG tool inputs
    SearchSgDocsInput, LearnPatternInput,
)

_SERVER_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Token tracking (REC-001 — from flame-mcp proven architecture)
# ---------------------------------------------------------------------------

_FULL_DOC_TOKENS = 13000  # combined size of all indexed docs

# Canonical stats dict. Schema lives in fpt_mcp._session_stats.make_empty_stats
# so the initialiser and the reset path cannot drift (invariant: stats_keys_schema_shared).
# Note: cache_hits is tracked inside rag.search and surfaced via session_stats_tool
# by importing get_cache_stats() (see C.1 fix), so it is NOT a _stats key.
_stats = make_empty_stats()
# Records when _stats was last reset (server start, idle-gap auto-reset, or explicit reset).
_stats_reset_at = datetime.datetime.now()
# Timestamp of the previous MCP tool call — drives the idle-gap auto-reset.
_last_call_at: Optional[datetime.datetime] = None

# F0 baseline telemetry: persistent JSONL stream that survives server restarts
# (the in-memory ring buffer in _stats['timings'] holds only the last 20 entries).
# Written best-effort; failures never propagate.
_TIMINGS_LOG = _SERVER_DIR / "logs" / "timings.jsonl"

# RAG state
_last_rag_score: int = 100
_rag_called_this_session: bool = False


def _tok(text: str) -> int:
    """Rough token estimate: 1 token ≈ 3 characters."""
    return max(1, len(text) // 3)


def _rating(tokens: int) -> str:
    if tokens < 500:
        return "🟢 low"
    elif tokens < 2000:
        return "🟡 medium"
    return "🔴 high"


# ---------------------------------------------------------------------------
# Soft-warning RAG enforcement (C.2)
# ---------------------------------------------------------------------------

def _rag_skipped_warning() -> Optional[dict]:
    """Return a soft-warning dict if search_sg_docs has not been called yet
    in this session, else None.

    The warning is merged into the response payload of any tool that touches
    ShotGrid mutation/query semantics where filter syntax, entity reference
    format, field names, or operator names matter. The intent is NOT to block
    execution — experienced workflows that already know the schema should
    proceed — but to nudge the LLM to verify with documentation when it has
    skipped the mandatory check.

    Increments _stats["rag_skipped"] each time it fires (visible in
    session_stats), which lets the user see retroactively how often the
    LLM bypassed the schema docs.
    """
    if _rag_called_this_session:
        return None
    _stats["rag_skipped"] += 1
    return {
        "rag_warning": (
            "search_sg_docs has not been called yet in this session. "
            "Per the MCP server instructions, call search_sg_docs FIRST "
            "for any query you are unsure about (filter syntax, operator "
            "names, entity reference format, field names, template tokens). "
            "Proceeding with this call anyway."
        )
    }


# ---------------------------------------------------------------------------
# Model trust gates (C5 — from flame-mcp)
# ---------------------------------------------------------------------------

WRITE_ALLOWED_MODELS = {
    "claude-opus", "claude-sonnet", "claude-sonnet-4",
    "claude-sonnet-4-6", "claude-opus-4-5", "claude-opus-4-6",
}


def _get_config() -> dict:
    try:
        return json.loads((_SERVER_DIR / "config.json").read_text())
    except Exception:
        return {}


def _get_current_model() -> str:
    """Resolve the active model id.

    Priority: FPT_MCP_RUNTIME_MODEL env var (set by qt/claude_worker.py per
    invocation, reflects the actual --model passed to the CLI) → config.json
    on disk → "unknown".

    The env-var path is what makes the trust gate work when the user toggles
    backends in the Qt console — config.json is static and would otherwise
    let any model bypass the write gate.
    """
    env_model = os.environ.get("FPT_MCP_RUNTIME_MODEL", "").strip()
    if env_model:
        return env_model
    return _get_config().get("model", "unknown")


def _model_can_write() -> bool:
    model = _get_current_model().lower()
    cfg_list = _get_config().get("write_allowed_models")
    if cfg_list:
        return any(allowed.lower() in model for allowed in cfg_list)
    return any(allowed in model for allowed in WRITE_ALLOWED_MODELS)


# Idle window (seconds) after which _stats is auto-zeroed on the next call.
# Overridable via config.json -> stats_idle_reset_seconds (default 30 min).
_STATS_IDLE_RESET_SECONDS = int(
    _get_config().get("stats_idle_reset_seconds", 30 * 60)
)


def _track_call() -> None:
    """Idle-gap auto-reset of _stats. Called at dispatcher + RAG/stats entry."""
    global _last_call_at, _stats_reset_at
    now = datetime.datetime.now()
    did_reset, reset_at = apply_idle_reset(
        _stats, now, _last_call_at, idle_reset_seconds=_STATS_IDLE_RESET_SECONDS,
    )
    if did_reset:
        _stats_reset_at = reset_at
    _last_call_at = now


def _track_timing(entry: dict) -> None:
    """F0: ring-buffer (max 20) + best-effort enriched JSONL append for
    cross-session baselines. Persistence failures never propagate."""
    _stats["timings"].append(entry)
    if len(_stats["timings"]) > 20:
        _stats["timings"].pop(0)
    cfg = _get_config()
    _persist_timing(_TIMINGS_LOG, {
        "ts":        datetime.datetime.now().isoformat(timespec="seconds"),
        "model":     _get_current_model(),
        "backend":   cfg.get("backend", "anthropic"),
        "tool_name": entry.get("op", "unknown"),
        **entry,
    })


def _count_turn(out: str, op: str, t0: float) -> None:
    """F0: record one dispatcher turn — increment turns_total, classify the raw
    handler output, bump failed_turns on error, and persist the timing."""
    _stats["turns_total"] += 1
    is_error = _result_is_error(out)
    if is_error:
        _stats["failed_turns"] += 1
    _track_timing({"op": op, "total_ms": round((time.monotonic() - t0) * 1000), "error": is_error})


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "fpt_mcp",
    instructions="""You are controlling ShotGrid (Flow Production Tracking) via the fpt-mcp server.

## MANDATORY WORKFLOW

1. For any ShotGrid query you're unsure about — filter syntax, entity format,
   field names, template tokens — call search_sg_docs FIRST.
   NEVER guess filter operators, entity reference format, or template tokens.

2. The safety module will warn you about dangerous patterns. Heed its warnings.

3. Entity references in filters MUST be dicts: {"type": "Asset", "id": 123}
   NEVER use plain integers or strings for entity links.

4. Toolkit template tokens are case-sensitive: {Shot}, {Asset}, {Step} (PascalCase).
   NEVER use {shot_name}, {asset_name}, {step} (lowercase).

5. When a working pattern succeeds and search_sg_docs returned < 60% relevance,
   call learn_pattern to save the validated pattern for future sessions.

6. Call session_stats at the end of multi-step tasks to report token efficiency.
""",
)


# Filter validation + Pydantic input models live in fpt_mcp.filters and
# fpt_mcp.models respectively (Bucket F Phase 2a). See the import block
# near the top of this file. The symbols are re-imported here so grep-based
# invariants continue to find them at this file path.


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

@mcp.tool(name="sg_find")
async def sg_find_tool(params: SgFindInput) -> str:
    """Search for any entity in ShotGrid with filters.

    Works with ALL entity types: Asset, Shot, Sequence, Version, Task,
    Note, PublishedFile, HumanUser, Project, Playlist, TimeLog,
    CustomEntity01-30, etc.

    Filter syntax follows ShotGrid API:
      [["field", "operator", value], ...]
    Operators: is, is_not, contains, not_contains, starts_with,
    greater_than, less_than, in, between, etc.
    """
    from fpt_mcp.shotgrid import sg_find_impl
    from fpt_mcp.suggestions import maybe_annotate_with_suggestions
    _stats["exec_calls"] += 1
    _stats["tokens_in"] += _tok(json.dumps({"filters": params.filters, "limit": params.limit}, default=str))
    out = await sg_find_impl(params)
    out = maybe_annotate_with_suggestions("sg_find", out)
    _stats["tokens_out"] += _tok(out)
    return out


@mcp.tool(name="sg_create")
async def sg_create_tool(params: SgCreateInput) -> str:
    """Create any entity in ShotGrid.

    Works with ALL entity types. Project is auto-linked if SHOTGRID_PROJECT_ID
    is set and 'project' is not in the data dict.
    """
    from fpt_mcp.shotgrid import sg_create_impl
    _stats["exec_calls"] += 1
    _stats["tokens_in"] += _tok(json.dumps({"entity_type": params.entity_type, "data": params.data}, default=str))
    out = await sg_create_impl(params)
    _stats["tokens_out"] += _tok(out)
    return out


@mcp.tool(name="sg_update")
async def sg_update_tool(params: SgUpdateInput) -> str:
    """Update any entity's fields in ShotGrid."""
    from fpt_mcp.shotgrid import sg_update_impl
    _stats["exec_calls"] += 1
    _stats["tokens_in"] += _tok(json.dumps(
        {"entity_type": params.entity_type, "entity_id": params.entity_id, "data": params.data},
        default=str,
    ))
    out = await sg_update_impl(params)
    _stats["tokens_out"] += _tok(out)
    return out


@mcp.tool(name="sg_schema")
async def sg_schema_tool(params: SgSchemaInput) -> str:
    """Get the field schema for any ShotGrid entity type.

    Returns field names, types, and properties. Use this to discover
    what fields are available before querying or creating entities.
    """
    from fpt_mcp.shotgrid import sg_schema_impl
    _stats["exec_calls"] += 1
    _stats["tokens_in"] += _tok(params.entity_type) + _tok(params.field_name or "")
    out = await sg_schema_impl(params)
    _stats["tokens_out"] += _tok(out)
    return out


@mcp.tool(name="sg_upload")
async def sg_upload_tool(params: SgUploadInput) -> str:
    """Upload a file to any entity field in ShotGrid.

    Use field_name='image' for thumbnails, 'sg_uploaded_movie' for movies,
    or any file/url field.
    """
    from fpt_mcp.shotgrid import sg_upload_impl
    _stats["exec_calls"] += 1
    _stats["tokens_in"] += _tok(params.file_path)
    out = await sg_upload_impl(params)
    _stats["tokens_out"] += _tok(out)
    return out


@mcp.tool(name="sg_download")
async def sg_download_tool(params: SgDownloadInput) -> str:
    """Download an attachment from any entity field in ShotGrid."""
    from fpt_mcp.shotgrid import sg_download_impl
    from fpt_mcp.suggestions import maybe_annotate_with_suggestions
    _stats["exec_calls"] += 1
    _stats["tokens_in"] += _tok(f"{params.entity_type} {params.entity_id} {params.field_name}")
    out = await sg_download_impl(params)
    out = maybe_annotate_with_suggestions("sg_download", out)
    _stats["tokens_out"] += _tok(out)
    return out


async def _get_tk_config():
    """Get or discover the TkConfig for the current project.

    Returns TkConfig if the project has a PipelineConfiguration, None otherwise.
    """
    if not PROJECT_ID:
        return None
    return await discover_or_fallback(PROJECT_ID, sg_find)


async def _resolve_step_short_name(step_input: str, entity_type: str) -> str:
    """Map a user-supplied step keyword to the project's canonical Step.short_name.

    Toolkit's ``{Step}`` template token expects the Step entity's
    ``short_name`` (e.g. ``MDL``), not the user-friendly code (``Model``)
    or lowercased input (``model``). Without this resolution the resulting
    path falls outside the project root and Toolkit's
    ``sgtk_from_path`` raises TankInitError.

    Probes ShotGrid for a Step matching the input under ``short_name`` or
    ``code`` across common case variants. Returns the canonical
    ``short_name`` on hit; returns ``step_input`` unchanged on miss so
    legacy and tested behaviour is preserved when SG is mocked or the
    Step does not exist in this site.
    """
    canon = (step_input or "").strip()
    if not canon:
        return canon
    seen: set[str] = set()
    for variant in (canon, canon.upper(), canon.lower(), canon.capitalize()):
        if variant in seen:
            continue
        seen.add(variant)
        for field in ("short_name", "code"):
            row = await sg_find_one(
                "Step",
                [
                    ["entity_type", "is", entity_type],
                    [field, "is", variant],
                ],
                ["short_name"],
            )
            if row and row.get("short_name"):
                return row["short_name"]
    return canon


async def _build_template_fields(
    entity_type: str,
    entity_id: int,
    step: str,
    name: str,
    version: int,
    extension: Optional[str] = None,
) -> dict[str, Any]:
    """Build the template fields dict by fetching entity context from ShotGrid."""
    fields_to_fetch = ["code"]
    if entity_type == "Asset":
        fields_to_fetch.append("sg_asset_type")
    elif entity_type == "Shot":
        fields_to_fetch.extend(["sg_sequence"])

    entity = await sg_find_one(
        entity_type,
        [["id", "is", entity_id]],
        fields_to_fetch,
    )
    if not entity:
        raise TkConfigError(f"{entity_type} #{entity_id} not found in ShotGrid.")

    step_token = await _resolve_step_short_name(step, entity_type)

    template_fields: dict[str, Any] = {
        "Step": step_token,
        "name": name,
        "version": version,
    }

    if entity_type == "Asset":
        template_fields["Asset"] = entity["code"]
        template_fields["sg_asset_type"] = entity.get("sg_asset_type", "Generic")
    elif entity_type == "Shot":
        template_fields["Shot"] = entity["code"]
        seq = entity.get("sg_sequence")
        if seq:
            template_fields["Sequence"] = seq.get("name", seq.get("code", "SEQ"))

    if extension:
        # Map extension to the right key (maya_extension, etc.)
        template_fields["maya_extension"] = extension

    return template_fields




@mcp.tool(name="tk_resolve_path")
async def tk_resolve_path_tool(params: TkResolvePathInput) -> str:
    """Resolve a Toolkit publish path using the project's PipelineConfiguration.

    Reads the PipelineConfiguration from ShotGrid, loads templates.yml,
    and resolves the full file path. Requires the project to have an
    Advanced Setup with a PipelineConfiguration entity.

    Use search_sg_docs to find available template names for the project's config.
    """
    from fpt_mcp.toolkit_tools import tk_resolve_path_impl
    _stats["exec_calls"] += 1
    _stats["tokens_in"] += _tok(f"{params.entity_type} {params.entity_id} {params.template_name}")
    out = await tk_resolve_path_impl(params)
    _stats["tokens_out"] += _tok(out)
    return out


@mcp.tool(name="tk_publish")
async def tk_publish_tool(params: TkPublishInput) -> str:
    """Publish a file to ShotGrid.

    Two modes:
    - With PipelineConfiguration: resolves the publish path from Toolkit
      templates automatically (use tk_resolve_path first to preview the path).
    - Without PipelineConfiguration: requires an explicit publish_path parameter.
      The path is stored in the PublishedFile and is accessible by any tool
      that reads the path field. If the project has a Local File Storage
      configured in ShotGrid, the file will be browsable from the web UI.
    """
    from fpt_mcp.toolkit_tools import tk_publish_impl
    from fpt_mcp.suggestions import maybe_annotate_with_suggestions
    _stats["exec_calls"] += 1
    _stats["tokens_in"] += _tok(f"{params.entity_type} {params.entity_id} {params.publish_type}")
    out = await tk_publish_impl(params)
    out = maybe_annotate_with_suggestions("tk_publish", out)
    _stats["tokens_out"] += _tok(out)
    return out


# ---------------------------------------------------------------------------
# Software launcher — locate DCC applications and launch them in context
# ---------------------------------------------------------------------------
# Implementation lives in fpt_mcp.launcher (Bucket F Phase 2b). The decorator
# stays here as a thin wrapper so install.sh ast-extraction still finds the
# tool name, and the .concepts.yml mcp_tool_inventory invariant continues
# to pass. `_project_id_for_entity` is re-exported here for tests that
# pre-date the split.
from fpt_mcp.launcher import _project_id_for_entity  # noqa: E402,F401

# Bucket F Phase 2d — re-export handler functions from shotgrid / reporting so
# tests that import them by the `fpt_mcp.server._do_sg_*` path still resolve.
from fpt_mcp.shotgrid import (  # noqa: E402,F401
    _do_sg_batch, _do_sg_delete, _do_sg_revive,
)
from fpt_mcp.reporting import (  # noqa: E402,F401
    _do_sg_activity, _do_sg_note_thread, _do_sg_summarize, _do_sg_text_search,
)


@mcp.tool(name="fpt_launch_app")
async def fpt_launch_app_tool(params: FptLaunchAppInput) -> str:
    """Launch a DCC application scoped to a ShotGrid entity.

    Discovery is OS-first: if the app is not installed on this machine the
    tool fails immediately without consulting ShotGrid. When the owning
    project has an Advanced Setup PipelineConfiguration whose ``tank`` CLI
    is reachable on disk, the launch is routed through ``tank`` so Toolkit
    pre-launch hooks run and the app opens in the correct context.
    Otherwise the tool falls back to a direct ``open -a`` launch and
    surfaces a warning — the app still opens, but without context
    injection from Toolkit.

    Common failure modes to explain to the user if they surface:

    - ``error: "... is not installed ..."`` — the DCC binary is not under
      the expected install path. Tell the user to install it and retry.
    - Popen succeeds but the launched process dies immediately with a
      tank message like ``EOF when reading a line`` or ``Authentication
      ... expired``. This means the Toolkit ``tank`` CLI lost its cached
      session. The user must run ``<PipelineConfiguration>/tank <Entity>
      <id>`` once in an interactive terminal, authenticate via the
      browser, and retry. The tool cannot do this because it cannot
      deliver the browser approval step.
    - Popen succeeds but tank errors with ``does not exist on disk`` for
      an engine (e.g. ``tk-shell v0.10.2``). The pipeline config expects
      bundles under ``<config>/install/`` which are absent. Suggest the
      user add ``bundle_cache_fallback_roots`` pointing to
      ``~/Library/Caches/Shotgun/bundle_cache`` in the config's
      ``pipeline_configuration.yml``.
    """
    from fpt_mcp.launcher import fpt_launch_app_impl
    _stats["exec_calls"] += 1
    _stats["tokens_in"] += _tok(f"{params.app} {params.entity_type} {params.entity_id}")
    out = await fpt_launch_app_impl(params)
    _stats["tokens_out"] += _tok(out)
    return out


# ---------------------------------------------------------------------------
# Bulk + Reporting dispatcher tools
# ---------------------------------------------------------------------------
# Handlers live in fpt_mcp.shotgrid (bulk) and fpt_mcp.reporting (reporting),
# extracted in Bucket F Phase 2d. The dispatcher wrappers below handle the
# _stats bookkeeping so test_telemetry AST scan of server.py finds the
# increments; handlers themselves are pure (except safety_blocks, which
# stays at the point the block is triggered).


@mcp.tool(name="fpt_bulk")
async def fpt_bulk(params: BulkDispatchInput) -> str:
    """Execute bulk/destructive ShotGrid operations.

    Available actions:

    • delete — Retire (soft-delete) an entity. Can be restored from trash. Required params: {"entity_type": "Shot", "entity_id": 123}
    • revive — Restore a previously retired entity. Required params: {"entity_type": "Shot", "entity_id": 123}
    • batch — Execute multiple operations in a single transactional call (ALL succeed or ALL fail). Required params: {"requests": "[{\"request_type\": \"create\", \"entity_type\": \"Shot\", \"data\": {\"code\": \"SH010\", \"project\": {\"type\": \"Project\", \"id\": 123}}}]"}
    """
    from fpt_mcp.suggestions import maybe_annotate_with_suggestions
    _track_call()
    dispatch = {
        BulkAction.DELETE: _do_sg_delete,
        BulkAction.REVIVE: _do_sg_revive,
        BulkAction.BATCH: _do_sg_batch,
    }
    handler = dispatch[params.action]
    _stats["exec_calls"] += 1
    params_str = json.dumps(params.params or {}, default=str)
    _stats["tokens_in"] += _tok(params_str)
    _t0 = time.monotonic()
    out = await handler(params.params or {})
    # F0: count the turn on the RAW handler output (before annotation) so an
    # appended next_suggested_actions block cannot mask the error key.
    _count_turn(out, "fpt_bulk", _t0)
    out = maybe_annotate_with_suggestions("fpt_bulk", out)
    _stats["tokens_out"] += _tok(out)
    return out


@mcp.tool(name="fpt_reporting")
async def fpt_reporting(params: ReportingDispatchInput) -> str:
    """Search, aggregate, and inspect ShotGrid data for reporting and analysis.

    Available actions:

    • text_search — Full-text search across multiple entity types at once. Required params: {"text": "search terms", "entity_types": "{\"Asset\":[], \"Shot\":[[\"sg_status_list\",\"is\",\"ip\"]]}"} Optional: {"limit": 10}
    • summarize — Server-side aggregation (count, sum, avg, min, max) with optional grouping. Required params: {"entity_type": "Task", "filters": "[[\"sg_status_list\",\"is\",\"ip\"]]", "summary_fields": "[{\"field\": \"duration\", \"type\": \"sum\"}]"} Optional: {"grouping": "[{\"field\": \"sg_status_list\", \"type\": \"exact\"}]"}
    • note_thread — Read the full reply thread of a Note. Required params: {"note_id": 123}
    • activity — Read the activity stream for an entity. Required params: {"entity_type": "Shot", "entity_id": 456} Optional: {"limit": 20}
    """
    _track_call()
    dispatch = {
        ReportingAction.TEXT_SEARCH: _do_sg_text_search,
        ReportingAction.SUMMARIZE: _do_sg_summarize,
        ReportingAction.NOTE_THREAD: _do_sg_note_thread,
        ReportingAction.ACTIVITY: _do_sg_activity,
    }
    handler = dispatch[params.action]
    _stats["exec_calls"] += 1
    params_str = json.dumps(params.params or {}, default=str)
    _stats["tokens_in"] += _tok(params_str)
    _t0 = time.monotonic()
    out = await handler(params.params or {})
    _count_turn(out, "fpt_reporting", _t0)
    _stats["tokens_out"] += _tok(out)
    return out


# ---------------------------------------------------------------------------
# RAG tools — search_sg_docs, learn_pattern, session_stats
# ---------------------------------------------------------------------------

@mcp.tool(name="search_sg_docs")
async def search_sg_docs_tool(params: SearchSgDocsInput) -> str:
    """Search ShotGrid API documentation using hybrid RAG (semantic + BM25).

    Call this BEFORE writing complex queries, using unfamiliar filters,
    or when unsure about entity format, template tokens, or operator names.
    Returns the most relevant documentation chunks with relevance scores.

    Covers three APIs: shotgun_api3 (Python SDK), Toolkit (sgtk), REST API.
    Uses HyDE query expansion + Reciprocal Rank Fusion for high precision.
    """
    from fpt_mcp.rag_tools import search_sg_docs_impl
    return await search_sg_docs_impl(params)


@mcp.tool(name="learn_pattern")
async def learn_pattern_tool(params: LearnPatternInput) -> str:
    """Save a validated working pattern to the RAG knowledge base.

    Call this after a successful operation when search_sg_docs returned
    low relevance (< 60%), indicating the pattern was not well-documented.
    The pattern will be available in future sessions.

    Model trust gates: only Sonnet/Opus can write directly.
    Other models stage candidates for review.
    """
    from fpt_mcp.rag_tools import learn_pattern_impl
    return await learn_pattern_impl(params)


@mcp.tool(name="session_stats")
async def session_stats_tool() -> str:
    """Show session efficiency statistics: token usage, RAG savings, patterns learned.

    Call at the end of multi-step tasks or when asked about efficiency.
    Shows how much context was saved by RAG vs loading full documentation.
    """
    _track_call()
    used = _stats["tokens_in"] + _stats["tokens_out"]
    saved = _stats["tokens_saved"]
    total = used + saved
    ratio = f"{saved / total * 100:.0f}%" if total > 0 else "—"
    uptime = str(datetime.datetime.now() - _stats_reset_at).split(".")[0]

    # Pull RAG cache counters from the rag.search module so the report
    # reflects what actually happened (cache_hits in _stats was historically
    # never incremented — the rag.search module owns the cache).
    cache_stats: dict[str, int] = {}
    try:
        from fpt_mcp.rag.search import get_cache_stats
        cache_stats = get_cache_stats()
    except ImportError:
        pass

    # F0: p_fallo = failed_turns / turns_total over the dispatcher operations.
    turns = _stats["turns_total"]
    failed = _stats["failed_turns"]
    p_fallo = f"{failed / turns * 100:.0f}%" if turns > 0 else "—"

    return json.dumps({
        "session_duration": uptime,
        "tool_calls": _stats["exec_calls"],
        "rag_calls": _stats["rag_calls"],
        "rag_skipped": _stats.get("rag_skipped", 0),
        "tokens_used": used,
        "tokens_saved_by_rag": saved,
        "token_efficiency": ratio,
        "patterns_learned": _stats["patterns_learned"],
        "patterns_staged": _stats["patterns_staged"],
        "safety_blocks": _stats["safety_blocks"],
        "cache_hits": cache_stats.get("cache_hits", 0),
        "cache_misses": cache_stats.get("cache_misses", 0),
        "cache_size": cache_stats.get("cache_size", 0),
        "dispatcher_turns": turns,
        "failed_turns": failed,
        "p_fallo": p_fallo,
        "full_doc_baseline": _FULL_DOC_TOKENS,
    }, indent=2)


@mcp.tool(name="reset_session_stats")
async def reset_session_stats_tool() -> str:
    """Zero the session stats counters immediately.

    Use at the start of a new Claude session (or a fresh debugging run) when
    the idle-based auto-reset has not fired — for example when two sessions
    happen back-to-back. Returns a confirmation line with the new reset
    timestamp.
    """
    global _stats_reset_at
    _track_call()
    now = datetime.datetime.now()
    _stats_reset_at = _reset_stats_helper(_stats, now)
    return json.dumps({
        "status": "reset",
        "reset_at": now.strftime("%H:%M:%S"),
    }, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="FPT MCP Server")
    parser.add_argument("--http", action="store_true", help="Run as HTTP server instead of stdio")
    parser.add_argument("--port", type=int, default=8090, help="HTTP port (default: 8090)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="HTTP host (default: 127.0.0.1)")
    args = parser.parse_args()

    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.settings.stateless_http = True
        mcp.settings.json_response = True
        mcp.settings.transport_security = None
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
