"""reporting.py — fpt_reporting dispatcher handlers.

Extracted from server.py in Bucket F Phase 2d. Contains:
  - _do_sg_text_search, _do_sg_summarize
  - _do_sg_note_thread, _do_sg_activity

All are pure (no `_stats["exec_calls"]` / `tokens_in` / `tokens_out`
increments). The `fpt_reporting` wrapper in server.py handles those so
the test_telemetry AST scan stays green.

Imports from `fpt_mcp.server` are lazy so existing test patches keep
intercepting calls from this module.
"""

from __future__ import annotations

import json
from typing import Any

from fpt_mcp.models import (
    SgActivityInput,
    SgNoteThreadInput,
    SgSummarizeInput,
    SgTextSearchInput,
)


async def _do_sg_text_search(params: dict) -> str:
    """Full-text search across multiple entity types simultaneously."""
    from pydantic import ValidationError
    from fpt_mcp.server import (
        PROJECT_ID, _PROJECT_SCOPED_ENTITIES, sg_text_search,
    )

    try:
        validated = SgTextSearchInput(**params)
    except ValidationError as e:
        return json.dumps({"error": f"Invalid params for text_search: {e}"})

    entity_types = json.loads(validated.entity_types)
    project_ids = [PROJECT_ID] if PROJECT_ID else None
    results = await sg_text_search(
        validated.text, entity_types, project_ids=project_ids, limit=validated.limit
    )
    payload: dict[str, Any] = results if isinstance(results, dict) else {"results": results}
    if not PROJECT_ID:
        scoped = [et for et in entity_types if et in _PROJECT_SCOPED_ENTITIES]
        if scoped:
            payload["project_scope_warning"] = (
                f"⚠️  SHOTGRID_PROJECT_ID is not set (0). "
                f"text_search for {', '.join(scoped)} spans ALL projects. "
                f"Set SHOTGRID_PROJECT_ID in .env to scope results."
            )
    return json.dumps(payload, default=str)


async def _do_sg_summarize(params: dict) -> str:
    """Server-side aggregation: count, sum, avg, min, max with optional grouping."""
    from pydantic import ValidationError
    from fpt_mcp.server import sg_summarize

    try:
        validated = SgSummarizeInput(**params)
    except ValidationError as e:
        return json.dumps({"error": f"Invalid params for summarize: {e}"})

    filters = json.loads(validated.filters)
    summary_fields = json.loads(validated.summary_fields)
    grouping = json.loads(validated.grouping) if validated.grouping else None
    results = await sg_summarize(
        validated.entity_type, filters, summary_fields, grouping=grouping
    )
    return json.dumps(results, default=str)


async def _do_sg_note_thread(params: dict) -> str:
    """Read the full reply thread of a Note."""
    from pydantic import ValidationError
    from fpt_mcp.server import sg_note_thread_read

    try:
        validated = SgNoteThreadInput(**params)
    except ValidationError as e:
        return json.dumps({"error": f"Invalid params for note_thread: {e}"})

    results = await sg_note_thread_read(validated.note_id)
    return json.dumps(results, default=str)


async def _do_sg_activity(params: dict) -> str:
    """Read the activity stream for an entity."""
    from pydantic import ValidationError
    from fpt_mcp.server import sg_activity_stream_read

    try:
        validated = SgActivityInput(**params)
    except ValidationError as e:
        return json.dumps({"error": f"Invalid params for activity: {e}"})

    results = await sg_activity_stream_read(
        validated.entity_type, validated.entity_id, limit=validated.limit
    )
    return json.dumps(results, default=str)
