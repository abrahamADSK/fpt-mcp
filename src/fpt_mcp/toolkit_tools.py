"""toolkit_tools.py — bodies of tk_resolve_path and tk_publish.

Extracted from server.py in Bucket F Phase 2c. Only the `*_impl` bodies
move here; the `_get_tk_config` and `_build_template_fields` helpers
stay in server.py because the existing test suite patches them through
`fpt_mcp.server` and moving them would require invasive test updates.

Impls use lazy imports from `fpt_mcp.server` for every symbol the tests
patch (`_get_tk_config`, `_build_template_fields`, `sg_find_one`,
`sg_create`, `PROJECT_ID`) so existing monkeypatches keep intercepting.
`_stats` / `_tok` bookkeeping lives in the server.py wrapper so the
test_telemetry AST scan still finds the increments.
"""

from __future__ import annotations

import json
import os
from typing import Any

from fpt_mcp.models import TkPublishInput, TkResolvePathInput
from fpt_mcp.tk_config import TkConfigError


async def tk_resolve_path_impl(params: TkResolvePathInput) -> str:
    """Body of tk_resolve_path_tool. See server.py for user-facing docstring."""
    # Lazy imports: tests patch these on fpt_mcp.server, so going through
    # that binding lets monkeypatch intercept the calls from this module.
    from fpt_mcp.server import _get_tk_config, _build_template_fields

    try:
        tk_config = await _get_tk_config()
        if tk_config is None:
            return json.dumps({
                "error": "No PipelineConfiguration found for this project. "
                         "Cannot resolve Toolkit paths without a pipeline config. "
                         "Use an explicit publish_path in tk_publish instead."
            })

        # Build fields from SG entity context
        version = params.version
        if version is None:
            fields_probe = await _build_template_fields(
                params.entity_type, params.entity_id,
                params.step, params.name, 0, params.extension,
            )
            version = tk_config.next_version(params.template_name, fields_probe)

        fields = await _build_template_fields(
            params.entity_type, params.entity_id,
            params.step, params.name, version, params.extension,
        )

        path = tk_config.resolve_path(params.template_name, fields)

        return json.dumps({
            "path": str(path),
            "version": version,
            "template": params.template_name,
            "project_root": str(tk_config.project_root),
        })

    except TkConfigError as e:
        return json.dumps({"error": str(e)})


async def tk_publish_impl(params: TkPublishInput) -> str:
    """Body of tk_publish_tool. See server.py for user-facing docstring."""
    from fpt_mcp.server import (
        _get_tk_config,
        _build_template_fields,
        sg_find_one,
        sg_create,
        PROJECT_ID,
    )

    try:
        tk_config = await _get_tk_config()
        publish_path = None
        version = params.version_number or 1
        template_name = None

        if tk_config is not None and params.publish_path is None:
            # Mode 1: Resolve path from PipelineConfiguration templates
            # Caller should provide a template_name-like approach, but for
            # tk_publish we infer from publish_type + entity_type
            # Try to find a matching template by convention
            entity_key = "asset" if params.entity_type == "Asset" else "shot"
            # Search templates for one matching the publish type
            ptype_lower = params.publish_type.lower().replace(" ", "_")
            candidates = [
                f"{ptype_lower}_{entity_key}_publish",
                f"{entity_key}_{ptype_lower}_publish",
                f"{ptype_lower}_{entity_key}",
            ]
            for candidate in candidates:
                if tk_config.get_template(candidate):
                    template_name = candidate
                    break

            if template_name is None:
                # Try listing templates for a partial match
                all_templates = tk_config.list_templates(ptype_lower)
                if all_templates:
                    template_name = next(iter(all_templates))

            if template_name is None:
                return json.dumps({
                    "error": f"No template found matching publish_type='{params.publish_type}' "
                             f"for entity_type='{params.entity_type}'. "
                             f"Available templates: {list(tk_config.list_templates().keys())}. "
                             f"Provide an explicit publish_path instead."
                })

            ext = params.extension
            if version == 1 and params.version_number is None:
                fields_probe = await _build_template_fields(
                    params.entity_type, params.entity_id,
                    params.step, params.name, 0, ext,
                )
                version = tk_config.next_version(template_name, fields_probe)

            fields = await _build_template_fields(
                params.entity_type, params.entity_id,
                params.step, params.name, version, ext,
            )
            publish_path = tk_config.resolve_path(template_name, fields)

        elif params.publish_path is not None:
            # Mode 2: Explicit path provided by user. Resolve to absolute
            # so the existence check below and the error messages are not
            # ambiguous when the LLM passes a relative path (which would
            # otherwise be evaluated against the MCP server's cwd).
            from pathlib import Path as _Path
            publish_path = _Path(params.publish_path).resolve()

        else:
            return json.dumps({
                "error": "No PipelineConfiguration found and no publish_path provided. "
                         "Please provide an explicit publish_path where the file should be published."
            })

        # Pre-flight: if local_path is provided, it must actually exist on
        # disk before we resolve PublishedFileType / Task / etc. Catching
        # this early avoids a partial publish where the SG record is created
        # but the file copy fails halfway through.
        if params.local_path and not os.path.isfile(params.local_path):
            return json.dumps({
                "error": f"local_path does not exist: {params.local_path}. "
                         "Provide a valid path to the source file, or omit "
                         "local_path to register an already-published file."
            })

        # Pre-flight: in Mode 2 (explicit publish_path), if no local_path
        # was given the publish_path itself must already exist on disk.
        # Otherwise we'd be creating a PublishedFile record pointing at
        # nothing — a silent failure that surfaces far from the cause.
        if (
            params.publish_path is not None
            and not params.local_path
            and publish_path
            and not publish_path.exists()
        ):
            return json.dumps({
                "error": f"publish_path does not exist on disk and no local_path "
                         f"was provided to copy from: {publish_path}. "
                         "Either pass local_path to copy the file, or ensure "
                         "the file already exists at publish_path."
            })

        # Copy source file if provided
        if params.local_path and publish_path:
            import shutil
            publish_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(params.local_path, str(publish_path))

        # Find or create PublishedFileType
        pft = await sg_find_one(
            "PublishedFileType",
            [["code", "is", params.publish_type]],
            ["id", "code"],
        )
        if not pft:
            pft = await sg_create("PublishedFileType", {"code": params.publish_type})

        # Fetch entity code for the publish name
        entity = await sg_find_one(
            params.entity_type,
            [["id", "is", params.entity_id]],
            ["code"],
        )
        entity_code = entity["code"] if entity else f"{params.entity_type}_{params.entity_id}"

        # Find linked Task (if exists)
        task = await sg_find_one(
            "Task",
            [
                ["entity", "is", {"type": params.entity_type, "id": params.entity_id}],
                ["step.Step.short_name", "is", params.step],
            ],
            ["id", "content"],
        )

        # Create the PublishedFile
        data: dict[str, Any] = {
            "code": f"{entity_code}_{params.step}_{params.publish_type.replace(' ', '_')}_v{version:03d}",
            "published_file_type": {"type": "PublishedFileType", "id": pft["id"]},
            "entity": {"type": params.entity_type, "id": params.entity_id},
            "path": {"local_path": str(publish_path)},
            "version_number": version,
            "sg_status_list": "wtg",
        }
        if task:
            data["task"] = {"type": "Task", "id": task["id"]}
        if params.comment:
            data["description"] = params.comment
        if PROJECT_ID:
            data["project"] = {"type": "Project", "id": PROJECT_ID}

        result = await sg_create("PublishedFile", data)

        response = {
            "id": result["id"],
            "code": data["code"],
            "path": str(publish_path),
            "version_number": version,
            "entity": {"type": params.entity_type, "id": params.entity_id, "code": entity_code},
            "publish_type": params.publish_type,
            "task": task["content"] if task else None,
            "file_copied": params.local_path is not None,
        }
        if template_name:
            response["template"] = template_name
            response["project_root"] = str(tk_config.project_root)

        return json.dumps(response, default=str)

    except TkConfigError as e:
        return json.dumps({"error": str(e)})
