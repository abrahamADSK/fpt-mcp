"""shotgrid.py — bodies of the direct SG tools + bulk dispatcher handlers.

Extracted from server.py in Bucket F Phase 2d. Contains:
  - sg_find_impl, sg_create_impl, sg_update_impl
  - sg_schema_impl, sg_upload_impl, sg_download_impl
  - _do_sg_delete, _do_sg_batch, _do_sg_revive  (bulk dispatcher handlers)
  - BULK_DISPATCH                               (dict: BulkAction → handler)

Impl functions are pure: they do NOT bump `_stats["exec_calls"]`,
`tokens_in`, or `tokens_out`. The @mcp.tool wrappers in server.py handle
those increments so:
  - test_telemetry AST scan of server.py still finds the increments
  - the bookkeeping is consistent with launcher / toolkit_tools patterns

`_stats["safety_blocks"]` stays inside impls where the safety check
fires, because the wrapper cannot know which code path triggered the
block.

Imports from `fpt_mcp.server` are lazy so existing test patches
(`patch("fpt_mcp.server.get_sg", ...)` etc.) keep intercepting calls
that originate in this module.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from fpt_mcp.models import (
    SgBatchInput,
    SgCreateInput,
    SgDeleteInput,
    SgDownloadInput,
    SgFindInput,
    SgReviveInput,
    SgSchemaInput,
    SgUpdateInput,
    SgUploadInput,
)
from fpt_mcp.sg_errors import sg_errors_to_json

# Matches: "API create() Cut.fps expected [BigDecimal, Float, NilClass] ... but got Integer"
_FLOAT_EXPECTED_RE = re.compile(
    r"API (?:create|update)\(\) \w+\.(\w+) expected \[[^\]]*(?:Float|BigDecimal)[^\]]*\].*but got Integer",
    re.IGNORECASE | re.DOTALL,
)


def _coerce_float_fields(data: dict, error_msg: str) -> dict | None:
    """Return a copy of *data* with the offending int field coerced to float.

    Parses ShotGrid's Integer→Float type-mismatch Fault message to find the
    field name and coerces it. Returns None if this is not a float issue.
    """
    m = _FLOAT_EXPECTED_RE.search(error_msg)
    if not m:
        return None
    field_name = m.group(1)
    if field_name not in data or not isinstance(data[field_name], int):
        return None
    fixed = dict(data)
    fixed[field_name] = float(data[field_name])
    return fixed


@sg_errors_to_json
async def sg_find_impl(params: SgFindInput) -> str:
    """Body of sg_find_tool. See server.py for user-facing docstring."""
    from fpt_mcp.server import (
        PROJECT_ID, _PROJECT_SCOPED_ENTITIES, _rag_skipped_warning, _stats,
        check_dangerous, sg_find,
    )

    params_str = json.dumps({"filters": params.filters, "limit": params.limit}, default=str)
    warning = check_dangerous(params_str, tool_name="sg_find")
    if warning:
        _stats["safety_blocks"] += 1
        return json.dumps({"safety_warning": warning})

    filters = list(params.filters)
    project_warning = None
    is_scoped = params.entity_type in _PROJECT_SCOPED_ENTITIES

    if is_scoped and not PROJECT_ID:
        project_warning = (
            f"⚠️  SHOTGRID_PROJECT_ID is not set (0). "
            f"This {params.entity_type} query spans ALL projects on the site. "
            f"Set SHOTGRID_PROJECT_ID in .env or add a project filter manually."
        )
    elif is_scoped and not params.add_project_filter:
        project_warning = (
            f"⚠️  add_project_filter=false on a project-scoped entity "
            f"({params.entity_type}). Results may include entities from "
            f"other projects. Active project: {PROJECT_ID}."
        )

    if params.add_project_filter and PROJECT_ID:
        filters.append(["project", "is", {"type": "Project", "id": PROJECT_ID}])

    results = await sg_find(
        params.entity_type, filters, params.fields,
        order=params.order, limit=params.limit,
    )
    payload: dict[str, Any] = {"total": len(results), "entities": results}
    if project_warning:
        payload["project_scope_warning"] = project_warning
    rag_warning = _rag_skipped_warning()
    if rag_warning:
        payload.update(rag_warning)
    return json.dumps(payload, default=str)


@sg_errors_to_json
async def sg_create_impl(params: SgCreateInput) -> str:
    """Body of sg_create_tool."""
    from fpt_mcp.server import (
        PROJECT_ID, _rag_skipped_warning, _stats, check_dangerous, sg_create,
    )

    data = dict(params.data)
    # Some global entities (TaskTemplate, Step, …) use 'projects' (multi_entity)
    # not 'project' (single entity). Template tasks (Task + task_template, no entity)
    # also must not have a project field. Skip auto-inject for all these cases.
    _NO_PROJECT_FIELD = frozenset({"TaskTemplate", "Step"})
    _is_template_task = (
        params.entity_type == "Task"
        and "task_template" in data
        and "entity" not in data
    )
    if "project" not in data and PROJECT_ID and params.entity_type not in _NO_PROJECT_FIELD and not _is_template_task:
        data["project"] = {"type": "Project", "id": PROJECT_ID}
    # Remove an explicit None sentinel the caller may have passed to suppress inject
    data.pop("project", None) if data.get("project") is None else None

    params_str = json.dumps({"entity_type": params.entity_type, "data": data}, default=str)
    safety_warning = check_dangerous(params_str, tool_name="sg_create")
    if safety_warning:
        _stats["safety_blocks"] += 1
        return json.dumps({"safety_warning": safety_warning})

    try:
        result = await sg_create(params.entity_type, data)
    except Exception as exc:
        fixed = _coerce_float_fields(data, str(exc))
        if fixed is None:
            raise
        result = await sg_create(params.entity_type, fixed)
    payload: dict[str, Any] = {"created": result} if not isinstance(result, dict) else dict(result)
    rag_warning = _rag_skipped_warning()
    if rag_warning:
        payload.update(rag_warning)
    return json.dumps(payload, default=str)


@sg_errors_to_json
async def sg_update_impl(params: SgUpdateInput) -> str:
    """Body of sg_update_tool."""
    from fpt_mcp.server import (
        _rag_skipped_warning, _stats, check_dangerous, sg_update,
    )

    params_str = json.dumps(
        {"entity_type": params.entity_type, "entity_id": params.entity_id, "data": params.data},
        default=str,
    )
    safety_warning = check_dangerous(params_str, tool_name="sg_update")
    if safety_warning:
        _stats["safety_blocks"] += 1
        return json.dumps({"safety_warning": safety_warning})

    try:
        result = await sg_update(params.entity_type, params.entity_id, params.data)
    except Exception as exc:
        fixed = _coerce_float_fields(dict(params.data), str(exc))
        if fixed is None:
            raise
        result = await sg_update(params.entity_type, params.entity_id, fixed)
    payload: dict[str, Any] = {"updated": result} if not isinstance(result, dict) else dict(result)
    rag_warning = _rag_skipped_warning()
    if rag_warning:
        payload.update(rag_warning)
    return json.dumps(payload, default=str)


@sg_errors_to_json
async def sg_schema_impl(params: SgSchemaInput) -> str:
    """Body of sg_schema_tool."""
    from fpt_mcp.server import sg_schema_field_read

    schema = await sg_schema_field_read(params.entity_type, params.field_name)
    summary: dict[str, Any] = {}
    for field_name, info in schema.items():
        summary[field_name] = {
            "type": info.get("data_type", {}).get("value", "unknown"),
            "label": info.get("name", {}).get("value", field_name),
            "editable": info.get("editable", {}).get("value", False),
        }
    return json.dumps(summary, default=str)


@sg_errors_to_json
async def sg_upload_impl(params: SgUploadInput) -> str:
    """Body of sg_upload_tool."""
    from fpt_mcp.server import sg_upload, sg_upload_thumbnail

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


@sg_errors_to_json
async def sg_download_impl(params: SgDownloadInput) -> str:
    """Body of sg_download_tool."""
    from fpt_mcp.server import _get_tk_config, sg_download_attachment, sg_find_one
    from fpt_mcp.paths import enforce_write_containment, resolve_allowed_roots

    entity = await sg_find_one(
        params.entity_type,
        [["id", "is", params.entity_id]],
        [params.field_name],
    )
    if not entity or not entity.get(params.field_name):
        return json.dumps({
            "error": f"No attachment in {params.field_name} for {params.entity_type} #{params.entity_id}"
        })

    attachment = entity[params.field_name]

    # Path-containment guard: anchor the download destination on an allowed
    # root BEFORE writing the file. Allowed roots = the discovered project_root
    # for the current SHOTGRID_PROJECT_ID (so a single-project install gets
    # containment for free, with no FPT_MCP_ALLOWED_WRITE_ROOTS to configure)
    # UNION that env list. Discovery is best-effort enrichment: a missing
    # PROJECT_ID or ANY discovery failure must NOT block the download, so it
    # silently falls back to the env-only allowlist (prior behaviour). Default
    # policy is WARN (log + allow); a hard refusal happens only under
    # FPT_MCP_STRICT_PATHS=1. See proposals/fpt-path-containment-allowlist.md.
    project_root = None
    try:
        tk_config = await _get_tk_config()
        if tk_config is not None:
            project_root = tk_config.project_root
    except Exception:  # noqa: BLE001 - discovery is optional; never block the download
        project_root = None
    allowed_roots = resolve_allowed_roots(project_root)
    containment_error = enforce_write_containment(
        params.download_path, allowed_roots, tool_name="sg_download"
    )
    if containment_error:
        return json.dumps({"error": containment_error})

    path = await sg_download_attachment(attachment, params.download_path)
    return json.dumps({"path": path, "entity_type": params.entity_type, "entity_id": params.entity_id})


# ---------------------------------------------------------------------------
# Bulk dispatcher handlers
# ---------------------------------------------------------------------------


@sg_errors_to_json
async def _do_sg_delete(params: dict) -> str:
    """Delete (retire) an entity. Soft-delete — can be restored."""
    from pydantic import ValidationError
    from fpt_mcp.server import _rag_skipped_warning, _stats, check_dangerous, get_sg

    try:
        validated = SgDeleteInput(**params)
    except ValidationError as e:
        return json.dumps({"error": f"Invalid params for delete: {e}"})

    params_str = json.dumps({"entity_type": validated.entity_type, "entity_id": validated.entity_id})
    safety_warning = check_dangerous(params_str, tool_name="sg_delete")
    if safety_warning:
        _stats["safety_blocks"] += 1
        return json.dumps({"safety_warning": safety_warning})

    from fpt_mcp.client import _sg_call
    sg = get_sg()
    result = await asyncio.to_thread(_sg_call, "delete", sg.delete, validated.entity_type, validated.entity_id)
    payload: dict[str, Any] = {
        "deleted": result,
        "entity_type": validated.entity_type,
        "entity_id": validated.entity_id,
    }
    rag_warning = _rag_skipped_warning()
    if rag_warning:
        payload.update(rag_warning)
    return json.dumps(payload)


@sg_errors_to_json
async def _do_sg_batch(params: dict) -> str:
    """Execute multiple ShotGrid operations in a single transactional call."""
    from pydantic import ValidationError
    from fpt_mcp.server import _stats, check_dangerous, sg_batch

    try:
        validated = SgBatchInput(**params)
    except ValidationError as e:
        return json.dumps({"error": f"Invalid params for batch: {e}"})

    batch_data = json.loads(validated.requests)
    # Prefix each sub-request with its ``sg_<request_type>`` form so the
    # content-keyed safety patterns (which are scoped by an sg_update /
    # sg_delete prefix) also fire on batch sub-operations — a batch is just
    # as capable of unlinking a project, soft-deleting, or rewriting a
    # PublishedFile path as the direct tools.
    scan_target = "\n".join(
        f"sg_{req.get('request_type', '')} {json.dumps(req, default=str)}"
        for req in batch_data
        if isinstance(req, dict)
    ) or validated.requests
    safety_warning = check_dangerous(scan_target)
    if safety_warning:
        _stats["safety_blocks"] += 1
        # Wrap in the same {"safety_warning": ...} JSON envelope every other
        # handler uses. Returning the raw string broke the JSON contract and
        # made _result_is_error() miss the block, so a safety-blocked batch was
        # not counted as a failed turn (artificially deflating p_fallo).
        return json.dumps({"safety_warning": safety_warning})

    results = await sg_batch(batch_data)
    return json.dumps(results, default=str)


@sg_errors_to_json
async def _do_sg_revive(params: dict) -> str:
    """Restore a soft-deleted (retired) entity."""
    from pydantic import ValidationError
    from fpt_mcp.server import sg_revive

    try:
        validated = SgReviveInput(**params)
    except ValidationError as e:
        return json.dumps({"error": f"Invalid params for revive: {e}"})

    result = await sg_revive(validated.entity_type, validated.entity_id)
    return json.dumps({
        "revived": result,
        "entity_type": validated.entity_type,
        "entity_id": validated.entity_id,
    })
