"""launcher.py — body of the fpt_launch_app MCP tool.

Extracted from server.py in Bucket F Phase 2b. The `@mcp.tool` decorator
stays in server.py as a thin wrapper that calls `fpt_launch_app_impl`,
so `install.sh` ast-extraction keeps finding the tool.

Lazy imports from `fpt_mcp.server` (`_stats`, `_tok`) are used to avoid
a circular import at module load. Phase 2e will move those stats
helpers to a neutral module and retire the lazy-import pattern.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
from typing import Any, Optional

from fpt_mcp.models import FptLaunchAppInput

# Stone+Wire project lister — authoritative local Flame project source
# (all volumes, runs as the current user, works with the Flame GUI closed).
_SW_LIST_PROJECTS = "/opt/Autodesk/sw/tools/sw_listProjects"

# Fallback scan dir for Flame projects when Stone+Wire tools are absent.
_FLAME_PROJECTS_DIR = "/opt/Autodesk/project"

# Any running Flame-family GUI matches this (startApp lives inside the
# bundle): Flame is effectively single-instance per framestore and holds
# exclusive per-project locks, so launches are refused while one is up.
_FLAME_PROC_PATTERN = "flame.app/Contents/MacOS/"

# sw_listProjects line format (amid noise):
#   UUID: name, /path/to/project, 1, YYYY-MM-DD HH:MM:SS.ffffff+TZ
# Parser ported from flame-mcp's _sw_list_projects (validated in-vivo there).
_SW_PROJECT_LINE = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r":\s+(.+?),\s+(/\S+?),\s+\d+,\s+(.+)$"
)

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


def _flame_slug(project_name: str) -> str:
    """SG project name → Flame project name, tk-flame's exact convention.

    tk-flame's ``project_startup.py::get_project_name`` derives the local
    Flame project name as ``re.sub(r"\\W+", "_", context.project["name"])``
    — every run of non-word characters collapses to one underscore. Using
    the identical rule means our direct launch resolves the same project
    that a Toolkit launch would have created.
    """
    return re.sub(r"\W+", "_", project_name)


def _local_flame_projects() -> list[str]:
    """List Flame project names existing on this workstation.

    Primary: ``sw_listProjects`` (Stone+Wire DB — all volumes, no GUI
    needed). Fallback: scan ``/opt/Autodesk/project``. Returns ``[]`` when
    neither source is available (no Flame install / S+W down), which the
    caller reports as "cannot verify" rather than guessing.
    """
    try:
        proc = subprocess.run(
            [_SW_LIST_PROJECTS], capture_output=True, text=True, timeout=10
        )
        names = [
            m.group(2).strip()
            for line in proc.stdout.splitlines()
            if (m := _SW_PROJECT_LINE.match(line.strip()))
        ]
        if names:
            return names
    except Exception:
        pass
    try:
        return sorted(
            e for e in os.listdir(_FLAME_PROJECTS_DIR)
            if not e.startswith(".")
            and os.path.isdir(os.path.join(_FLAME_PROJECTS_DIR, e))
        )
    except Exception:
        return []


def _flame_running() -> bool:
    """True when any Flame-family GUI process is running on this machine."""
    try:
        proc = subprocess.run(
            ["pgrep", "-f", _FLAME_PROC_PATTERN],
            capture_output=True, text=True, timeout=5,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _compose_flame_direct(
    params: FptLaunchAppInput,
    result: Any,
    plan: dict[str, Any],
    sg: Any,
    project_id: Optional[int],
) -> Optional[str]:
    """Compose the direct ``startApplication`` launch plan for Flame.

    Fills ``plan["argv"]`` (and the flame-specific plan fields) on success
    and returns ``None``; on any guard failure returns the finished JSON
    error payload for the tool to hand back. Guards, in order:

    1. The owning SG Project must be resolvable (name needed for mapping).
    2. The derived Flame project must EXIST locally — ``--start-project``
       with an unknown name makes Flame error out; tk-flame avoids this by
       pre-creating the project via Wiretap, which the direct route cannot
       do. The error suggests ``route='toolkit'`` (which can create it).
    3. No Flame instance may be running (single-instance per framestore +
       exclusive project locks) unless ``force=true``.
    """
    if project_id is None:
        plan["error"] = (
            f"cannot resolve the owning Project for {params.entity_type} "
            f"{params.entity_id}; a project context is required to map the "
            f"SG project to a local Flame project."
        )
        return json.dumps(plan, default=str)

    try:
        row = sg.find_one(
            "Project", [["id", "is", project_id]], ["name"]
        )
        sg_name = (row or {}).get("name")
    except Exception as exc:
        plan["error"] = f"could not read SG Project {project_id} name: {exc}"
        return json.dumps(plan, default=str)
    if not sg_name:
        plan["error"] = (
            f"SG Project {project_id} has no name; cannot derive the Flame "
            f"project name."
        )
        return json.dumps(plan, default=str)

    slug = _flame_slug(sg_name)
    local = _local_flame_projects()
    match = slug if slug in local else next(
        (p for p in local if p.lower() == slug.lower()), None
    )
    plan["sg_project_name"] = sg_name
    plan["flame_project"] = match or slug

    if match is None:
        suggestions = difflib.get_close_matches(slug, local, n=3, cutoff=0.6)
        hint = (
            f" Closest local projects: {', '.join(suggestions)}."
            if suggestions else ""
        )
        plan["error"] = (
            f"Flame project '{slug}' (derived from SG project '{sg_name}') "
            f"does not exist on this workstation.{hint} Create it in Flame "
            f"first, or launch with route='toolkit' — the tk-flame route "
            f"pre-creates missing projects via Wiretap."
        )
        return json.dumps(plan, default=str)

    if _flame_running() and not params.force:
        plan["error"] = (
            "a Flame instance is already running on this machine. Flame is "
            "effectively single-instance per framestore and holds exclusive "
            "project locks — a second launch would fail or fight for the "
            "lock. Close the running Flame first, or pass force=true to "
            "launch anyway."
        )
        return json.dumps(plan, default=str)

    argv = [str(result.binary), f"--start-project={match}"]
    if params.workspace:
        argv.append(f"--start-workspace={params.workspace}")
    else:
        # tk-flame's own default: create/use the default workspace rather
        # than failing when none is named.
        argv.append("--create-workspace")
    argv.append("--closed-libs")

    plan["argv"] = argv
    return None


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
        "route": params.route,
        "source_layers": result.source_layers,
        "warnings": list(result.warnings),
    }

    if result.app == "flame" and params.route in ("auto", "direct"):
        # Flame default route: direct startApplication into the matching
        # local project (no Toolkit/SSO dependency). route='toolkit' opts
        # into the tank path below for project creation + pipeline hooks.
        error_payload = _compose_flame_direct(
            params, result, plan, sg, project_id
        )
        if error_payload is not None:
            return error_payload
        argv = plan["argv"]
    elif params.route == "toolkit" and result.tank_command is None:
        plan["error"] = (
            "route='toolkit' requested but no usable Toolkit tank CLI was "
            "found for this project (no Advanced Setup PipelineConfiguration "
            "with a tank binary on disk). Use route='auto' or 'direct', or "
            "set up the pipeline configuration first."
        )
        return json.dumps(plan, default=str)
    elif (
        params.route != "direct"
        and result.launch_method == "tank"
        and result.tank_command is not None
    ):
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
        # Reached with no usable tank CLI, or with route='direct' explicitly
        # bypassing one — either way the app opens without Toolkit context.
        argv = ["open", "-a", str(result.binary)]
        plan["warnings"].append(
            "launching without Toolkit context; the app will open but not "
            "in the selected entity context"
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
