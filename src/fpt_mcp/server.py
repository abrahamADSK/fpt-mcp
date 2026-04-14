#!/usr/bin/env python3
"""FPT MCP Server — Full Flow Production Tracking (ShotGrid) + Toolkit + RAG.

General-purpose MCP server exposing the complete ShotGrid API, Toolkit
path conventions, and RAG-powered documentation search.

Features:
    - 8 Tier-1 ShotGrid/Toolkit tools (always visible)
    - 3 bulk tools (behind fpt_bulk dispatch: delete, revive, batch)
    - 4 reporting tools (behind fpt_reporting dispatch: text_search, summarize, note_thread, activity)
    - 3 RAG tools (search_sg_docs, learn_pattern, session_stats)
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
from pathlib import Path
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, ConfigDict, field_validator
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

_SERVER_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Token tracking (REC-001 — from flame-mcp proven architecture)
# ---------------------------------------------------------------------------

_FULL_DOC_TOKENS = 13000  # combined size of all indexed docs

_stats = {
    "exec_calls": 0,       # total tool calls
    "tokens_in": 0,        # tokens in parameters
    "tokens_out": 0,       # tokens in responses
    "rag_calls": 0,        # search_sg_docs calls
    "rag_skipped": 0,      # tools called without prior search_sg_docs (soft warning)
    "tokens_saved": 0,     # tokens saved by RAG vs loading full doc
    "patterns_learned": 0, # patterns added to docs
    "patterns_staged": 0,  # candidates staged by non-trusted models
    "safety_blocks": 0,    # dangerous pattern detections
    # Note: cache_hits is now tracked inside rag.search and surfaced via
    # session_stats_tool by importing get_cache_stats(). See C.1 fix.
}
_stats_reset_at = datetime.datetime.now()

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


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

# Shared strict config for every input model. extra="forbid" makes the
# schema reject hallucinated keys at validation time (rather than silently
# accepting them and forwarding garbage to ShotGrid). str_strip_whitespace
# normalises accidental leading/trailing whitespace from LLM output.
_STRICT_CONFIG = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Filter operator enum (C.4)
# ---------------------------------------------------------------------------
#
# Canonical list of valid ShotGrid filter operators. Sourced from the
# shotgun_api3 documentation (docs/SG_API.md). Used by the SgFindInput
# filter validator (C.3) to reject hallucinated operators at the MCP layer
# instead of letting them fail at the ShotGrid API layer with a confusing
# error.
#
# We use a frozenset of strings rather than an Enum so the validator can
# accept the operator either as a literal string in the filter triple
# (the natural shape from JSON) without forcing the LLM to use enum
# member syntax.

_VALID_FILTER_OPERATORS: frozenset[str] = frozenset({
    # Equality / containment
    "is", "is_not",
    "in", "not_in",
    # String matching
    "contains", "not_contains",
    "starts_with", "ends_with",
    # Numeric / date comparison
    "less_than", "greater_than", "between", "not_between",
    "in_last", "not_in_last", "in_next", "not_in_next",
    "in_calendar_day", "in_calendar_week",
    "in_calendar_month", "in_calendar_year",
    # Type-aware
    "type_is", "type_is_not",
    # Name matching (multi-entity fields)
    "name_contains", "name_not_contains", "name_starts_with", "name_ends_with",
    "name_is",
})


def _validate_filter_triples(filters: list) -> list:
    """C.3 — structural validation for ShotGrid filter lists.

    Walks every filter and rejects:
      - Filters that are not a 3-element list/tuple [field, operator, value]
      - Operators that are not in _VALID_FILTER_OPERATORS (catches the
        hallucinated 'is_exactly', 'matches', 'like', etc. that the LLM
        invents when it skips search_sg_docs)
      - Entity references passed as bare integers/strings instead of the
        canonical {"type": "...", "id": N} dict (the single most common
        ShotGrid hallucination, per the audit)

    Allows logical groupings of the form
      {"filter_operator": "any"|"all", "filters": [...]}
    by recursing into the nested filters list. This is the canonical
    ShotGrid syntax for OR/AND blocks.

    Note: this complements safety.py's regex check, which only catches
    JSON-key-value entity refs ("entity": 123) and misses the array form
    ([["entity", "is", 123]]) that filter lists actually use.
    """
    # Field names that are typed as entity links and therefore require
    # a {"type": ..., "id": ...} dict on the value side. This is a
    # conservative subset — there are more entity-link fields in custom
    # schemas, but these are the universal ones.
    _entity_link_fields = {
        "entity", "project", "task", "user", "asset", "shot",
        "sequence", "version", "playlist", "step", "published_file",
        "parent", "children", "linked_versions",
    }

    def _is_entity_dict(value: Any) -> bool:
        return (
            isinstance(value, dict)
            and "type" in value
            and "id" in value
            and isinstance(value["id"], int)
            and isinstance(value["type"], str)
        )

    for idx, f in enumerate(filters):
        # Logical grouping: {"filter_operator": "all"|"any", "filters": [...]}
        if isinstance(f, dict):
            if "filter_operator" in f and "filters" in f:
                op = f.get("filter_operator")
                if op not in ("all", "any"):
                    raise ValueError(
                        f"filter[{idx}]: invalid filter_operator '{op}' "
                        "(must be 'all' or 'any')"
                    )
                if not isinstance(f["filters"], list):
                    raise ValueError(
                        f"filter[{idx}]: 'filters' inside a logical group must be a list"
                    )
                _validate_filter_triples(f["filters"])
                continue
            raise ValueError(
                f"filter[{idx}]: dict filters must have 'filter_operator' "
                "and 'filters' keys (logical grouping)"
            )

        # Triple: [field, operator, value]
        if not isinstance(f, (list, tuple)) or len(f) != 3:
            raise ValueError(
                f"filter[{idx}]: each filter must be a 3-element list "
                f"[field, operator, value], got {type(f).__name__} of length "
                f"{len(f) if hasattr(f, '__len__') else 'n/a'}"
            )
        field, op, value = f[0], f[1], f[2]

        if not isinstance(field, str):
            raise ValueError(
                f"filter[{idx}]: field name must be a string, got {type(field).__name__}"
            )
        if not isinstance(op, str):
            raise ValueError(
                f"filter[{idx}]: operator must be a string, got {type(op).__name__}"
            )
        if op not in _VALID_FILTER_OPERATORS:
            raise ValueError(
                f"filter[{idx}]: invalid operator '{op}'. "
                f"Valid operators: {sorted(_VALID_FILTER_OPERATORS)}. "
                "Common hallucinations: 'is_exactly', 'matches', 'like', "
                "'before_date' — none of these exist in the ShotGrid API."
            )

        # Entity-link field validation: if the field looks like an entity
        # link, the value must be a dict (or a list of dicts for 'in').
        if field in _entity_link_fields and op in ("is", "is_not"):
            if not _is_entity_dict(value):
                raise ValueError(
                    f"filter[{idx}]: field '{field}' is an entity link, "
                    f"value must be {{'type': '...', 'id': N}}, got {value!r}. "
                    "Bare integers and strings are not accepted by ShotGrid."
                )
        if field in _entity_link_fields and op in ("in", "not_in"):
            if not isinstance(value, list) or not all(_is_entity_dict(v) for v in value):
                raise ValueError(
                    f"filter[{idx}]: field '{field}' with operator '{op}' "
                    "requires a list of entity dicts, got "
                    f"{value!r}."
                )

    return filters


class SgFindInput(BaseModel):
    model_config = _STRICT_CONFIG
    entity_type: str = Field(description="ShotGrid entity type: Asset, Shot, Sequence, Version, Task, Note, PublishedFile, HumanUser, Project, etc.")
    filters: list = Field(default_factory=list, description="ShotGrid filter list. Examples: [['sg_status_list','is','ip']], [['id','is',1234]], [['code','contains','hero']], [['entity','is',{'type':'Asset','id':123}]]. Use [] for no filter.")
    fields: list[str] = Field(default_factory=lambda: ["id", "code", "sg_status_list"], description="Fields to return. Use sg_schema to discover available fields.")
    order: list[dict] = Field(default_factory=list, description="Sort order. Example: [{'field_name':'created_at','direction':'desc'}]")
    limit: int = Field(default=50, description="Max results to return. 0 = unlimited.")
    add_project_filter: bool = Field(default=True, description="Auto-add project filter from SHOTGRID_PROJECT_ID env var.")

    @field_validator("filters")
    @classmethod
    def _validate_filters(cls, v: list) -> list:
        return _validate_filter_triples(v)

class SgCreateInput(BaseModel):
    model_config = _STRICT_CONFIG
    entity_type: str = Field(description="Entity type to create: Asset, Shot, Sequence, Task, Version, Note, PublishedFile, etc.")
    data: dict[str, Any] = Field(description="Field values. Example: {'code':'HERO','sg_asset_type':'Character','description':'Main character'}. Project is auto-added if not specified.")

class SgUpdateInput(BaseModel):
    model_config = _STRICT_CONFIG
    entity_type: str = Field(description="Entity type: Asset, Shot, Version, Task, etc.")
    entity_id: int = Field(description="ID of the entity to update.")
    data: dict[str, Any] = Field(description="Fields to update. Example: {'sg_status_list':'cmpt','description':'Final version'}.")

class SgDeleteInput(BaseModel):
    model_config = _STRICT_CONFIG
    entity_type: str = Field(description="Entity type to delete.")
    entity_id: int = Field(description="ID of the entity to delete.")

class SgSchemaInput(BaseModel):
    model_config = _STRICT_CONFIG
    entity_type: str = Field(description="Entity type to inspect: Asset, Shot, Task, Version, PublishedFile, etc.")
    field_name: Optional[str] = Field(default=None, description="Specific field to inspect. Omit to get all fields.")

class SgUploadInput(BaseModel):
    model_config = _STRICT_CONFIG
    entity_type: str = Field(description="Entity type to upload to.")
    entity_id: int = Field(description="Entity ID.")
    file_path: str = Field(description="Local path to the file to upload.")
    field_name: str = Field(default="image", description="Target field: 'image' for thumbnail, 'sg_uploaded_movie' for movie, etc.")
    display_name: Optional[str] = Field(default=None, description="Display name for the attachment.")

class SgDownloadInput(BaseModel):
    model_config = _STRICT_CONFIG
    entity_type: str = Field(description="Entity type.")
    entity_id: int = Field(description="Entity ID.")
    field_name: str = Field(default="image", description="Field containing the attachment: 'image', 'sg_uploaded_movie', etc.")
    download_path: str = Field(description="Local path where to save the file.")

class TkResolvePathInput(BaseModel):
    model_config = _STRICT_CONFIG
    entity_type: str = Field(description="'Asset' or 'Shot'.")
    entity_id: int = Field(description="Entity ID in ShotGrid. Used to auto-fetch entity context (code, asset_type, sequence).")
    template_name: str = Field(
        description=(
            "Name of the template from the project's templates.yml "
            "(e.g. 'maya_asset_publish', 'nuke_shot_publish'). "
            "Use search_sg_docs to find available templates."
        ),
    )
    step: str = Field(default="model", description="Pipeline step: model, rig, texture, anim, light, comp, etc.")
    name: str = Field(default="main", description="Publish name (e.g. 'main', 'turntable', 'hero_robot').")
    version: Optional[int] = Field(default=None, description="Version number. Auto-incremented if omitted.")
    extension: Optional[str] = Field(default=None, description="File extension override (e.g. 'ma', 'mb'). Only needed for templates with extension tokens.")

class TkPublishInput(BaseModel):
    model_config = _STRICT_CONFIG
    entity_type: str = Field(description="'Asset' or 'Shot'.")
    entity_id: int = Field(description="Entity ID in ShotGrid.")
    publish_type: str = Field(
        description=(
            "PublishedFileType code in ShotGrid (e.g. 'Maya Scene', 'Nuke Script', "
            "'Alembic Cache', 'Image'). Created automatically if it doesn't exist."
        ),
    )
    step: str = Field(default="model", description="Pipeline step: model, rig, texture, anim, light, comp, etc.")
    name: str = Field(default="main", description="Publish name.")
    comment: Optional[str] = Field(default=None, description="Publish comment/notes.")
    local_path: Optional[str] = Field(default=None, description="Source file path. Copied to the resolved publish location if a PipelineConfiguration exists.")
    publish_path: Optional[str] = Field(
        default=None,
        description=(
            "Explicit publish path. Required when the project has no PipelineConfiguration. "
            "The file at local_path (if provided) is copied here. "
            "This path is stored in the PublishedFile entity."
        ),
    )
    version_number: Optional[int] = Field(default=None, description="Explicit version. Auto-incremented from existing files if omitted (requires PipelineConfiguration).")
    extension: Optional[str] = Field(default=None, description="File extension override.")


# ---------------------------------------------------------------------------
# Dispatch Models
# ---------------------------------------------------------------------------

class BulkAction(str, Enum):
    """Actions available in the fpt_bulk dispatch tool."""
    DELETE = "delete"
    REVIVE = "revive"
    BATCH = "batch"


class BulkDispatchInput(BaseModel):
    """Input for the fpt_bulk dispatch tool."""
    model_config = _STRICT_CONFIG

    action: BulkAction = Field(..., description="Which bulk action to run")
    params: Optional[dict] = Field(default=None, description="Parameters for the chosen action (see tool description)")


class ReportingAction(str, Enum):
    """Actions available in the fpt_reporting dispatch tool."""
    TEXT_SEARCH = "text_search"
    SUMMARIZE = "summarize"
    NOTE_THREAD = "note_thread"
    ACTIVITY = "activity"


class ReportingDispatchInput(BaseModel):
    """Input for the fpt_reporting dispatch tool."""
    model_config = _STRICT_CONFIG

    action: ReportingAction = Field(..., description="Which reporting action to run")
    params: Optional[dict] = Field(default=None, description="Parameters for the chosen action (see tool description)")


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
    _stats["exec_calls"] += 1

    # Safety check
    params_str = json.dumps({"filters": params.filters, "limit": params.limit}, default=str)
    _stats["tokens_in"] += _tok(params_str)
    warning = check_dangerous(params_str)
    if warning:
        _stats["safety_blocks"] += 1
        return json.dumps({"safety_warning": warning})

    filters = list(params.filters)
    if params.add_project_filter and PROJECT_ID:
        filters.append(["project", "is", {"type": "Project", "id": PROJECT_ID}])

    results = await sg_find(
        params.entity_type, filters, params.fields,
        order=params.order, limit=params.limit,
    )
    payload: dict[str, Any] = {"total": len(results), "entities": results}
    warning = _rag_skipped_warning()
    if warning:
        payload.update(warning)
    response = json.dumps(payload, default=str)
    _stats["tokens_out"] += _tok(response)
    return response


@mcp.tool(name="sg_create")
async def sg_create_tool(params: SgCreateInput) -> str:
    """Create any entity in ShotGrid.

    Works with ALL entity types. Project is auto-linked if SHOTGRID_PROJECT_ID
    is set and 'project' is not in the data dict.
    """
    _stats["exec_calls"] += 1

    data = dict(params.data)
    if "project" not in data and PROJECT_ID:
        data["project"] = {"type": "Project", "id": PROJECT_ID}

    # Safety check (catches integer entity refs in nested data, etc.)
    params_str = json.dumps({"entity_type": params.entity_type, "data": data}, default=str)
    _stats["tokens_in"] += _tok(params_str)
    safety_warning = check_dangerous(params_str)
    if safety_warning:
        _stats["safety_blocks"] += 1
        return json.dumps({"safety_warning": safety_warning})

    result = await sg_create(params.entity_type, data)
    payload: dict[str, Any] = {"created": result} if not isinstance(result, dict) else dict(result)
    warning = _rag_skipped_warning()
    if warning:
        payload.update(warning)
    response = json.dumps(payload, default=str)
    _stats["tokens_out"] += _tok(response)
    return response


@mcp.tool(name="sg_update")
async def sg_update_tool(params: SgUpdateInput) -> str:
    """Update any entity's fields in ShotGrid."""
    _stats["exec_calls"] += 1

    # Safety check (catches dangerous status codes, integer entity refs, etc.)
    params_str = json.dumps(
        {"entity_type": params.entity_type, "entity_id": params.entity_id, "data": params.data},
        default=str,
    )
    _stats["tokens_in"] += _tok(params_str)
    safety_warning = check_dangerous(params_str)
    if safety_warning:
        _stats["safety_blocks"] += 1
        return json.dumps({"safety_warning": safety_warning})

    result = await sg_update(params.entity_type, params.entity_id, params.data)
    payload: dict[str, Any] = {"updated": result} if not isinstance(result, dict) else dict(result)
    warning = _rag_skipped_warning()
    if warning:
        payload.update(warning)
    response = json.dumps(payload, default=str)
    _stats["tokens_out"] += _tok(response)
    return response


async def _do_sg_delete(params: dict) -> str:
    """Delete (retire) an entity in ShotGrid. Soft-delete — can be restored from trash."""
    from pydantic import ValidationError
    try:
        validated = SgDeleteInput(**params)
    except ValidationError as e:
        return json.dumps({"error": f"Invalid params for delete: {e}"})

    _stats["exec_calls"] += 1

    # Safety check
    params_str = json.dumps({"entity_type": validated.entity_type, "entity_id": validated.entity_id})
    _stats["tokens_in"] += _tok(params_str)
    safety_warning = check_dangerous(params_str)
    if safety_warning:
        _stats["safety_blocks"] += 1
        return json.dumps({"safety_warning": safety_warning})

    sg = get_sg()
    import asyncio
    result = await asyncio.to_thread(sg.delete, validated.entity_type, validated.entity_id)
    payload: dict[str, Any] = {
        "deleted": result,
        "entity_type": validated.entity_type,
        "entity_id": validated.entity_id,
    }
    rag_warning = _rag_skipped_warning()
    if rag_warning:
        payload.update(rag_warning)
    response = json.dumps(payload)
    _stats["tokens_out"] += _tok(response)
    return response


@mcp.tool(name="sg_schema")
async def sg_schema_tool(params: SgSchemaInput) -> str:
    """Get the field schema for any ShotGrid entity type.

    Returns field names, types, and properties. Use this to discover
    what fields are available before querying or creating entities.
    """
    schema = await sg_schema_field_read(params.entity_type, params.field_name)
    # Simplify output for readability
    summary = {}
    for field_name, info in schema.items():
        summary[field_name] = {
            "type": info.get("data_type", {}).get("value", "unknown"),
            "label": info.get("name", {}).get("value", field_name),
            "editable": info.get("editable", {}).get("value", False),
        }
    return json.dumps(summary, default=str)


@mcp.tool(name="sg_upload")
async def sg_upload_tool(params: SgUploadInput) -> str:
    """Upload a file to any entity field in ShotGrid.

    Use field_name='image' for thumbnails, 'sg_uploaded_movie' for movies,
    or any file/url field.
    """
    if params.field_name == "image":
        result_id = await sg_upload_thumbnail(params.entity_type, params.entity_id, params.file_path)
    else:
        result_id = await sg_upload(
            params.entity_type, params.entity_id, params.file_path,
            params.field_name, params.display_name,
        )
    return json.dumps({
        "attachment_id": result_id,
        "entity_type": params.entity_type,
        "entity_id": params.entity_id,
        "field": params.field_name,
    })


@mcp.tool(name="sg_download")
async def sg_download_tool(params: SgDownloadInput) -> str:
    """Download an attachment from any entity field in ShotGrid."""
    entity = await sg_find_one(
        params.entity_type,
        [["id", "is", params.entity_id]],
        [params.field_name],
    )
    if not entity or not entity.get(params.field_name):
        return json.dumps({"error": f"No attachment in {params.field_name} for {params.entity_type} #{params.entity_id}"})

    attachment = entity[params.field_name]
    path = await sg_download_attachment(attachment, params.download_path)
    return json.dumps({"path": path, "entity_type": params.entity_type, "entity_id": params.entity_id})


async def _get_tk_config():
    """Get or discover the TkConfig for the current project.

    Returns TkConfig if the project has a PipelineConfiguration, None otherwise.
    """
    if not PROJECT_ID:
        return None
    return await discover_or_fallback(PROJECT_ID, sg_find)


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

    template_fields: dict[str, Any] = {
        "Step": step,
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
    try:
        tk_config = await _get_tk_config()
        if tk_config is None:
            return json.dumps({
                "error": "No PipelineConfiguration found for this project. "
                         "Cannot resolve Toolkit paths without a pipeline config. "
                         "Use an explicit publish_path in tk_publish instead."
            })

        # Build fields from SG entity context
        version = params.version
        if version is None:
            fields_probe = await _build_template_fields(
                params.entity_type, params.entity_id,
                params.step, params.name, 0, params.extension,
            )
            version = tk_config.next_version(params.template_name, fields_probe)

        fields = await _build_template_fields(
            params.entity_type, params.entity_id,
            params.step, params.name, version, params.extension,
        )

        path = tk_config.resolve_path(params.template_name, fields)

        return json.dumps({
            "path": str(path),
            "version": version,
            "template": params.template_name,
            "project_root": str(tk_config.project_root),
        })

    except TkConfigError as e:
        return json.dumps({"error": str(e)})


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
    try:
        tk_config = await _get_tk_config()
        publish_path = None
        version = params.version_number or 1
        template_name = None

        if tk_config is not None and params.publish_path is None:
            # Mode 1: Resolve path from PipelineConfiguration templates
            # Caller should provide a template_name-like approach, but for
            # tk_publish we infer from publish_type + entity_type
            # Try to find a matching template by convention
            entity_key = "asset" if params.entity_type == "Asset" else "shot"
            # Search templates for one matching the publish type
            ptype_lower = params.publish_type.lower().replace(" ", "_")
            candidates = [
                f"{ptype_lower}_{entity_key}_publish",
                f"{entity_key}_{ptype_lower}_publish",
                f"{ptype_lower}_{entity_key}",
            ]
            for candidate in candidates:
                if tk_config.get_template(candidate):
                    template_name = candidate
                    break

            if template_name is None:
                # Try listing templates for a partial match
                all_templates = tk_config.list_templates(ptype_lower)
                if all_templates:
                    template_name = next(iter(all_templates))

            if template_name is None:
                return json.dumps({
                    "error": f"No template found matching publish_type='{params.publish_type}' "
                             f"for entity_type='{params.entity_type}'. "
                             f"Available templates: {list(tk_config.list_templates().keys())}. "
                             f"Provide an explicit publish_path instead."
                })

            ext = params.extension
            if version == 1 and params.version_number is None:
                fields_probe = await _build_template_fields(
                    params.entity_type, params.entity_id,
                    params.step, params.name, 0, ext,
                )
                version = tk_config.next_version(template_name, fields_probe)

            fields = await _build_template_fields(
                params.entity_type, params.entity_id,
                params.step, params.name, version, ext,
            )
            publish_path = tk_config.resolve_path(template_name, fields)

        elif params.publish_path is not None:
            # Mode 2: Explicit path provided by user. Resolve to absolute
            # so the existence check below and the error messages are not
            # ambiguous when the LLM passes a relative path (which would
            # otherwise be evaluated against the MCP server's cwd).
            from pathlib import Path as _Path
            publish_path = _Path(params.publish_path).resolve()

        else:
            return json.dumps({
                "error": "No PipelineConfiguration found and no publish_path provided. "
                         "Please provide an explicit publish_path where the file should be published."
            })

        # Pre-flight: if local_path is provided, it must actually exist on
        # disk before we resolve PublishedFileType / Task / etc. Catching
        # this early avoids a partial publish where the SG record is created
        # but the file copy fails halfway through.
        if params.local_path and not os.path.isfile(params.local_path):
            return json.dumps({
                "error": f"local_path does not exist: {params.local_path}. "
                         "Provide a valid path to the source file, or omit "
                         "local_path to register an already-published file."
            })

        # Pre-flight: in Mode 2 (explicit publish_path), if no local_path
        # was given the publish_path itself must already exist on disk.
        # Otherwise we'd be creating a PublishedFile record pointing at
        # nothing — a silent failure that surfaces far from the cause.
        if (
            params.publish_path is not None
            and not params.local_path
            and publish_path
            and not publish_path.exists()
        ):
            return json.dumps({
                "error": f"publish_path does not exist on disk and no local_path "
                         f"was provided to copy from: {publish_path}. "
                         "Either pass local_path to copy the file, or ensure "
                         "the file already exists at publish_path."
            })

        # Copy source file if provided
        if params.local_path and publish_path:
            import shutil
            publish_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(params.local_path, str(publish_path))

        # Find or create PublishedFileType
        pft = await sg_find_one(
            "PublishedFileType",
            [["code", "is", params.publish_type]],
            ["id", "code"],
        )
        if not pft:
            pft = await sg_create("PublishedFileType", {"code": params.publish_type})

        # Fetch entity code for the publish name
        entity = await sg_find_one(
            params.entity_type,
            [["id", "is", params.entity_id]],
            ["code"],
        )
        entity_code = entity["code"] if entity else f"{params.entity_type}_{params.entity_id}"

        # Find linked Task (if exists)
        task = await sg_find_one(
            "Task",
            [
                ["entity", "is", {"type": params.entity_type, "id": params.entity_id}],
                ["step.Step.short_name", "is", params.step],
            ],
            ["id", "content"],
        )

        # Create the PublishedFile
        data: dict[str, Any] = {
            "code": f"{entity_code}_{params.step}_{params.publish_type.replace(' ', '_')}_v{version:03d}",
            "published_file_type": {"type": "PublishedFileType", "id": pft["id"]},
            "entity": {"type": params.entity_type, "id": params.entity_id},
            "path": {"local_path": str(publish_path)},
            "version_number": version,
            "sg_status_list": "wtg",
        }
        if task:
            data["task"] = {"type": "Task", "id": task["id"]}
        if params.comment:
            data["description"] = params.comment
        if PROJECT_ID:
            data["project"] = {"type": "Project", "id": PROJECT_ID}

        result = await sg_create("PublishedFile", data)

        response = {
            "id": result["id"],
            "code": data["code"],
            "path": str(publish_path),
            "version_number": version,
            "entity": {"type": params.entity_type, "id": params.entity_id, "code": entity_code},
            "publish_type": params.publish_type,
            "task": task["content"] if task else None,
            "file_copied": params.local_path is not None,
        }
        if template_name:
            response["template"] = template_name
            response["project_root"] = str(tk_config.project_root)

        return json.dumps(response, default=str)

    except TkConfigError as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Software launcher — locate DCC applications and launch them in context
# ---------------------------------------------------------------------------


class FptLaunchAppInput(BaseModel):
    model_config = _STRICT_CONFIG
    app: str = Field(
        description=(
            "App to launch, case-insensitive. Supported: 'maya'. "
            "Other DCCs (nuke, houdini, flame) are resolvable but not "
            "yet wired for context launch — they fall back to 'open'."
        )
    )
    entity_type: str = Field(
        description=(
            "ShotGrid entity type the launch is scoped to. One of "
            "'Asset', 'Shot', 'Sequence', 'Task'."
        )
    )
    entity_id: int = Field(
        description="ShotGrid entity id."
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "If true, resolve the app and return the launch plan WITHOUT "
            "spawning the process. Useful for UI previews and tests."
        ),
    )


def _project_id_for_entity(entity_type: str, entity_id: int) -> Optional[int]:
    """Resolve the Project id that owns a given entity.

    Project entities return their own id. For everything else, we look up
    the ``project`` field. Returns ``None`` on SG errors so the caller can
    degrade gracefully to a bare OS-scan result.
    """
    if entity_type == "Project":
        return entity_id
    try:
        sg = get_sg()
        row = sg.find_one(
            entity_type, [["id", "is", entity_id]], ["project"]
        )
    except Exception:
        return None
    if not row or not row.get("project"):
        return None
    return row["project"].get("id")


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
    import subprocess

    sg = get_sg()
    project_id = _project_id_for_entity(params.entity_type, params.entity_id)

    result = resolve_app(
        params.app,
        project_id=project_id,
        sg_find=sg.find,
    )
    if result is None:
        return json.dumps({
            "error": (
                f"{params.app} is not installed on this machine; cannot "
                f"launch. Install the app first and retry."
            )
        })

    plan: dict[str, Any] = {
        "app": result.app,
        "binary": str(result.binary),
        "version": result.version,
        "engine": result.engine,
        "launch_method": result.launch_method,
        "tank_command": (
            str(result.tank_command) if result.tank_command else None
        ),
        "pipeline_config_path": (
            str(result.pipeline_config_path)
            if result.pipeline_config_path
            else None
        ),
        "entity_type": params.entity_type,
        "entity_id": params.entity_id,
        "project_id": project_id,
        "source_layers": result.source_layers,
        "warnings": list(result.warnings),
    }

    if result.launch_method == "tank" and result.tank_command is not None:
        # tk-multi-launchapp registers its command under two common
        # conventions depending on the pipeline:
        #   1. launch_<app>      — default, single DCC version per config
        #   2. <app>_<version>   — multi-version pipelines that register
        #                          one launcher per installed version
        # We prefer pattern 2 when we have a version string from the OS
        # scan, since it is unambiguous across pipelines that expose both
        # a specific Maya release and legacy generic launchers. Callers
        # whose pipeline uses a non-standard convention should launch
        # Maya via a wrapper that maps to the right tank command.
        if result.version:
            cmd_name = f"{result.app}_{result.version}"
        else:
            cmd_name = f"launch_{result.app}"
        argv = [
            str(result.tank_command),
            params.entity_type,
            str(params.entity_id),
            cmd_name,
        ]
    else:
        argv = ["open", "-a", str(result.binary)]
        if result.launch_method != "tank":
            plan["warnings"].append(
                "launching without Toolkit context (no tank CLI); the app "
                "will open but not in the selected entity context"
            )

    plan["argv"] = argv

    if params.dry_run:
        plan["dry_run"] = True
        return json.dumps(plan, default=str)

    try:
        proc = subprocess.Popen(argv, start_new_session=True)
        plan["pid"] = proc.pid
    except Exception as exc:
        plan["error"] = f"launch failed: {exc}"

    return json.dumps(plan, default=str)


# ---------------------------------------------------------------------------
# Additional SG API tools — batch, text_search, summarize, revive,
# note_thread, activity_stream
# ---------------------------------------------------------------------------

class SgBatchInput(BaseModel):
    model_config = _STRICT_CONFIG
    requests: str = Field(
        description=(
            "JSON array of batch requests. Each request is an object with: "
            "'request_type' ('create'|'update'|'delete'), 'entity_type', "
            "and either 'data' (create/update) or 'entity_id' (update/delete). "
            "Example: [{\"request_type\":\"create\",\"entity_type\":\"Shot\","
            "\"data\":{\"code\":\"SH010\",\"project\":{\"type\":\"Project\",\"id\":123}}}]"
        ),
    )


async def _do_sg_batch(params: dict) -> str:
    """Execute multiple ShotGrid operations in a single transactional call.

    ALL operations succeed or ALL fail — no partial results.
    Supports create, update, and delete in a single batch.
    Much more efficient than individual calls for bulk operations.
    """
    from pydantic import ValidationError
    try:
        validated = SgBatchInput(**params)
    except ValidationError as e:
        return json.dumps({"error": f"Invalid params for batch: {e}"})

    _stats["exec_calls"] += 1
    _stats["tokens_in"] += _tok(validated.requests)
    # Safety check
    safety_warning = check_dangerous(validated.requests)
    if safety_warning:
        _stats["safety_blocks"] += 1
        return safety_warning
    batch_data = json.loads(validated.requests)
    results = await sg_batch(batch_data)
    # Note: sg_batch returns a raw list to preserve the existing tool
    # contract (callers expect a JSON array). The RAG soft-warning
    # is intentionally NOT applied here — sg_batch is already gated by
    # the dangerous-pattern check above and is used by workflows that
    # already know the schema.
    out = json.dumps(results, default=str)
    _stats["tokens_out"] += _tok(out)
    return out


class SgTextSearchInput(BaseModel):
    model_config = _STRICT_CONFIG
    text: str = Field(description="Search text — searches across all text fields of the specified entity types.")
    entity_types: str = Field(
        description=(
            "JSON object mapping entity type names to filter lists. "
            "Example: {\"Asset\":[], \"Shot\":[[\"sg_status_list\",\"is\",\"ip\"]]}"
        ),
    )
    limit: int = Field(default=10, description="Max results per entity type.")


async def _do_sg_text_search(params: dict) -> str:
    """Full-text search across multiple entity types simultaneously.

    Unlike sg_find which searches field-by-field, text_search looks
    across all text fields (code, description, notes, etc.) at once.
    Useful for finding entities when you only have a keyword.
    """
    from pydantic import ValidationError
    try:
        validated = SgTextSearchInput(**params)
    except ValidationError as e:
        return json.dumps({"error": f"Invalid params for text_search: {e}"})

    _stats["exec_calls"] += 1
    _stats["tokens_in"] += _tok(validated.text) + _tok(validated.entity_types)
    entity_types = json.loads(validated.entity_types)
    project_ids = [PROJECT_ID] if PROJECT_ID else None
    results = await sg_text_search(validated.text, entity_types, project_ids=project_ids, limit=validated.limit)
    out = json.dumps(results, default=str)
    _stats["tokens_out"] += _tok(out)
    return out


class SgSummarizeInput(BaseModel):
    model_config = _STRICT_CONFIG
    entity_type: str = Field(description="Entity type to aggregate (e.g. 'Task', 'TimeLog', 'Version').")
    filters: str = Field(description="JSON array of ShotGrid filters. Same syntax as sg_find.")
    summary_fields: str = Field(
        description=(
            "JSON array of aggregation specs. Each: {\"field\":\"field_name\",\"type\":\"agg_type\"}. "
            "Types: 'count', 'sum', 'avg', 'min', 'max', 'count_distinct'. "
            "Example: [{\"field\":\"id\",\"type\":\"count\"},{\"field\":\"duration\",\"type\":\"sum\"}]"
        ),
    )
    grouping: Optional[str] = Field(
        default=None,
        description=(
            "JSON array of grouping specs. Each: {\"field\":\"field_name\",\"type\":\"exact\",\"direction\":\"asc\"}. "
            "Example: [{\"field\":\"sg_status_list\",\"type\":\"exact\",\"direction\":\"asc\"}]"
        ),
    )


async def _do_sg_summarize(params: dict) -> str:
    """Server-side aggregation: count, sum, avg, min, max with optional grouping.

    Much more efficient than fetching all records with sg_find and
    calculating in Python. Runs entirely on the ShotGrid server.
    """
    from pydantic import ValidationError
    try:
        validated = SgSummarizeInput(**params)
    except ValidationError as e:
        return json.dumps({"error": f"Invalid params for summarize: {e}"})

    _stats["exec_calls"] += 1
    _stats["tokens_in"] += _tok(validated.filters) + _tok(validated.summary_fields)
    filters = json.loads(validated.filters)
    summary_fields = json.loads(validated.summary_fields)
    grouping = json.loads(validated.grouping) if validated.grouping else None
    results = await sg_summarize(validated.entity_type, filters, summary_fields, grouping=grouping)
    out = json.dumps(results, default=str)
    _stats["tokens_out"] += _tok(out)
    return out


class SgReviveInput(BaseModel):
    model_config = _STRICT_CONFIG
    entity_type: str = Field(description="Entity type to restore (e.g. 'Asset', 'Shot', 'Task').")
    entity_id: int = Field(description="ID of the soft-deleted entity to restore.")


async def _do_sg_revive(params: dict) -> str:
    """Restore a soft-deleted (retired) entity.

    Reverses sg_delete. The entity is moved out of the trash and
    becomes active again with all its data intact.
    """
    from pydantic import ValidationError
    try:
        validated = SgReviveInput(**params)
    except ValidationError as e:
        return json.dumps({"error": f"Invalid params for revive: {e}"})

    _stats["exec_calls"] += 1
    _stats["tokens_in"] += _tok(f"{validated.entity_type} {validated.entity_id}")
    result = await sg_revive(validated.entity_type, validated.entity_id)
    out = json.dumps({"revived": result, "entity_type": validated.entity_type, "entity_id": validated.entity_id})
    _stats["tokens_out"] += _tok(out)
    return out


class SgNoteThreadInput(BaseModel):
    model_config = _STRICT_CONFIG
    note_id: int = Field(description="ID of the Note entity to read the full reply thread for.")


async def _do_sg_note_thread(params: dict) -> str:
    """Read the full reply thread of a Note, including all nested replies.

    Returns the complete conversation thread that sg_find cannot
    reconstruct. Includes reply content, authors, and timestamps.
    """
    from pydantic import ValidationError
    try:
        validated = SgNoteThreadInput(**params)
    except ValidationError as e:
        return json.dumps({"error": f"Invalid params for note_thread: {e}"})

    _stats["exec_calls"] += 1
    _stats["tokens_in"] += _tok(str(validated.note_id))
    results = await sg_note_thread_read(validated.note_id)
    out = json.dumps(results, default=str)
    _stats["tokens_out"] += _tok(out)
    return out


class SgActivityInput(BaseModel):
    model_config = _STRICT_CONFIG
    entity_type: str = Field(description="Entity type (e.g. 'Asset', 'Shot', 'Version', 'Task').")
    entity_id: int = Field(description="Entity ID to read activity stream for.")
    limit: int = Field(default=20, description="Max number of activity entries to return.")


async def _do_sg_activity(params: dict) -> str:
    """Read the activity stream for an entity.

    Returns recent updates, status changes, notes, and other events.
    This uses a dedicated ShotGrid API method that cannot be replicated
    with sg_find.
    """
    from pydantic import ValidationError
    try:
        validated = SgActivityInput(**params)
    except ValidationError as e:
        return json.dumps({"error": f"Invalid params for activity: {e}"})

    _stats["exec_calls"] += 1
    _stats["tokens_in"] += _tok(f"{validated.entity_type} {validated.entity_id}")
    results = await sg_activity_stream_read(validated.entity_type, validated.entity_id, limit=validated.limit)
    out = json.dumps(results, default=str)
    _stats["tokens_out"] += _tok(out)
    return out


# ---------------------------------------------------------------------------
# Bulk Dispatch Tool
# ---------------------------------------------------------------------------

@mcp.tool(name="fpt_bulk")
async def fpt_bulk(params: BulkDispatchInput) -> str:
    """Execute bulk/destructive ShotGrid operations.

    Available actions:

    • delete — Retire (soft-delete) an entity. Can be restored from trash. Required params: {"entity_type": "Shot", "entity_id": 123}
    • revive — Restore a previously retired entity. Required params: {"entity_type": "Shot", "entity_id": 123}
    • batch — Execute multiple operations in a single transactional call (ALL succeed or ALL fail). Required params: {"requests": "[{\"request_type\": \"create\", \"entity_type\": \"Shot\", \"data\": {\"code\": \"SH010\", \"project\": {\"type\": \"Project\", \"id\": 123}}}]"}
    """
    dispatch = {
        BulkAction.DELETE: _do_sg_delete,
        BulkAction.REVIVE: _do_sg_revive,
        BulkAction.BATCH: _do_sg_batch,
    }
    handler = dispatch[params.action]
    return await handler(params.params or {})


# ---------------------------------------------------------------------------
# Reporting Dispatch Tool
# ---------------------------------------------------------------------------

@mcp.tool(name="fpt_reporting")
async def fpt_reporting(params: ReportingDispatchInput) -> str:
    """Search, aggregate, and inspect ShotGrid data for reporting and analysis.

    Available actions:

    • text_search — Full-text search across multiple entity types at once. Required params: {"text": "search terms", "entity_types": "{\"Asset\":[], \"Shot\":[[\"sg_status_list\",\"is\",\"ip\"]]}"} Optional: {"limit": 10}
    • summarize — Server-side aggregation (count, sum, avg, min, max) with optional grouping. Required params: {"entity_type": "Task", "filters": "[[\"sg_status_list\",\"is\",\"ip\"]]", "summary_fields": "[{\"field\": \"duration\", \"type\": \"sum\"}]"} Optional: {"grouping": "[{\"field\": \"sg_status_list\", \"type\": \"exact\"}]"}
    • note_thread — Read the full reply thread of a Note. Required params: {"note_id": 123}
    • activity — Read the activity stream for an entity. Required params: {"entity_type": "Shot", "entity_id": 456} Optional: {"limit": 20}
    """
    dispatch = {
        ReportingAction.TEXT_SEARCH: _do_sg_text_search,
        ReportingAction.SUMMARIZE: _do_sg_summarize,
        ReportingAction.NOTE_THREAD: _do_sg_note_thread,
        ReportingAction.ACTIVITY: _do_sg_activity,
    }
    handler = dispatch[params.action]
    return await handler(params.params or {})


# ---------------------------------------------------------------------------
# RAG tools — search_sg_docs, learn_pattern, session_stats
# ---------------------------------------------------------------------------

class SearchSgDocsInput(BaseModel):
    model_config = _STRICT_CONFIG
    query: str = Field(
        description=(
            "Natural language query about ShotGrid API, Toolkit, or REST API. "
            "Examples: 'how to filter Assets by type', 'template tokens for Shot publish', "
            "'entity reference format in filters', 'batch operation semantics'."
        ),
    )
    n_results: int = Field(
        default=5,
        description="Number of documentation chunks to return (default: 5, max: 10).",
        ge=1, le=10,
    )


@mcp.tool(name="search_sg_docs")
async def search_sg_docs_tool(params: SearchSgDocsInput) -> str:
    """Search ShotGrid API documentation using hybrid RAG (semantic + BM25).

    Call this BEFORE writing complex queries, using unfamiliar filters,
    or when unsure about entity format, template tokens, or operator names.
    Returns the most relevant documentation chunks with relevance scores.

    Covers three APIs: shotgun_api3 (Python SDK), Toolkit (sgtk), REST API.
    Uses HyDE query expansion + Reciprocal Rank Fusion for high precision.
    """
    global _last_rag_score, _rag_called_this_session

    try:
        from fpt_mcp.rag.search import search
        text, relevance = search(params.query, n_results=params.n_results)
    except ImportError:
        return json.dumps({
            "error": "RAG dependencies not installed. Run: pip install chromadb sentence-transformers rank-bm25",
            "fallback": "Proceed with caution — no documentation verification available.",
        })
    except Exception as e:
        return json.dumps({"error": f"RAG search failed: {e}"})

    _stats["rag_calls"] += 1
    _stats["tokens_saved"] += _FULL_DOC_TOKENS - _tok(text)
    _last_rag_score = relevance
    _rag_called_this_session = True

    result = {
        "documentation": text,
        "max_relevance": relevance,
        "chunks_returned": params.n_results,
    }

    if relevance < 60:
        result["warning"] = (
            f"Low relevance ({relevance}%) — this query may cover an undocumented area. "
            "Proceed carefully. If your approach works, call learn_pattern to save it."
        )

    return json.dumps(result, default=str)


class LearnPatternInput(BaseModel):
    model_config = _STRICT_CONFIG
    description: str = Field(
        description="Short description of what the pattern does (e.g. 'filter PublishedFiles by Shot and type').",
    )
    code: str = Field(
        description="The working code/query pattern to remember (e.g. sg.find filter syntax, template fields).",
    )
    api: str = Field(
        default="shotgun_api3",
        description="Which API this pattern belongs to: 'shotgun_api3', 'toolkit', or 'rest_api'.",
    )


@mcp.tool(name="learn_pattern")
async def learn_pattern_tool(params: LearnPatternInput) -> str:
    """Save a validated working pattern to the RAG knowledge base.

    Call this after a successful operation when search_sg_docs returned
    low relevance (< 60%), indicating the pattern was not well-documented.
    The pattern will be available in future sessions.

    Model trust gates: only Sonnet/Opus can write directly.
    Other models stage candidates for review.
    """
    if _model_can_write():
        # Direct write to docs
        api_file_map = {
            "shotgun_api3": "SG_API.md",
            "toolkit": "TK_API.md",
            "rest_api": "REST_API.md",
        }
        doc_file = api_file_map.get(params.api, "SG_API.md")
        doc_path = _SERVER_DIR / "docs" / doc_file

        try:
            entry = (
                f"\n\n## Learned: {params.description}\n\n"
                f"```python\n{params.code}\n```\n"
            )
            with open(doc_path, "a", encoding="utf-8") as f:
                f.write(entry)
            _stats["patterns_learned"] += 1

            # Clear RAG cache so new pattern is found on next search
            try:
                from fpt_mcp.rag.search import clear_cache
                clear_cache()
            except ImportError:
                pass

            return json.dumps({
                "status": "learned",
                "description": params.description,
                "file": doc_file,
                "note": "Pattern appended to docs. Run build_index to include in RAG.",
            })
        except Exception as e:
            return json.dumps({"error": f"Failed to write pattern: {e}"})
    else:
        # Stage candidate for review
        candidates_path = _SERVER_DIR / "rag" / "candidates.json"
        try:
            candidates = json.loads(candidates_path.read_text()) if candidates_path.exists() else []
        except Exception:
            candidates = []

        candidates.append({
            "description": params.description,
            "code": params.code,
            "api": params.api,
            "model": _get_current_model(),
            "timestamp": datetime.datetime.now().isoformat(),
        })

        try:
            candidates_path.parent.mkdir(parents=True, exist_ok=True)
            candidates_path.write_text(json.dumps(candidates, indent=2, ensure_ascii=False))
        except Exception:
            pass

        _stats["patterns_staged"] += 1

        return json.dumps({
            "status": "staged",
            "description": params.description,
            "note": f"Model '{_get_current_model()}' is read-only. Pattern staged for review.",
        })


@mcp.tool(name="session_stats")
async def session_stats_tool() -> str:
    """Show session efficiency statistics: token usage, RAG savings, patterns learned.

    Call at the end of multi-step tasks or when asked about efficiency.
    Shows how much context was saved by RAG vs loading full documentation.
    """
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
        "full_doc_baseline": _FULL_DOC_TOKENS,
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
