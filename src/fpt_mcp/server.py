#!/usr/bin/env python3
"""FPT MCP Server — Flow Production Tracking (ShotGrid) integration.

Provides tools for managing Assets, Sequences, Shots, Versions,
PublishedFiles, and thumbnails in Autodesk Flow Production Tracking.
Designed to work alongside maya-mcp and flame-mcp as part of a
VFX pipeline orchestrated by Claude Desktop.

Hybrid approach: uses ShotGrid API (shotgun_api3) for entity CRUD
while following Toolkit (tk-config-default2) path conventions for
publish paths, ensuring compatibility with Toolkit loaders.
"""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

from fpt_mcp.tools.assets import (
    FindAssetsInput,
    GetAssetImageInput,
    find_assets_impl,
    get_asset_image_impl,
)
from fpt_mcp.tools.sequences import (
    CreateSequenceInput,
    create_sequence_impl,
)
from fpt_mcp.tools.shots import (
    CreateShotInput,
    create_shot_impl,
)
from fpt_mcp.tools.versions import (
    CreateVersionInput,
    UploadThumbnailInput,
    create_version_impl,
    upload_thumbnail_impl,
)
from fpt_mcp.tools.publish import (
    CreatePublishedFileInput,
    FindPublishedFilesInput,
    create_published_file_impl,
    find_published_files_impl,
)


# ---------------------------------------------------------------------------
# Server init
# ---------------------------------------------------------------------------

mcp = FastMCP("fpt_mcp")


# ---------------------------------------------------------------------------
# Tool registrations
# ---------------------------------------------------------------------------

# --- Assets ----------------------------------------------------------------

@mcp.tool(
    name="fpt_find_assets",
    annotations={
        "title": "Find Assets in FPT",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def fpt_find_assets(params: FindAssetsInput) -> str:
    """List assets from Flow Production Tracking with optional filters.

    Search by asset type (Character, Environment, Prop), status, or name.
    Returns id, code, asset_type, status, description, and thumbnail URL
    for each matching asset.

    Args:
        params (FindAssetsInput): Validated input containing:
            - asset_type (Optional[str]): Filter by type, e.g. 'Character'
            - status (Optional[str]): Filter by status code, e.g. 'ip'
            - name_contains (Optional[str]): Substring filter on asset name
            - limit (int): Max results, default 50

    Returns:
        str: JSON with {"total": int, "assets": [...]}
    """
    return await find_assets_impl(params)


@mcp.tool(
    name="fpt_get_asset_image",
    annotations={
        "title": "Download Asset Reference Image",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def fpt_get_asset_image(params: GetAssetImageInput) -> str:
    """Download the reference image for an asset from FPT.

    Tries the uploaded_movie field on the latest linked Version first,
    then falls back to the Version thumbnail, then the Asset thumbnail.
    Returns the local file path of the downloaded image.

    Args:
        params (GetAssetImageInput): Validated input containing:
            - asset_id (int): ShotGrid Asset entity ID
            - download_dir (Optional[str]): Where to save the image

    Returns:
        str: JSON with {"path": str, "source": str, "asset_id": int}
    """
    return await get_asset_image_impl(params)


# --- Sequences -------------------------------------------------------------

@mcp.tool(
    name="fpt_create_sequence",
    annotations={
        "title": "Create Sequence in FPT",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def fpt_create_sequence(params: CreateSequenceInput) -> str:
    """Create a new Sequence in Flow Production Tracking.

    Checks for duplicate codes before creating. Returns the new
    Sequence's id and code.

    Args:
        params (CreateSequenceInput): Validated input containing:
            - code (str): Sequence code, e.g. 'SEQ010'
            - description (Optional[str]): Description
            - status (str): Status code, default 'ip'

    Returns:
        str: JSON with {"id": int, "code": str, "type": "Sequence"}
    """
    return await create_sequence_impl(params)


# --- Shots -----------------------------------------------------------------

@mcp.tool(
    name="fpt_create_shot",
    annotations={
        "title": "Create Shot in FPT",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def fpt_create_shot(params: CreateShotInput) -> str:
    """Create a new Shot linked to a Sequence in Flow Production Tracking.

    Validates the parent Sequence exists and checks for duplicate codes.
    Supports cut_in, cut_out, and frame_range fields.

    Args:
        params (CreateShotInput): Validated input containing:
            - code (str): Shot code, e.g. 'SHOT010'
            - sequence_id (int): Parent Sequence ID
            - description (Optional[str]): Shot notes
            - status (str): Status code, default 'wtg'
            - cut_in/cut_out (Optional[int]): Frame range
            - frame_range (Optional[str]): e.g. '1001-1120'

    Returns:
        str: JSON with {"id": int, "code": str, "sequence": {...}}
    """
    return await create_shot_impl(params)


# --- Versions --------------------------------------------------------------

@mcp.tool(
    name="fpt_create_version",
    annotations={
        "title": "Create Version in FPT",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def fpt_create_version(params: CreateVersionInput) -> str:
    """Create a Version entity linked to an Asset or Shot.

    Optionally uploads a movie/image to the uploaded_movie field.
    Useful for tracking renders, playblasts, or reference media.

    Args:
        params (CreateVersionInput): Validated input containing:
            - code (str): Version name, e.g. 'MRBone1_model_v003'
            - entity_type (str): 'Asset' or 'Shot'
            - entity_id (int): Parent entity ID
            - description (Optional[str]): Notes
            - status (str): 'rev', 'app', or 'rej'
            - movie_path (Optional[str]): File to upload as movie
            - frame_range (Optional[str]): e.g. '1001-1120'

    Returns:
        str: JSON with {"id": int, "code": str, "entity": {...}}
    """
    return await create_version_impl(params)


@mcp.tool(
    name="fpt_upload_thumbnail",
    annotations={
        "title": "Upload Thumbnail to FPT Entity",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def fpt_upload_thumbnail(params: UploadThumbnailInput) -> str:
    """Upload a thumbnail image to any FPT entity (Asset, Shot, Version).

    Replaces the existing thumbnail. The entity must exist.

    Args:
        params (UploadThumbnailInput): Validated input containing:
            - entity_type (str): 'Asset', 'Shot', 'Version', etc.
            - entity_id (int): Entity ID
            - image_path (str): Local path to the image

    Returns:
        str: JSON with {"thumbnail_id": int, "entity_type": str, ...}
    """
    return await upload_thumbnail_impl(params)


# --- Publishes -------------------------------------------------------------

@mcp.tool(
    name="fpt_create_published_file",
    annotations={
        "title": "Create Published File in FPT",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def fpt_create_published_file(params: CreatePublishedFileInput) -> str:
    """Register a published file in FPT with Toolkit-compatible paths.

    Supported types: OBJ, Texture, Maya Scene, Rendered Image.
    Auto-increments version numbers and resolves paths using
    tk-config-default2 conventions.

    Args:
        params (CreatePublishedFileInput): Validated input containing:
            - code (str): Publish name
            - file_type (str): 'OBJ', 'Texture', 'Maya Scene', 'Rendered Image'
            - entity_type/entity_id: Parent Asset or Shot
            - step (str): Pipeline step, default 'model'
            - project_name (str): For path resolution
            - local_path (Optional[str]): Explicit path or auto-generated
            - version_number (Optional[int]): Explicit or auto-incremented

    Returns:
        str: JSON with {"id": int, "path": str, "version_number": int, ...}
    """
    return await create_published_file_impl(params)


@mcp.tool(
    name="fpt_find_published_files",
    annotations={
        "title": "Find Published Files in FPT",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def fpt_find_published_files(params: FindPublishedFilesInput) -> str:
    """Search for published files in FPT with optional filters.

    Filter by parent entity (Asset/Shot), file type, or list all.
    Returns id, code, file_type, entity, path, and version_number.

    Args:
        params (FindPublishedFilesInput): Validated input containing:
            - entity_type (Optional[str]): 'Asset' or 'Shot'
            - entity_id (Optional[int]): Parent entity ID
            - file_type (Optional[str]): Publish type filter
            - limit (int): Max results, default 50

    Returns:
        str: JSON with {"total": int, "publishes": [...]}
    """
    return await find_published_files_impl(params)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """Run the FPT MCP server via stdio transport."""
    mcp.run()


if __name__ == "__main__":
    main()
