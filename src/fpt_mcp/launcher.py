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
import os
import re
import socket
import subprocess
from typing import Any, Optional

from fpt_mcp.models import FptLaunchAppInput
from fpt_mcp.sg_errors import sg_errors_to_json

# Stone+Wire project lister — authoritative local Flame project source
# (all volumes, runs as the current user, works with the Flame GUI closed).
_SW_LIST_PROJECTS = "/opt/Autodesk/sw/tools/sw_listProjects"

# Fallback scan dir for Flame projects when Stone+Wire tools are absent.
_FLAME_PROJECTS_DIR = "/opt/Autodesk/project"

# Any running Flame-family GUI matches this (startApp lives inside the
# bundle): Flame is effectively single-instance per framestore and holds
# exclusive per-project locks, so launches are refused while one is up.
_FLAME_PROC_PATTERN = "flame.app/Contents/MacOS/"

# Maya single-instance guard. fpt-mcp does NOT own the Maya Command Port —
# it reads the host/port from the shared environment, mirroring maya-mcp's
# canonical localhost:8100 defaults (MAYA_PORT was moved off the historical
# 7001 to dodge Flame's Stone+Wire services). A running Maya binds this TCP
# port for the maya-mcp bridge; a crashed Maya releases it.
_MAYA_HOST = os.environ.get("MAYA_HOST", "localhost")
_MAYA_PORT = int(os.environ.get("MAYA_PORT", "8100"))


def _maya_command_port_open(
    host: str = _MAYA_HOST, port: int = _MAYA_PORT
) -> bool:
    """True when something is listening on the Maya Command Port.

    A running Maya bound to the maya-mcp bridge port (``MAYA_PORT``, default
    ``8100``) accepts the TCP connection; a crashed or closed Maya has
    released the port, so the connect fails and we report it free — a
    crashed Maya never causes a false refusal. Mirrors the ecosystem's
    ``_tcp_check`` style: a short 2s connect probe that swallows the
    refusal / timeout / OS errors (all ``OSError`` subclasses —
    ``ConnectionRefusedError``, ``TimeoutError``, ``socket.gaierror``) as
    "nothing there".
    """
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


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


# Entity types whose Toolkit launch is scoped step-LESS by default, but whose
# real work happens under a pipeline Step. `tank Sequence <id> <cmd>` boots the
# base `sequence` environment: workfiles2 has no `template_work` (the app reports
# "No templates have been defined") and the {Step} folder token falls back to the
# lowercased Step name (a rogue `.../layout/...` instead of the short_name
# `.../LAY/...`). Mapping the entity to its default Step lets the launcher target
# the Task so `tank Task <id> <cmd>` boots the step environment (create_folders +
# pick_environment). Sequence only today; Asset/Shot keep their entity-level
# launch (their steps are chosen via Workfiles/context change).
_DEFAULT_LAUNCH_STEP = {"Sequence": "Layout"}


def _resolve_step_task(
    sg: Any,
    entity_type: str,
    entity_id: int,
    step: Optional[str],
) -> Any:
    """Resolve a step-less entity to a concrete Task for a Toolkit launch.

    Looks up the entity's Tasks and returns the one whose Step matches the
    request (``step`` arg, else the entity's default from
    ``_DEFAULT_LAUNCH_STEP``) so the caller can launch ``tank Task <id> <cmd>``
    — booting Maya into the step environment instead of a work-template-less
    base context.

    Returns:
        ``("Task", task_id)`` — launch into this Task.
        ``None``             — no resolution needed/possible; keep the entity
                               context (unmapped entity, no Step, or SG error).
        ``{"error": str}``   — actionable failure (no/ambiguous Step Task); the
                               caller surfaces it instead of launching a broken
                               step-less context.
    """
    desired = step or _DEFAULT_LAUNCH_STEP.get(entity_type)
    if not desired:
        return None
    try:
        tasks = sg.find(
            "Task",
            [["entity", "is", {"type": entity_type, "id": entity_id}]],
            ["content", "step"],
        )
    except Exception:
        # Never block a launch on a task-resolution SG error — fall back to the
        # entity context (may still be navigable via Workfiles).
        return None
    if not isinstance(tasks, list):
        return None
    matches = [
        t for t in tasks
        if ((t.get("step") or {}).get("name") or "").lower() == desired.lower()
    ]
    if len(matches) == 1:
        return ("Task", matches[0]["id"])
    if not matches:
        available = sorted(
            {
                (t.get("step") or {}).get("name")
                for t in tasks
                if t.get("step")
            }
        )
        return {
            "error": (
                f"{entity_type} {entity_id} has no '{desired}' Task; launching "
                f"a {entity_type} needs a Step Task so Maya boots into the step "
                f"environment (which carries work templates). Available steps: "
                f"{', '.join(a for a in available if a) or 'none'}. Create the "
                f"'{desired}' Task, or pass step=<name>."
            )
        }
    return {
        "error": (
            f"{entity_type} {entity_id} has {len(matches)} '{desired}' Tasks; "
            f"cannot pick one automatically. Launch on the specific Task."
        )
    }


def _local_flame_projects() -> list[str]:
    """List Flame project names existing on this workstation.

    Thin name-only view over ``_local_flame_projects_with_paths`` (same
    Stone+Wire source and dir-scan fallback).
    """
    pairs, _source = _local_flame_projects_with_paths()
    return [name for name, _path in pairs]


def _local_flame_projects_with_paths() -> tuple[list[tuple[str, str]], str]:
    """List local Flame projects as ``((name, project_home), source)``.

    Project homes live on DIFFERENT volumes per project (framestore
    configuration) — the Stone+Wire DB is the authority for where each
    one's metadata clib lives. ``source`` is ``"stone_wire"`` (complete),
    ``"dir-scan"`` (degraded fallback — projects on other volumes are
    MISSING; callers must surface this, never hide it) or ``"none"``.
    """
    try:
        # 30s: Stone+Wire stalls up to ~20s validating dead network mounts
        # (/hosts/<name> over a stale interface) before answering — a 10s
        # timeout silently degraded to the dir-scan fallback (Chat 93).
        proc = subprocess.run(
            [_SW_LIST_PROJECTS], capture_output=True, text=True, timeout=30
        )
        pairs = [
            (m.group(2).strip(), m.group(3).strip())
            for line in proc.stdout.splitlines()
            if (m := _SW_PROJECT_LINE.match(line.strip()))
        ]
        if pairs:
            return pairs, "stone_wire"
    except Exception:
        pass
    try:
        scan = sorted(
            (e, os.path.join(_FLAME_PROJECTS_DIR, e))
            for e in os.listdir(_FLAME_PROJECTS_DIR)
            if not e.startswith(".")
            and os.path.isdir(os.path.join(_FLAME_PROJECTS_DIR, e))
        )
        return scan, "dir-scan"
    except Exception:
        return [], "none"


# Marker anchoring the FPT-link slot inside the project metadata clib: the
# shotgunProjectName field sits IMMEDIATELY before the Dolby Vision project
# setting (validated on 2025/2026/2027 specimens, Chat 93). Strings are
# 4-byte big-endian length-prefixed; length 0 = not linked.
_CLIB_LINK_MARKER = b"Dolby Vision"


def _read_fpt_link(project_name: str, project_home: str) -> Optional[str]:
    """Read the NATIVE Flame↔FPT link for a project from disk (READ-ONLY).

    Parses the project metadata clib — ``<home>/catalog/.#project.000.clib``
    (2027 layout) or ``/opt/Autodesk/clip/stonefs/<name>.prj/`` (older) —
    for the ``shotgunProjectName`` slot: the length-prefixed string right
    before the Dolby Vision settings field. Returns the linked SG project
    name, ``""`` when unlinked, or ``None`` when the clib is missing or the
    layout is not recognised (caller treats None as "cannot verify").

    Writes NEVER go through this path. Links are created and broken only
    from Flame's own Flow Production Tracking menu: the MCP write path
    (flame-mcp ``fpt_link`` set/break) was removed in Chat 98 after both
    in-vivo attempts triggered Flame's error report — the attribute write
    saves the whole project and the bridge runs off Flame's main thread.
    """
    candidates = [
        os.path.join(project_home, "catalog", ".#project.000.clib"),
        os.path.join(
            "/opt/Autodesk/clip/stonefs", f"{project_name}.prj",
            ".#project.000.clib",
        ),
    ]
    path = next((c for c in candidates if os.path.isfile(c)), None)
    if path is None:
        return None
    try:
        with open(path, "rb") as fh:
            data = fh.read(1 << 20)
    except OSError:
        return None
    i = data.find(_CLIB_LINK_MARKER)
    if i < 4:
        return None
    end = i - 4  # the marker's own length prefix starts at i-4
    for k in range(0, 256):
        start = end - k
        if start - 4 < 0:
            break
        if int.from_bytes(data[start - 4:start], "big") == k:
            try:
                return data[start:end].decode("utf-8")
            except UnicodeDecodeError:
                return None
    return None


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

    pairs, source = _local_flame_projects_with_paths()
    local = [n for n, _p in pairs]
    plan["sg_project_name"] = sg_name
    plan["project_source"] = source

    def _listing() -> list[dict[str, Any]]:
        return [
            {"name": n, "fpt_link": _read_fpt_link(n, p)} for n, p in pairs
        ]

    if params.list_projects:
        # Enumeration mode: the real local options WITH each project's
        # native link read from its metadata clib (READ-ONLY).
        plan["local_flame_projects"] = _listing()
        plan["choice_required"] = True
        plan["hint"] = (
            "re-call with flame_project=<name> to open one; links are "
            "created and broken from Flame's own Flow Production Tracking "
            "menu (flame-mcp's fpt_link only REPORTS the link — its write "
            "path was removed in Chat 98 after it triggered Flame's error "
            "report in-vivo)"
        )
        return json.dumps(plan, default=str)

    if params.flame_project:
        # Explicit user choice — case-insensitive exact match only.
        wanted = params.flame_project
        match = next(
            (n for n in local if n.lower() == wanted.lower()), None
        )
        plan["flame_project"] = match or wanted
        if match is None:
            plan["error"] = (
                f"Flame project '{wanted}' does not exist on this "
                f"workstation. Local projects: {', '.join(local) or '(none)'}."
            )
            return json.dumps(plan, default=str)
    else:
        # NATIVE LINK DISCOVERY (Chat 93, user-approved contract): read
        # each local project's shotgunProjectName from its clib. The
        # FPT↔Flame relation is 1:1 — exactly one linked project opens
        # directly; several is an INCONSISTENT state (never a menu); none
        # → the caller asks the user which project to open/link.
        linked = [
            (n, p) for n, p in pairs
            if (_read_fpt_link(n, p) or "").lower() == sg_name.lower()
        ]
        if len(linked) == 1:
            match = linked[0][0]
            plan["flame_project"] = match
            plan["fpt_linked"] = True
        elif len(linked) > 1:
            plan["error"] = (
                f"INCONSISTENT native-link state: {len(linked)} Flame "
                f"projects claim the FPT link to '{sg_name}': "
                f"{', '.join(n for n, _ in linked)}. The FPT↔Flame relation "
                f"is 1:1 — open the wrong project in Flame and break its "
                f"link from Flame's own Flow Production Tracking menu, then "
                f"retry. Nothing was launched."
            )
            return json.dumps(plan, default=str)
        else:
            plan["local_flame_projects"] = _listing()
            plan["choice_required"] = True
            plan["hint"] = (
                f"no local Flame project is linked to '{sg_name}'. Ask the "
                f"user which project to open and re-call with "
                f"flame_project=<name>. The link itself is created from "
                f"Flame's own Flow Production Tracking menu once the "
                f"project is loaded — flame-mcp's fpt_link only REPORTS it."
            )
            if source != "stone_wire":
                plan["warning"] = (
                    "project list came from the degraded dir-scan fallback "
                    "— projects on other volumes may be missing"
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


@sg_errors_to_json
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

    # Maya single-instance guard (parity with the Flame guard in
    # _compose_flame_direct). Every route — tank, direct, or the bare
    # 'open' fallback — spawns a FRESH Maya, so this lives before the route
    # dispatch and applies to all of them. A second Maya stealing the
    # Command Port would leave the maya-mcp bridge wired to a stale
    # instance. Refuse up-front (no argv composed) unless force=true, and
    # fire under dry_run too so a UI preview surfaces the conflict. A
    # crashed Maya releases the port, so this never false-refuses.
    if (
        result.app == "maya"
        and not params.force
        and _maya_command_port_open()
    ):
        plan["error"] = (
            f"a Maya instance is already bound to the Command Port "
            f"({_MAYA_HOST}:{_MAYA_PORT}). Launching a second Maya would "
            f"leave the maya-mcp bridge talking to a stale instance. Close "
            f"the running Maya first, or pass force=true to launch anyway."
        )
        return json.dumps(plan, default=str)

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
        # Resolve step-less entities (Sequence) to their Step Task so tank
        # boots the step environment (create_folders + pick_environment)
        # instead of a work-template-less base context. Asset/Shot keep their
        # entity-level launch. An unresolvable Step surfaces as an actionable
        # error rather than a broken launch.
        launch_type, launch_id = params.entity_type, params.entity_id
        if params.entity_type in _DEFAULT_LAUNCH_STEP:
            resolved = _resolve_step_task(
                sg, params.entity_type, params.entity_id, params.step
            )
            if isinstance(resolved, dict):
                plan.update(resolved)  # actionable {"error": ...}
                return json.dumps(plan, default=str)
            if resolved is not None:
                launch_type, launch_id = resolved
                plan["resolved_task"] = {
                    "requested": f"{params.entity_type} {params.entity_id}",
                    "launched_as": f"{launch_type} {launch_id}",
                    "step": (
                        params.step
                        or _DEFAULT_LAUNCH_STEP.get(params.entity_type)
                    ),
                }
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
            launch_type,
            str(launch_id),
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
