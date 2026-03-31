#!/usr/bin/env python3
"""FPT MCP Server — Full Flow Production Tracking (ShotGrid) + Toolkit + RAG.

General-purpose MCP server exposing the complete ShotGrid API, Toolkit
path conventions, and RAG-powered documentation search.

Features:
    - 7 ShotGrid API tools (CRUD, schema, media)
    - 2 Toolkit tools (path resolution, publish pipeline)
    - 3 RAG tools (search_sg_docs, learn_pattern, session_stats)
    - Dangerous pattern detection (safety.py)
    - Hybrid search: ChromaDB + BM25 + HyDE + RRF fusion
    - Token tracking with RAG savings measurement
    - Model trust gates for self-learning

Designed to work alongside maya-mcp and flame-mcp as part of a
VFX pipeline orchestrated by Claude.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field
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
    PROJECT_ID,
)
from fpt_mcp.tk_config import discover_or_fallback, TkConfigError
from fpt_mcp.safety import check_dangerous

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
    "tokens_saved": 0,     # tokens saved by RAG vs loading full doc
    "patterns_learned": 0, # patterns added to docs
    "patterns_staged": 0,  # candidates staged by non-trusted models
    "safety_blocks": 0,    # dangerous pattern detections
    "cache_hits": 0,       # RAG cache hits
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

class SgFindInput(BaseModel):
    entity_type: str = Field(description="ShotGrid entity type: Asset, Shot, Sequence, Version, Task, Note, PublishedFile, HumanUser, Project, etc.")
    filters: list = Field(default_factory=list, description="ShotGrid filter list. Examples: [['sg_status_list','is','ip']], [['id','is',1234]], [['code','contains','hero']]. Use [] for no filter.")
    fields: list[str] = Field(default_factory=lambda: ["id", "code", "sg_status_list"], description="Fields to return. Use sg_schema to discover available fields.")
    order: list[dict] = Field(default_factory=list, description="Sort order. Example: [{'field_name':'created_at','direction':'desc'}]")
    limit: int = Field(default=50, description="Max results to return. 0 = unlimited.")
    add_project_filter: bool = Field(default=True, description="Auto-add project filter from SHOTGRID_PROJECT_ID env var.")

class SgCreateInput(BaseModel):
    entity_type: str = Field(description="Entity type to create: Asset, Shot, Sequence, Task, Version, Note, PublishedFile, etc.")
    data: dict[str, Any] = Field(description="Field values. Example: {'code':'HERO','sg_asset_type':'Character','description':'Main character'}. Project is auto-added if not specified.")

class SgUpdateInput(BaseModel):
    entity_type: str = Field(description="Entity type: Asset, Shot, Version, Task, etc.")
    entity_id: int = Field(description="ID of the entity to update.")
    data: dict[str, Any] = Field(description="Fields to update. Example: {'sg_status_list':'cmpt','description':'Final version'}.")

class SgDeleteInput(BaseModel):
    entity_type: str = Field(description="Entity type to delete.")
    entity_id: int = Field(description="ID of the entity to delete.")

class SgSchemaInput(BaseModel):
    entity_type: str = Field(description="Entity type to inspect: Asset, Shot, Task, Version, PublishedFile, etc.")
    field_name: Optional[str] = Field(default=None, description="Specific field to inspect. Omit to get all fields.")

class SgUploadInput(BaseModel):
    entity_type: str = Field(description="Entity type to upload to.")
    entity_id: int = Field(description="Entity ID.")
    file_path: str = Field(description="Local path to the file to upload.")
    field_name: str = Field(default="image", description="Target field: 'image' for thumbnail, 'sg_uploaded_movie' for movie, etc.")
    display_name: Optional[str] = Field(default=None, description="Display name for the attachment.")

class SgDownloadInput(BaseModel):
    entity_type: str = Field(description="Entity type.")
    entity_id: int = Field(description="Entity ID.")
    field_name: str = Field(default="image", description="Field containing the attachment: 'image', 'sg_uploaded_movie', etc.")
    download_path: str = Field(description="Local path where to save the file.")

class TkResolvePathInput(BaseModel):
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
    response = json.dumps({"total": len(results), "entities": results}, default=str)
    _stats["tokens_out"] += _tok(response)
    return response


@mcp.tool(name="sg_create")
async def sg_create_tool(params: SgCreateInput) -> str:
    """Create any entity in ShotGrid.

    Works with ALL entity types. Project is auto-linked if SHOTGRID_PROJECT_ID
    is set and 'project' is not in the data dict.
    """
    data = dict(params.data)
    if "project" not in data and PROJECT_ID:
        data["project"] = {"type": "Project", "id": PROJECT_ID}

    result = await sg_create(params.entity_type, data)
    return json.dumps(result, default=str)


@mcp.tool(name="sg_update")
async def sg_update_tool(params: SgUpdateInput) -> str:
    """Update any entity's fields in ShotGrid."""
    result = await sg_update(params.entity_type, params.entity_id, params.data)
    return json.dumps(result, default=str)


@mcp.tool(name="sg_delete")
async def sg_delete_tool(params: SgDeleteInput) -> str:
    """Delete (retire) an entity in ShotGrid.

    This performs a soft-delete (retire). The entity can be restored
    from ShotGrid's trash.
    """
    _stats["exec_calls"] += 1

    # Safety check
    params_str = json.dumps({"entity_type": params.entity_type, "entity_id": params.entity_id})
    warning = check_dangerous(params_str)
    if warning:
        _stats["safety_blocks"] += 1
        return json.dumps({"safety_warning": warning})

    sg = get_sg()
    import asyncio
    result = await asyncio.to_thread(sg.delete, params.entity_type, params.entity_id)
    return json.dumps({"deleted": result, "entity_type": params.entity_type, "entity_id": params.entity_id})


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
            # Mode 2: Explicit path provided by user
            from pathlib import Path as _Path
            publish_path = _Path(params.publish_path)

        else:
            return json.dumps({
                "error": "No PipelineConfiguration found and no publish_path provided. "
                         "Please provide an explicit publish_path where the file should be published."
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
# RAG tools — search_sg_docs, learn_pattern, session_stats
# ---------------------------------------------------------------------------

class SearchSgDocsInput(BaseModel):
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

    return json.dumps({
        "session_duration": uptime,
        "tool_calls": _stats["exec_calls"],
        "rag_calls": _stats["rag_calls"],
        "tokens_used": used,
        "tokens_saved_by_rag": saved,
        "token_efficiency": ratio,
        "patterns_learned": _stats["patterns_learned"],
        "patterns_staged": _stats["patterns_staged"],
        "safety_blocks": _stats["safety_blocks"],
        "cache_hits": _stats["cache_hits"],
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
