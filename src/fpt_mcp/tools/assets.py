"""Tools for querying and downloading asset data from FPT."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Optional, List

from pydantic import BaseModel, Field, ConfigDict

from fpt_mcp.client import (
    sg_find,
    sg_find_one,
    sg_download_attachment,
    get_project_filter,
    PROJECT_ID,
)


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class FindAssetsInput(BaseModel):
    """Input for fpt_find_assets."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    asset_type: Optional[str] = Field(
        default=None,
        description=(
            "Filter by asset type: 'Character', 'Environment', 'Prop', etc. "
            "Leave empty to list all types."
        ),
    )
    status: Optional[str] = Field(
        default=None,
        description="Filter by status code, e.g. 'ip' (in progress), 'fin' (final).",
    )
    name_contains: Optional[str] = Field(
        default=None,
        description="Substring filter on asset code/name (case-insensitive).",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of assets to return.",
    )


class GetAssetImageInput(BaseModel):
    """Input for fpt_get_asset_image."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    asset_id: int = Field(
        ..., description="ShotGrid entity ID of the Asset.", ge=1
    )
    download_dir: Optional[str] = Field(
        default=None,
        description=(
            "Directory to save the image. Defaults to a temp directory. "
            "The tool returns the absolute path to the downloaded file."
        ),
    )


# ---------------------------------------------------------------------------
# Tool implementations (registered in server.py)
# ---------------------------------------------------------------------------

async def find_assets_impl(params: FindAssetsInput) -> str:
    """Query assets from FPT with optional filters.

    Returns a JSON list of asset dicts with id, code, sg_asset_type,
    sg_status_list, description, and image (thumbnail URL).
    """
    filters: list = []

    if PROJECT_ID:
        filters.append(["project", "is", get_project_filter()])
    if params.asset_type:
        filters.append(["sg_asset_type", "is", params.asset_type])
    if params.status:
        filters.append(["sg_status_list", "is", params.status])
    if params.name_contains:
        filters.append(["code", "contains", params.name_contains])

    fields = [
        "id",
        "code",
        "sg_asset_type",
        "sg_status_list",
        "description",
        "image",
        "created_at",
    ]

    results = await sg_find(
        "Asset", filters, fields, order=[{"field_name": "code", "direction": "asc"}], limit=params.limit
    )

    if not results:
        return json.dumps({"total": 0, "assets": [], "message": "No assets found matching the filters."})

    assets = []
    for r in results:
        assets.append({
            "id": r["id"],
            "code": r.get("code"),
            "asset_type": r.get("sg_asset_type"),
            "status": r.get("sg_status_list"),
            "description": r.get("description"),
            "thumbnail_url": r.get("image"),
            "created_at": str(r.get("created_at", "")),
        })

    return json.dumps({"total": len(assets), "assets": assets}, indent=2)


async def get_asset_image_impl(params: GetAssetImageInput) -> str:
    """Download the reference image from the latest Version linked to an asset.

    Looks for the 'uploaded_movie' field on Versions linked to the asset,
    falling back to the asset thumbnail if no Version movie is found.
    Returns the local file path of the downloaded image.
    """
    # 1. Try to find a Version linked to this asset with uploaded_movie
    version_filters = [
        ["entity", "is", {"type": "Asset", "id": params.asset_id}],
    ]
    version_fields = ["id", "code", "uploaded_movie", "image"]

    versions = await sg_find(
        "Version",
        version_filters,
        version_fields,
        order=[{"field_name": "created_at", "direction": "desc"}],
        limit=1,
    )

    attachment = None
    source = "unknown"

    if versions and versions[0].get("uploaded_movie"):
        attachment = versions[0]["uploaded_movie"]
        source = f"Version {versions[0]['id']} uploaded_movie"
    elif versions and versions[0].get("image"):
        # Version thumbnail as fallback
        attachment = versions[0]["image"]
        source = f"Version {versions[0]['id']} thumbnail"
    else:
        # Final fallback: asset's own thumbnail
        asset = await sg_find_one(
            "Asset",
            [["id", "is", params.asset_id]],
            ["image"],
        )
        if asset and asset.get("image"):
            attachment = asset["image"]
            source = "Asset thumbnail"

    if not attachment:
        return json.dumps({
            "error": f"No image found for Asset {params.asset_id}. "
            "The asset has no linked Versions with uploaded_movie and no thumbnail.",
        })

    # 2. Download
    download_dir = params.download_dir or tempfile.mkdtemp(prefix="fpt_img_")
    os.makedirs(download_dir, exist_ok=True)

    # Determine filename
    if isinstance(attachment, dict) and attachment.get("name"):
        filename = attachment["name"]
    else:
        filename = f"asset_{params.asset_id}_ref.jpg"

    dest_path = os.path.join(download_dir, filename)

    # If attachment is a URL string (thumbnail), download via httpx
    if isinstance(attachment, str):
        import httpx

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(attachment)
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                f.write(resp.content)
    else:
        # attachment is a dict → use ShotGrid API download
        await sg_download_attachment(attachment, dest_path)

    return json.dumps({
        "path": dest_path,
        "source": source,
        "asset_id": params.asset_id,
    }, indent=2)
