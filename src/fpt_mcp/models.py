"""models.py — Pydantic input models for every MCP tool.

Extracted from server.py in Bucket F Phase 2a. server.py re-exports every
symbol from this module so existing imports (and the .concepts.yml
invariants that grep for them by file path) keep working.

Contains:
    - _STRICT_CONFIG            — shared ConfigDict(extra="forbid", ...)
    - BulkAction, ReportingAction — str-valued enums for dispatchers
    - Direct-tool models        — SgFindInput, SgCreateInput, SgUpdateInput,
                                  SgDeleteInput, SgSchemaInput, SgUploadInput,
                                  SgDownloadInput, TkResolvePathInput,
                                  TkPublishInput, FptLaunchAppInput,
                                  SearchSgDocsInput, LearnPatternInput
    - Dispatcher wrappers       — BulkDispatchInput, ReportingDispatchInput
    - Per-action sub-models     — SgBatchInput, SgReviveInput (bulk),
                                  SgTextSearchInput, SgSummarizeInput,
                                  SgNoteThreadInput, SgActivityInput
                                  (reporting)

Layering rule (Bucket F Phase 2a):
    models.py MUST NOT import from any other fpt_mcp module EXCEPT filters
    (which itself imports nothing from fpt_mcp). This keeps the data layer
    free of import cycles as subsequent phases (shotgrid, reporting,
    toolkit_tools, launcher, rag_tools) are extracted.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fpt_mcp.filters import _validate_filter_triples


# Shared strict config for every input model. extra="forbid" makes the
# schema reject hallucinated keys at validation time (rather than silently
# accepting them and forwarding garbage to ShotGrid). str_strip_whitespace
# normalises accidental leading/trailing whitespace from LLM output.
_STRICT_CONFIG = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Direct ShotGrid tool inputs
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Toolkit tool inputs
# ---------------------------------------------------------------------------

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
    entity_type: Optional[str] = Field(
        default=None,
        description=(
            "'Asset' or 'Shot'. Auto-derived from local_path via Toolkit template matching "
            "when the project has a PipelineConfiguration. Required only if local_path is "
            "absent or not a Toolkit-managed path."
        ),
    )
    entity_id: Optional[int] = Field(
        default=None,
        description=(
            "Entity ID in ShotGrid. Auto-derived from local_path (one sg_find by entity code) "
            "when entity_type can be inferred from the path. Required only if local_path is "
            "absent or not a Toolkit-managed path."
        ),
    )
    publish_type: str = Field(
        description=(
            "PublishedFileType code in ShotGrid (e.g. 'Maya Scene', 'Nuke Script', "
            "'Alembic Cache', 'Image'). Created automatically if it doesn't exist."
        ),
    )
    step: Optional[str] = Field(
        default=None,
        description=(
            "Pipeline step short_name (e.g. 'MDL', 'RIG'). "
            "Auto-derived from local_path when the path encodes the step token. "
            "Falls back to 'model' if neither provided nor derivable."
        ),
    )
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
# Dispatcher enums + wrapper models
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
# Launcher tool input
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


# ---------------------------------------------------------------------------
# Bulk action sub-models (consumed by fpt_bulk dispatcher)
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


class SgReviveInput(BaseModel):
    model_config = _STRICT_CONFIG
    entity_type: str = Field(description="Entity type to restore (e.g. 'Asset', 'Shot', 'Task').")
    entity_id: int = Field(description="ID of the soft-deleted entity to restore.")


# ---------------------------------------------------------------------------
# Reporting action sub-models (consumed by fpt_reporting dispatcher)
# ---------------------------------------------------------------------------

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


class SgNoteThreadInput(BaseModel):
    model_config = _STRICT_CONFIG
    note_id: int = Field(description="ID of the Note entity to read the full reply thread for.")


class SgActivityInput(BaseModel):
    model_config = _STRICT_CONFIG
    entity_type: str = Field(description="Entity type (e.g. 'Asset', 'Shot', 'Version', 'Task').")
    entity_id: int = Field(description="Entity ID to read activity stream for.")
    limit: int = Field(default=20, description="Max number of activity entries to return.")


# ---------------------------------------------------------------------------
# RAG tool inputs
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
