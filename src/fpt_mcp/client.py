"""ShotGrid API client wrapper with async support.

Wraps shotgun_api3.Shotgun (synchronous) in asyncio.to_thread calls
so every tool can be async without blocking the event loop.
"""

from __future__ import annotations

import asyncio
import os
import sys
from functools import lru_cache
from typing import Any

import shotgun_api3
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SHOTGRID_URL: str = os.getenv("SHOTGRID_URL", "")
SCRIPT_NAME: str = os.getenv("SHOTGRID_SCRIPT_NAME", "")
SCRIPT_KEY: str = os.getenv("SHOTGRID_SCRIPT_KEY", "")
PROJECT_ID: int = int(os.getenv("SHOTGRID_PROJECT_ID", "0"))


def _validate_config() -> None:
    """Raise early if required env vars are missing."""
    missing = []
    if not SHOTGRID_URL:
        missing.append("SHOTGRID_URL")
    if not SCRIPT_NAME:
        missing.append("SHOTGRID_SCRIPT_NAME")
    if not SCRIPT_KEY:
        missing.append("SHOTGRID_SCRIPT_KEY")
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Check your .env file."
        )


# ---------------------------------------------------------------------------
# Singleton connection
# ---------------------------------------------------------------------------

_sg_instance: shotgun_api3.Shotgun | None = None


def get_sg() -> shotgun_api3.Shotgun:
    """Return a cached ShotGrid connection (thread-safe singleton)."""
    global _sg_instance
    if _sg_instance is None:
        _validate_config()
        _sg_instance = shotgun_api3.Shotgun(
            SHOTGRID_URL,
            script_name=SCRIPT_NAME,
            api_key=SCRIPT_KEY,
        )
    return _sg_instance


def get_project_filter() -> dict[str, Any]:
    """Return the project entity dict for filters. Uses PROJECT_ID from env."""
    if PROJECT_ID:
        return {"type": "Project", "id": PROJECT_ID}
    return {}


# ---------------------------------------------------------------------------
# Async wrappers for common ShotGrid operations
# ---------------------------------------------------------------------------

async def sg_find(
    entity_type: str,
    filters: list,
    fields: list[str],
    order: list[dict] | None = None,
    limit: int = 0,
) -> list[dict[str, Any]]:
    """Async wrapper around sg.find()."""
    sg = get_sg()
    return await asyncio.to_thread(
        sg.find, entity_type, filters, fields, order=order or [], limit=limit
    )


async def sg_find_one(
    entity_type: str,
    filters: list,
    fields: list[str],
) -> dict[str, Any] | None:
    """Async wrapper around sg.find_one()."""
    sg = get_sg()
    return await asyncio.to_thread(sg.find_one, entity_type, filters, fields)


async def sg_create(
    entity_type: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Async wrapper around sg.create()."""
    sg = get_sg()
    return await asyncio.to_thread(sg.create, entity_type, data)


async def sg_update(
    entity_type: str,
    entity_id: int,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Async wrapper around sg.update()."""
    sg = get_sg()
    return await asyncio.to_thread(sg.update, entity_type, entity_id, data)


async def sg_upload(
    entity_type: str,
    entity_id: int,
    path: str,
    field_name: str = "image",
    display_name: str | None = None,
) -> int:
    """Async wrapper around sg.upload()."""
    sg = get_sg()
    return await asyncio.to_thread(
        sg.upload, entity_type, entity_id, path, field_name, display_name
    )


async def sg_upload_thumbnail(
    entity_type: str,
    entity_id: int,
    path: str,
) -> int:
    """Async wrapper around sg.upload_thumbnail()."""
    sg = get_sg()
    return await asyncio.to_thread(sg.upload_thumbnail, entity_type, entity_id, path)


async def sg_download_attachment(
    attachment: dict[str, Any] | str,
    file_path: str,
) -> str:
    """Download an attachment or thumbnail URL from ShotGrid.

    Handles two cases:
    - dict attachment (e.g. from 'sg_uploaded_movie'): uses sg.download_attachment()
    - str URL (e.g. from 'image' thumbnail field): downloads via HTTP directly
    """
    import pathlib
    pathlib.Path(file_path).parent.mkdir(parents=True, exist_ok=True)

    if isinstance(attachment, str) and attachment.startswith("http"):
        # Thumbnail URL — download directly
        import urllib.request
        await asyncio.to_thread(urllib.request.urlretrieve, attachment, file_path)
    else:
        # Standard attachment dict
        sg = get_sg()
        await asyncio.to_thread(sg.download_attachment, attachment, file_path=file_path)
    return file_path


async def sg_schema_field_read(
    entity_type: str,
    field_name: str | None = None,
) -> dict[str, Any]:
    """Async wrapper around sg.schema_field_read()."""
    sg = get_sg()
    return await asyncio.to_thread(sg.schema_field_read, entity_type, field_name)


async def sg_batch(
    requests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Async wrapper around sg.batch(). Transactional — all or nothing."""
    sg = get_sg()
    return await asyncio.to_thread(sg.batch, requests)


async def sg_revive(
    entity_type: str,
    entity_id: int,
) -> bool:
    """Async wrapper around sg.revive(). Restores a soft-deleted entity."""
    sg = get_sg()
    return await asyncio.to_thread(sg.revive, entity_type, entity_id)


async def sg_text_search(
    text: str,
    entity_types: dict[str, list[list]],
    project_ids: list[int] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Async wrapper around sg.text_search(). Full-text search across entities."""
    sg = get_sg()
    return await asyncio.to_thread(
        sg.text_search, text, entity_types, project_ids=project_ids, limit=limit
    )


async def sg_summarize(
    entity_type: str,
    filters: list,
    summary_fields: list[dict[str, str]],
    grouping: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Async wrapper around sg.summarize(). Server-side aggregation."""
    sg = get_sg()
    return await asyncio.to_thread(
        sg.summarize, entity_type, filters, summary_fields, grouping=grouping
    )


async def sg_note_thread_read(
    note_id: int,
    entity_fields: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Async wrapper around sg.note_thread_read(). Returns full reply thread."""
    sg = get_sg()
    return await asyncio.to_thread(
        sg.note_thread_read, note_id, entity_fields=entity_fields
    )


async def sg_activity_stream_read(
    entity_type: str,
    entity_id: int,
    limit: int = 20,
) -> dict[str, Any]:
    """Async wrapper around sg.activity_stream_read()."""
    sg = get_sg()
    return await asyncio.to_thread(
        sg.activity_stream_read, entity_type, entity_id, limit=limit
    )
