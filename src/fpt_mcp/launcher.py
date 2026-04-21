"""launcher.py — body of the fpt_launch_app MCP tool.

Extracted from server.py in Bucket F Phase 2b. The `@mcp.tool` decorator
stays in server.py as a thin wrapper that calls `fpt_launch_app_impl`,
so `install.sh` ast-extraction keeps finding the tool.

Lazy imports from `fpt_mcp.server` (`_stats`, `_tok`) are used to avoid
a circular import at module load. Phase 2e will move those stats
helpers to a neutral module and retire the lazy-import pattern.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Optional

from fpt_mcp.models import FptLaunchAppInput

# Note on imports:
#   get_sg, resolve_app, _stats, _tok are imported lazily INSIDE the
#   functions (through fpt_mcp.server) rather than at module load. This
#   preserves the test contract where the existing suite patches
#   `fpt_mcp.server.get_sg` etc. — if we bound them here at top-level,
#   launcher.py would hold its own references and the monkeypatches
#   would not intercept. The extra dict lookup per call is negligible.
#   Phase 2e moves _stats / _tok to a neutral module and retires this
#   indirection.


def _project_id_for_entity(entity_type: str, entity_id: int) -> Optional[int]:
    """Resolve the Project id that owns a given entity.

    Project entities return their own id. For everything else, we look up
    the ``project`` field. Returns ``None`` on SG errors so the caller can
    degrade gracefully to a bare OS-scan result.
    """
    if entity_type == "Project":
        return entity_id
    from fpt_mcp.server import get_sg  # lazy for test-patch compatibility
    try:
        sg = get_sg()
        row = sg.find_one(
            entity_type, [["id", "is", entity_id]], ["project"]
        )
    except Exception:
        return None
    if not row or not row.get("project"):
        return None
    return row["project"].get("id")  # type: ignore[typeddict-item]  # shotgun_api3 BaseEntity stubs are incomplete


async def fpt_launch_app_impl(params: FptLaunchAppInput) -> str:
    """Launch a DCC application scoped to a ShotGrid entity.

    See server.py's `fpt_launch_app_tool` for the user-facing docstring.
    This module contains the implementation; the tool decorator lives in
    server.py to keep `install.sh` and the mcp_tool_inventory invariants
    stable. `_stats` bookkeeping lives in the wrapper so the
    test_telemetry AST-scan of server.py still sees the increments.
    """
    # Lazy imports: avoid circular dependency + let tests patch these
    # symbols on fpt_mcp.server (see module-level note above).
    from fpt_mcp.server import get_sg, resolve_app

    sg = get_sg()
    project_id = _project_id_for_entity(params.entity_type, params.entity_id)

    result = resolve_app(
        params.app,
        project_id=project_id,
        sg_find=sg.find,
    )
    if result is None:
        return json.dumps({
            "error": (
                f"{params.app} is not installed on this machine; cannot "
                f"launch. Install the app first and retry."
            )
        })

    plan: dict[str, Any] = {
        "app": result.app,
        "binary": str(result.binary),
        "version": result.version,
        "engine": result.engine,
        "launch_method": result.launch_method,
        "tank_command": (
            str(result.tank_command) if result.tank_command else None
        ),
        "pipeline_config_path": (
            str(result.pipeline_config_path)
            if result.pipeline_config_path
            else None
        ),
        "entity_type": params.entity_type,
        "entity_id": params.entity_id,
        "project_id": project_id,
        "source_layers": result.source_layers,
        "warnings": list(result.warnings),
    }

    if result.launch_method == "tank" and result.tank_command is not None:
        # tk-multi-launchapp registers its command under two common
        # conventions depending on the pipeline:
        #   1. launch_<app>      — default, single DCC version per config
        #   2. <app>_<version>   — multi-version pipelines that register
        #                          one launcher per installed version
        # We prefer pattern 2 when we have a version string from the OS
        # scan, since it is unambiguous across pipelines that expose both
        # a specific Maya release and legacy generic launchers. Callers
        # whose pipeline uses a non-standard convention should launch
        # Maya via a wrapper that maps to the right tank command.
        if result.version:
            cmd_name = f"{result.app}_{result.version}"
        else:
            cmd_name = f"launch_{result.app}"
        argv = [
            str(result.tank_command),
            params.entity_type,
            str(params.entity_id),
            cmd_name,
        ]
    else:
        argv = ["open", "-a", str(result.binary)]
        if result.launch_method != "tank":
            plan["warnings"].append(
                "launching without Toolkit context (no tank CLI); the app "
                "will open but not in the selected entity context"
            )

    plan["argv"] = argv

    if params.dry_run:
        plan["dry_run"] = True
        return json.dumps(plan, default=str)

    try:
        proc = subprocess.Popen(argv, start_new_session=True)
        plan["pid"] = proc.pid
    except Exception as exc:
        plan["error"] = f"launch failed: {exc}"

    return json.dumps(plan, default=str)
