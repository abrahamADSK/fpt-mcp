"""Tools for creating and querying PublishedFile entities in FPT.

Hybrid approach: creates PublishedFile via ShotGrid API but resolves
paths using Toolkit conventions so publishes are compatible with
tk-multi-loader2 in Maya and Flame.
"""

from __future__ import annotations

import json
import os
from typing import Optional, List
from enum import Enum

from pydantic import BaseModel, Field, ConfigDict

from fpt_mcp.client import (
    sg_create,
    sg_find,
    sg_find_one,
    get_project_filter,
    PROJECT_ID,
)
from fpt_mcp.paths import resolve_publish_path, next_version_number


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class PublishFileType(str, Enum):
    """Supported PublishedFile types matching the agreed pipeline spec."""
    OBJ = "OBJ"
    TEXTURE = "Texture"
    MAYA_SCENE = "Maya Scene"
    RENDERED_IMAGE = "Rendered Image"


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class CreatePublishedFileInput(BaseModel):
    """Input for fpt_create_published_file."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: str = Field(
        ...,
        description="Publish name/code, e.g. 'MRBone1_model'. Used in path resolution.",
        min_length=1,
        max_length=128,
    )
    file_type: PublishFileType = Field(
        ...,
        description="Publish type: 'OBJ', 'Texture', 'Maya Scene', or 'Rendered Image'.",
    )
    entity_type: str = Field(
        ...,
        description="Parent entity type: 'Asset' or 'Shot'.",
    )
    entity_id: int = Field(
        ...,
        description="ShotGrid ID of the parent Asset or Shot.",
        ge=1,
    )
    task_id: Optional[int] = Field(
        default=None,
        description="Optional ShotGrid Task ID to link the publish to.",
        ge=1,
    )
    version_id: Optional[int] = Field(
        default=None,
        description="Optional Version ID to link the publish to.",
        ge=1,
    )
    local_path: Optional[str] = Field(
        default=None,
        description=(
            "Absolute local path to the published file. "
            "If omitted, a Toolkit-convention path is auto-generated."
        ),
    )
    description: Optional[str] = Field(
        default=None,
        description="Publish description.",
    )
    version_number: Optional[int] = Field(
        default=None,
        description="Explicit version number. If omitted, auto-incremented.",
        ge=1,
    )
    step: str = Field(
        default="model",
        description="Pipeline step: 'model', 'rig', 'layout', 'anim', 'light', 'comp'.",
    )
    project_name: str = Field(
        default="default",
        description="Project name for Toolkit path resolution.",
    )


class FindPublishedFilesInput(BaseModel):
    """Input for fpt_find_published_files."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    entity_type: Optional[str] = Field(
        default=None,
        description="Filter by parent entity type: 'Asset' or 'Shot'.",
    )
    entity_id: Optional[int] = Field(
        default=None,
        description="Filter by parent entity ID.",
        ge=1,
    )
    file_type: Optional[str] = Field(
        default=None,
        description="Filter by publish type: 'OBJ', 'Texture', 'Maya Scene', 'Rendered Image'.",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum results to return.",
    )


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------

async def create_published_file_impl(params: CreatePublishedFileInput) -> str:
    """Create a PublishedFile entity in FPT with Toolkit-compatible paths.

    Uses the hybrid approach:
    - Entity creation via ShotGrid API (shotgun_api3)
    - Path resolution via Toolkit conventions (paths.py)

    Returns the created publish details including the resolved path.
    """
    # Validate parent entity
    parent = await sg_find_one(
        params.entity_type,
        [["id", "is", params.entity_id]],
        ["id", "code", "sg_asset_type"] if params.entity_type == "Asset" else ["id", "code"],
    )
    if not parent:
        return json.dumps({
            "error": f"{params.entity_type} with id={params.entity_id} not found.",
        })

    # Resolve path using Toolkit conventions
    if params.local_path:
        resolved_path = params.local_path
        version_num = params.version_number or 1
    else:
        # Auto-resolve path
        publish_base = resolve_publish_path(
            project=params.project_name,
            entity_type=params.entity_type,
            entity=parent.get("code", str(params.entity_id)),
            step=params.step,
            name=params.code,
            version=0,  # placeholder, we'll compute
        )
        # Go up one level to find version siblings
        version_parent = publish_base.parent
        version_num = params.version_number or next_version_number(version_parent)

        resolved_path = str(resolve_publish_path(
            project=params.project_name,
            entity_type=params.entity_type,
            entity=parent.get("code", str(params.entity_id)),
            step=params.step,
            name=params.code,
            version=version_num,
        ))

        # Add file extension
        ext_map = {
            PublishFileType.OBJ: ".obj",
            PublishFileType.TEXTURE: ".png",
            PublishFileType.MAYA_SCENE: ".ma",
            PublishFileType.RENDERED_IMAGE: ".exr",
        }
        resolved_path = os.path.join(
            resolved_path,
            f"{params.code}_v{str(version_num).zfill(3)}{ext_map[params.file_type]}",
        )

    # Resolve or create the PublishedFileType entity
    pf_type = await sg_find_one(
        "PublishedFileType",
        [["code", "is", params.file_type.value]],
        ["id", "code"],
    )
    if not pf_type:
        pf_type = await sg_create("PublishedFileType", {"code": params.file_type.value})

    # Build the PublishedFile data
    data: dict = {
        "code": params.code,
        "published_file_type": pf_type,
        "entity": {"type": params.entity_type, "id": params.entity_id},
        "path": {"local_path": resolved_path},
        "version_number": version_num,
        "sg_status_list": "pub",
    }
    if params.description:
        data["description"] = params.description
    if PROJECT_ID:
        data["project"] = get_project_filter()
    if params.task_id:
        data["task"] = {"type": "Task", "id": params.task_id}
    if params.version_id:
        data["version"] = {"type": "Version", "id": params.version_id}

    result = await sg_create("PublishedFile", data)

    return json.dumps({
        "id": result["id"],
        "code": params.code,
        "type": "PublishedFile",
        "file_type": params.file_type.value,
        "entity": {
            "type": params.entity_type,
            "id": params.entity_id,
            "code": parent.get("code"),
        },
        "path": resolved_path,
        "version_number": version_num,
        "message": f"Published '{params.code}' v{str(version_num).zfill(3)} ({params.file_type.value}) "
        f"linked to {params.entity_type} '{parent.get('code')}'.",
    }, indent=2)


async def find_published_files_impl(params: FindPublishedFilesInput) -> str:
    """Query PublishedFile entities from FPT with optional filters.

    Returns a JSON list with id, code, file_type, entity link,
    path, and version_number.
    """
    filters: list = []

    if PROJECT_ID:
        filters.append(["project", "is", get_project_filter()])
    if params.entity_type and params.entity_id:
        filters.append(["entity", "is", {"type": params.entity_type, "id": params.entity_id}])
    elif params.entity_id:
        filters.append(["entity.Asset.id", "is", params.entity_id])
    if params.file_type:
        filters.append(["published_file_type.PublishedFileType.code", "is", params.file_type])

    fields = [
        "id",
        "code",
        "published_file_type",
        "entity",
        "path",
        "version_number",
        "sg_status_list",
        "created_at",
        "description",
    ]

    results = await sg_find(
        "PublishedFile",
        filters,
        fields,
        order=[{"field_name": "created_at", "direction": "desc"}],
        limit=params.limit,
    )

    if not results:
        return json.dumps({"total": 0, "publishes": [], "message": "No published files found."})

    publishes = []
    for r in results:
        pf_type = r.get("published_file_type")
        entity = r.get("entity")
        path_data = r.get("path")

        publishes.append({
            "id": r["id"],
            "code": r.get("code"),
            "file_type": pf_type.get("name") if pf_type else None,
            "entity": {
                "type": entity.get("type") if entity else None,
                "id": entity.get("id") if entity else None,
                "name": entity.get("name") if entity else None,
            } if entity else None,
            "path": path_data.get("local_path") if isinstance(path_data, dict) else str(path_data),
            "version_number": r.get("version_number"),
            "status": r.get("sg_status_list"),
            "description": r.get("description"),
            "created_at": str(r.get("created_at", "")),
        })

    return json.dumps({"total": len(publishes), "publishes": publishes}, indent=2)
