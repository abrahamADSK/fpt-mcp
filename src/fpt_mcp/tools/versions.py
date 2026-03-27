"""Tools for creating Versions and uploading thumbnails in FPT."""

from __future__ import annotations

import json
import os
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict

from fpt_mcp.client import (
    sg_create,
    sg_find_one,
    sg_upload,
    sg_upload_thumbnail,
    get_project_filter,
    PROJECT_ID,
)


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class CreateVersionInput(BaseModel):
    """Input for fpt_create_version."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: str = Field(
        ...,
        description="Version name/code, e.g. 'MRBone1_model_v003'.",
        min_length=1,
        max_length=128,
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
    description: Optional[str] = Field(
        default=None,
        description="Version description or notes.",
    )
    status: str = Field(
        default="rev",
        description="Status code: 'rev' (pending review), 'app' (approved), 'rej' (rejected).",
    )
    movie_path: Optional[str] = Field(
        default=None,
        description="Local path to a movie/image file to upload as 'uploaded_movie'.",
    )
    frame_range: Optional[str] = Field(
        default=None,
        description="Frame range string, e.g. '1001-1120'.",
    )


class UploadThumbnailInput(BaseModel):
    """Input for fpt_upload_thumbnail."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    entity_type: str = Field(
        ...,
        description="Entity type: 'Asset', 'Shot', 'Version', etc.",
    )
    entity_id: int = Field(
        ..., description="ShotGrid entity ID.", ge=1
    )
    image_path: str = Field(
        ...,
        description="Absolute local path to the image file (JPG, PNG, etc.).",
        min_length=1,
    )


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------

async def create_version_impl(params: CreateVersionInput) -> str:
    """Create a Version entity linked to an Asset or Shot.

    Optionally uploads a movie file to the 'uploaded_movie' field.
    Returns the created Version details.
    """
    # Validate parent entity exists
    parent = await sg_find_one(
        params.entity_type,
        [["id", "is", params.entity_id]],
        ["id", "code"],
    )
    if not parent:
        return json.dumps({
            "error": f"{params.entity_type} with id={params.entity_id} not found.",
        })

    data: dict = {
        "code": params.code,
        "entity": {"type": params.entity_type, "id": params.entity_id},
        "sg_status_list": params.status,
    }
    if params.description:
        data["description"] = params.description
    if PROJECT_ID:
        data["project"] = get_project_filter()
    if params.frame_range:
        data["sg_first_frame"] = int(params.frame_range.split("-")[0]) if "-" in params.frame_range else None
        data["sg_last_frame"] = int(params.frame_range.split("-")[1]) if "-" in params.frame_range else None

    result = await sg_create("Version", data)
    version_id = result["id"]

    # Upload movie if provided
    movie_uploaded = False
    if params.movie_path:
        if not os.path.isfile(params.movie_path):
            return json.dumps({
                "error": f"Movie file not found: {params.movie_path}",
                "version_id": version_id,
                "message": "Version was created but movie upload failed.",
            })
        await sg_upload(
            "Version", version_id, params.movie_path,
            field_name="sg_uploaded_movie",
            display_name=os.path.basename(params.movie_path),
        )
        movie_uploaded = True

    return json.dumps({
        "id": version_id,
        "code": params.code,
        "type": "Version",
        "entity": {"type": params.entity_type, "id": params.entity_id, "code": parent.get("code")},
        "status": params.status,
        "movie_uploaded": movie_uploaded,
        "message": f"Version '{params.code}' created and linked to {params.entity_type} '{parent.get('code')}'.",
    }, indent=2)


async def upload_thumbnail_impl(params: UploadThumbnailInput) -> str:
    """Upload a thumbnail image to any FPT entity (Asset, Version, Shot, etc.).

    Returns confirmation with the entity details.
    """
    if not os.path.isfile(params.image_path):
        return json.dumps({
            "error": f"Image file not found: {params.image_path}",
        })

    # Validate entity exists
    entity = await sg_find_one(
        params.entity_type,
        [["id", "is", params.entity_id]],
        ["id", "code"],
    )
    if not entity:
        return json.dumps({
            "error": f"{params.entity_type} with id={params.entity_id} not found.",
        })

    thumb_id = await sg_upload_thumbnail(
        params.entity_type, params.entity_id, params.image_path
    )

    return json.dumps({
        "thumbnail_id": thumb_id,
        "entity_type": params.entity_type,
        "entity_id": params.entity_id,
        "entity_code": entity.get("code"),
        "image_path": params.image_path,
        "message": f"Thumbnail uploaded to {params.entity_type} '{entity.get('code')}'.",
    }, indent=2)
