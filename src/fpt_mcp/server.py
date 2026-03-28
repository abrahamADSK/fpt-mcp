#!/usr/bin/env python3
"""FPT MCP Server — Full Flow Production Tracking (ShotGrid) + Toolkit integration.

General-purpose MCP server exposing the complete ShotGrid API and Toolkit
path conventions. No entity restrictions — works with any entity type,
any field, any filter.

Designed to work alongside maya-mcp and flame-mcp as part of a
VFX pipeline orchestrated by Claude.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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
from fpt_mcp.paths import resolve_publish_path, next_version_number


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP("fpt_mcp")


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
    entity_code: str = Field(description="Entity code, e.g. 'hero_char' or 'SHOT010'.")
    step: str = Field(default="model", description="Pipeline step: model, rig, texture, anim, light, comp, etc.")
    publish_name: str = Field(default="main", description="Publish name for the path.")
    version: Optional[int] = Field(default=None, description="Version number. Auto-incremented if omitted.")
    asset_type: Optional[str] = Field(default=None, description="Asset type (required for Asset entities): Character, Environment, Prop, etc.")
    sequence_code: Optional[str] = Field(default=None, description="Sequence code (optional for Shot entities).")
    project_name: Optional[str] = Field(default=None, description="Project name for path. Auto-detected if omitted.")

class TkPublishInput(BaseModel):
    entity_type: str = Field(description="'Asset' or 'Shot'.")
    entity_id: int = Field(description="Entity ID in ShotGrid.")
    publish_type: str = Field(description="File type: any string, e.g. 'OBJ', 'Texture', 'Maya Scene', 'Alembic Cache', 'Nuke Script', 'EXR Sequence', 'Flame Batch', etc.")
    task: str = Field(default="model", description="Pipeline step: model, rig, texture, anim, light, comp, etc.")
    comment: Optional[str] = Field(default=None, description="Publish comment/notes.")
    local_path: Optional[str] = Field(default=None, description="Explicit file path. Auto-generated with Toolkit conventions if omitted.")
    version_number: Optional[int] = Field(default=None, description="Explicit version. Auto-incremented if omitted.")


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
    filters = list(params.filters)
    if params.add_project_filter and PROJECT_ID:
        filters.append(["project", "is", {"type": "Project", "id": PROJECT_ID}])

    results = await sg_find(
        params.entity_type, filters, params.fields,
        order=params.order, limit=params.limit,
    )
    return json.dumps({"total": len(results), "entities": results}, default=str)


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


@mcp.tool(name="tk_resolve_path")
async def tk_resolve_path_tool(params: TkResolvePathInput) -> str:
    """Resolve a Toolkit-compatible publish path using tk-config-default2 conventions.

    Returns the full file path that Toolkit loaders (tk-multi-loader2)
    in Maya, Flame, Nuke, etc. can pick up natively.
    """
    # Get project name if not provided
    project_name = params.project_name
    if not project_name and PROJECT_ID:
        proj = await sg_find_one("Project", [["id", "is", PROJECT_ID]], ["name"])
        project_name = proj["name"] if proj else "unknown_project"

    version = params.version
    if version is None:
        # Build the publish base path (without version) to scan existing versions
        publish_base = resolve_publish_path(
            project=project_name or "project",
            entity_type=params.entity_type.lower(),
            entity=params.entity_code,
            step=params.step,
            name=params.publish_name,
            version=0,
        ).parent  # go up from v000 to the name directory
        version = next_version_number(publish_base)

    path = resolve_publish_path(
        project=project_name or "project",
        entity_type=params.entity_type.lower(),
        entity=params.entity_code,
        step=params.step,
        name=params.publish_name,
        version=version,
    )
    return json.dumps({"path": str(path), "version": version, "project": project_name})


@mcp.tool(name="tk_publish")
async def tk_publish_tool(params: TkPublishInput) -> str:
    """Create a PublishedFile with Toolkit-compatible auto-versioned path.

    Registers a publish in ShotGrid with a path that follows
    tk-config-default2 conventions so Toolkit loaders can find it.
    """
    # Resolve entity info
    entity = await sg_find_one(
        params.entity_type,
        [["id", "is", params.entity_id]],
        ["code", "sg_asset_type"] if params.entity_type == "Asset" else ["code"],
    )
    if not entity:
        return json.dumps({"error": f"{params.entity_type} #{params.entity_id} not found"})

    entity_code = entity["code"]

    # Get project name
    project_name = "project"
    if PROJECT_ID:
        proj = await sg_find_one("Project", [["id", "is", PROJECT_ID]], ["name"])
        if proj:
            project_name = proj["name"]

    # Resolve version
    version = params.version_number
    if version is None:
        publish_base = resolve_publish_path(
            project=project_name,
            entity_type=params.entity_type.lower(),
            entity=entity_code,
            step=params.task,
            name=params.publish_type.replace(" ", "_").lower(),
            version=0,
        ).parent
        version = next_version_number(publish_base)

    # Resolve path
    publish_path = params.local_path
    if not publish_path:
        publish_path = str(resolve_publish_path(
            project=project_name,
            entity_type=params.entity_type.lower(),
            entity=entity_code,
            step=params.task,
            name=params.publish_type.replace(" ", "_").lower(),
            version=version,
        ))

    # Create the PublishedFile
    data: dict[str, Any] = {
        "code": f"{entity_code}_{params.task}_{params.publish_type.replace(' ','_')}_v{version:03d}",
        "published_file_type": {"type": "PublishedFileType", "code": params.publish_type},
        "entity": {"type": params.entity_type, "id": params.entity_id},
        "path": {"local_path": publish_path},
        "version_number": version,
        "sg_status_list": "wtg",
    }
    if params.comment:
        data["description"] = params.comment
    if PROJECT_ID:
        data["project"] = {"type": "Project", "id": PROJECT_ID}

    result = await sg_create("PublishedFile", data)
    return json.dumps({
        "id": result["id"],
        "code": data["code"],
        "path": publish_path,
        "version_number": version,
        "entity": {"type": params.entity_type, "id": params.entity_id, "code": entity_code},
        "publish_type": params.publish_type,
    }, default=str)


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
