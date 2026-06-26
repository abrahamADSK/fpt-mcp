"""ShotGrid API client wrapper with async support.

Wraps shotgun_api3.Shotgun (synchronous) in asyncio.to_thread calls
so every tool can be async without blocking the event loop.
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from typing import Any, Callable

import shotgun_api3
from dotenv import load_dotenv

from fpt_mcp.logging_config import configure_logging, get_logger, sanitize_for_log

# Install the rotating file handler as early as possible so the very first
# ShotGrid call (and any auth/connection fault it raises) is captured.
configure_logging()
_logger = get_logger("fpt_mcp.client")

# override=True: values from .env must win over any stale env var
# inherited from the parent process. Without override, a long-lived
# stdio server spawned while .env still contained placeholder values
# would retain those bad values for its whole lifetime even after .env
# is fixed, because SHOTGRID_URL is already present at load time.
#
# EXCEPTION — SHOTGRID_PROJECT_ID: an explicitly-injected value (the Qt
# console binding the MCP session to the project it was launched for, Chat 69)
# MUST win over the .env default. override=True would otherwise clobber the
# inherited value back to the .env one, so capture it first and restore it
# after the dotenv load. Credentials keep the override=True semantics above.
_injected_project_id = os.environ.get("SHOTGRID_PROJECT_ID")
# Anchor on THIS repo's .env by absolute path, not the cwd. Another client (a
# Maya/Flame console, Claude Desktop) can spawn fpt-mcp from a different cwd, and
# a bare load_dotenv() searches up from the cwd — it would then load the wrong
# repo's .env and lose the ShotGrid credentials (Chat 76, symmetric to the
# maya-mcp WORLDLABS_API_KEY fix).
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_ENV_PATH, override=True)
if _injected_project_id:
    os.environ["SHOTGRID_PROJECT_ID"] = _injected_project_id

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SHOTGRID_URL: str = os.getenv("SHOTGRID_URL", "")
SCRIPT_NAME: str = os.getenv("SHOTGRID_SCRIPT_NAME", "")
SCRIPT_KEY: str = os.getenv("SHOTGRID_SCRIPT_KEY", "")
PROJECT_ID: int = int(os.getenv("SHOTGRID_PROJECT_ID", "0"))

# Socket/request timeout (seconds) applied to the shared connection via
# ``sg.config.timeout_secs``. Without it a non-responding ShotGrid server
# blocks the asyncio.to_thread worker forever with no recovery. shotgun_api3
# threads this value straight into httplib2's socket timeout.
TIMEOUT_SECS: float = float(os.getenv("SHOTGRID_TIMEOUT_SECS", "30"))

# Placeholder values shipped in .env.example. If the user never filled in
# .env (or just ran setup_venv.sh / install.sh and forgot step 2), these
# strings leak through and the first real ShotGrid call fails with an
# opaque SSL CERTIFICATE_VERIFY_FAILED error. Fail fast and loud instead.
_PLACEHOLDER_FRAGMENTS = {
    "SHOTGRID_URL": ("YOUR_SITE", "yoursite.shotgrid"),
    "SHOTGRID_SCRIPT_NAME": ("your_script_name",),
    "SHOTGRID_SCRIPT_KEY": ("your_script_key", "your_key"),
}


def _validate_config() -> None:
    """Raise early if required env vars are missing or still hold placeholders."""
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

    placeholders = []
    for var, value in (
        ("SHOTGRID_URL", SHOTGRID_URL),
        ("SHOTGRID_SCRIPT_NAME", SCRIPT_NAME),
        ("SHOTGRID_SCRIPT_KEY", SCRIPT_KEY),
    ):
        lowered = value.lower()
        if any(frag.lower() in lowered for frag in _PLACEHOLDER_FRAGMENTS[var]):
            placeholders.append(var)
    if placeholders:
        raise EnvironmentError(
            "ShotGrid credentials still hold placeholder values from .env.example: "
            f"{', '.join(placeholders)}. "
            "Edit .env at the repo root and replace the template values with "
            "your real ShotGrid site URL, script name, and application key "
            "(ShotGrid Admin → Scripts). "
            "Without this step every call will fail with an SSL certificate error."
        )


# ---------------------------------------------------------------------------
# Singleton connection
# ---------------------------------------------------------------------------

_sg_instance: shotgun_api3.Shotgun | None = None

# Guards lazy construction of the singleton (double-checked locking).
_sg_init_lock = threading.Lock()

# Serializes USE of the shared connection. shotgun_api3.Shotgun wraps a single
# httplib2 connection (one socket). Every async wrapper runs its op via
# asyncio.to_thread, so concurrent tool calls (MCP can dispatch in parallel;
# HTTP mode and any future fan-out expose it) would otherwise interleave reads
# and writes on that one socket and corrupt responses. This lock makes each
# op atomic with respect to the others without giving up the singleton.
_sg_call_lock = threading.Lock()


def get_sg() -> shotgun_api3.Shotgun:
    """Return a cached ShotGrid connection (thread-safe singleton)."""
    global _sg_instance
    if _sg_instance is None:
        with _sg_init_lock:
            # Re-check inside the lock: another thread may have built it while
            # we were blocked on the lock.
            if _sg_instance is None:
                _validate_config()
                sg = shotgun_api3.Shotgun(
                    SHOTGRID_URL,
                    script_name=SCRIPT_NAME,
                    api_key=SCRIPT_KEY,
                )
                # Apply the socket/request timeout so a dead server cannot hang
                # a worker thread indefinitely. Best-effort: a stub/mock Shotgun
                # in tests may not expose `.config`.
                try:
                    sg.config.timeout_secs = TIMEOUT_SECS
                except AttributeError:
                    pass
                _sg_instance = sg
    return _sg_instance


def _sg_call(op: str, method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run one ShotGrid SDK call under the shared-connection lock with logging.

    This is the single chokepoint for every ShotGrid I/O in the server:
      * serializes access to the shared httplib2 socket (thread-safety fix),
      * logs the op + sanitized arguments before the call, and
      * logs the full traceback on failure before re-raising, so an
        AuthenticationFault / connection timeout is reconstructable from the
        rotating log instead of vanishing into the to_thread worker.

    The call is re-raised unchanged so existing all-or-nothing semantics
    (e.g. batch rollback) and error classification at the tool boundary are
    preserved.
    """
    _logger.info("sg op=%s args=%s", op, sanitize_for_log(list(args)))
    with _sg_call_lock:
        try:
            return method(*args, **kwargs)
        except Exception:
            _logger.exception("sg op=%s FAILED", op)
            raise


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
        _sg_call, "find", sg.find, entity_type, filters, fields, order=order or [], limit=limit
    )


async def sg_find_one(
    entity_type: str,
    filters: list,
    fields: list[str],
) -> dict[str, Any] | None:
    """Async wrapper around sg.find_one()."""
    sg = get_sg()
    return await asyncio.to_thread(_sg_call, "find_one", sg.find_one, entity_type, filters, fields)


async def sg_create(
    entity_type: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Async wrapper around sg.create()."""
    sg = get_sg()
    return await asyncio.to_thread(_sg_call, "create", sg.create, entity_type, data)


async def sg_update(
    entity_type: str,
    entity_id: int,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Async wrapper around sg.update()."""
    sg = get_sg()
    return await asyncio.to_thread(_sg_call, "update", sg.update, entity_type, entity_id, data)


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
        _sg_call, "upload", sg.upload, entity_type, entity_id, path, field_name, display_name
    )


async def sg_upload_thumbnail(
    entity_type: str,
    entity_id: int,
    path: str,
) -> int:
    """Async wrapper around sg.upload_thumbnail()."""
    sg = get_sg()
    return await asyncio.to_thread(_sg_call, "upload_thumbnail", sg.upload_thumbnail, entity_type, entity_id, path)


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
        await asyncio.to_thread(_sg_call, "download_attachment", sg.download_attachment, attachment, file_path=file_path)
    return file_path


async def sg_schema_field_read(
    entity_type: str,
    field_name: str | None = None,
) -> dict[str, Any]:
    """Async wrapper around sg.schema_field_read()."""
    sg = get_sg()
    return await asyncio.to_thread(_sg_call, "schema_field_read", sg.schema_field_read, entity_type, field_name)


async def sg_batch(
    requests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Async wrapper around sg.batch(). Transactional — all or nothing."""
    sg = get_sg()
    return await asyncio.to_thread(_sg_call, "batch", sg.batch, requests)


async def sg_revive(
    entity_type: str,
    entity_id: int,
) -> bool:
    """Async wrapper around sg.revive(). Restores a soft-deleted entity."""
    sg = get_sg()
    return await asyncio.to_thread(_sg_call, "revive", sg.revive, entity_type, entity_id)


async def sg_text_search(
    text: str,
    entity_types: dict[str, list[list]],
    project_ids: list[int] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Async wrapper around sg.text_search(). Full-text search across entities."""
    sg = get_sg()
    return await asyncio.to_thread(
        _sg_call, "text_search", sg.text_search, text, entity_types, project_ids=project_ids, limit=limit
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
        _sg_call, "summarize", sg.summarize, entity_type, filters, summary_fields, grouping=grouping
    )


async def sg_note_thread_read(
    note_id: int,
    entity_fields: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Async wrapper around sg.note_thread_read(). Returns full reply thread."""
    sg = get_sg()
    return await asyncio.to_thread(
        _sg_call, "note_thread_read", sg.note_thread_read, note_id, entity_fields=entity_fields
    )


async def sg_activity_stream_read(
    entity_type: str,
    entity_id: int,
    limit: int = 20,
) -> dict[str, Any]:
    """Async wrapper around sg.activity_stream_read()."""
    sg = get_sg()
    return await asyncio.to_thread(
        _sg_call, "activity_stream_read", sg.activity_stream_read, entity_type, entity_id, limit=limit
    )
