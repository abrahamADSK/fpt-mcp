"""Software resolver — locate DCC applications on this machine and enrich
them with pipeline metadata from ShotGrid and Toolkit.

Discovery is OS-first: the primary source of truth is "is the binary
installed on this machine right now?". SG ``Software`` entity and
Toolkit ``PipelineConfiguration`` data provide metadata overlays
(version constraints, launch method, engine id) but never override the
OS-scan result. If the OS scan finds nothing, the resolver returns
``None`` regardless of what SG claims — you cannot launch what is not
installed.

Layer order::

    1. OS scan                  primary discovery (source of binary)
    2. Toolkit enrichment       upgrades launch_method to "tank" when the
                                project has an Advanced Setup config on
                                disk with a working ``tank`` CLI
    3. SG Software enrichment   attaches engine id and version constraints
                                when the SG Software entity is populated;
                                contributes nothing when the entity is an
                                empty stub

Typical usage::

    from fpt_mcp.software_resolver import resolve_app
    from fpt_mcp.client import get_sg

    sg = get_sg()
    result = resolve_app("maya", project_id=1244, sg_find=sg.find)
    if result is None:
        raise RuntimeError("Maya is not installed on this machine")
    print(result.binary, result.launch_method, result.warnings)
"""

from __future__ import annotations

import glob
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


# macOS install layout for Autodesk products. The glob captures any
# version suffix: ``maya2025``, ``maya2027``, etc.
_MACOS_MAYA_GLOB = "/Applications/Autodesk/maya*/Maya.app"

# Parse the version out of a Maya install path (``/maya2027/`` → ``2027``).
# Accepts optional ``.minor`` tail for future-proofing against ``maya2027.1``.
_MAYA_VERSION_RE = re.compile(r"/maya(\d{4}(?:\.\d+)?)/")

# Flame installs live under /opt/Autodesk, one directory per release
# (``flame_2027``, ``flame_2025.2.7``, …), each shipping the
# ``startApplication`` CLI launcher that tk-flame itself invokes. Autodesk
# maintains version symlinks (e.g. ``flame_2025.2.6 → flame_2025.2.7``) in
# the same layout, so the glob may yield duplicate targets — deduplicated
# by resolved path in the scanner.
_MACOS_FLAME_GLOB = "/opt/Autodesk/flame_*/bin/startApplication"

# Parse the version out of a Flame install path
# (``/flame_2025.2.7/`` → ``2025.2.7``; ``/flame_2027/`` → ``2027``).
_FLAME_VERSION_RE = re.compile(r"/flame_(\d{4}(?:\.\d+)*)/")

# User-visible app name → Toolkit engine id. Extend as more DCCs gain
# resolver coverage.
_APP_TO_ENGINE: dict[str, str] = {
    "maya": "tk-maya",
    "nuke": "tk-nuke",
    "houdini": "tk-houdini",
    "flame": "tk-flame",
}


@dataclass
class ResolvedApp:
    """A DCC application located on this machine with pipeline metadata.

    ``binary`` is guaranteed to exist on disk at resolution time. All other
    fields may be None when the relevant layer could not contribute.
    """

    app: str
    binary: Path
    version: Optional[str] = None
    engine: Optional[str] = None
    launch_method: str = "open"
    tank_command: Optional[Path] = None
    pipeline_config_path: Optional[Path] = None
    source_layers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Layer 1 — OS scan (primary discovery)
# ---------------------------------------------------------------------------


def _os_scan_maya(
    glob_pattern: str = _MACOS_MAYA_GLOB,
) -> list[tuple[Path, Optional[str]]]:
    """Scan the filesystem for Maya installs.

    Returns a list of ``(binary, version)`` tuples sorted newest-first so
    the caller can pick the first entry as the default. Entries with
    unparseable version strings sort to the end.
    """
    hits: list[tuple[Path, Optional[str]]] = []
    for path_str in glob.glob(glob_pattern):
        path = Path(path_str)
        if not path.exists():
            continue
        match = _MAYA_VERSION_RE.search(path_str)
        version = match.group(1) if match else None
        hits.append((path, version))

    def sort_key(item: tuple[Path, Optional[str]]) -> tuple[int, str]:
        _, v = item
        if not v:
            return (0, "")
        try:
            major = int(v.split(".")[0])
        except ValueError:
            return (0, v)
        return (1, f"{major:08d}")

    return sorted(hits, key=sort_key, reverse=True)


def _os_scan_flame(
    glob_pattern: str = _MACOS_FLAME_GLOB,
) -> list[tuple[Path, Optional[str]]]:
    """Scan the filesystem for Flame installs.

    Returns ``(startApplication_path, version)`` tuples sorted newest-first
    by the FULL version tuple — unlike Maya, Flame ships several minor
    releases per year (``2025.2.6``, ``2025.2.7``) plus version symlinks,
    so major-only sorting would tie. Symlinked duplicates of the same
    install are collapsed to the highest version label pointing at them.
    """
    by_target: dict[Path, tuple[Path, Optional[str]]] = {}
    for path_str in glob.glob(glob_pattern):
        path = Path(path_str)
        if not path.exists():  # dangling symlink
            continue
        match = _FLAME_VERSION_RE.search(path_str)
        version = match.group(1) if match else None
        target = path.resolve()
        prev = by_target.get(target)
        if prev is None or _flame_sort_key((path, version)) > _flame_sort_key(prev):
            by_target[target] = (path, version)

    return sorted(by_target.values(), key=_flame_sort_key, reverse=True)


def _flame_sort_key(item: tuple[Path, Optional[str]]) -> tuple:
    """Sort key over the full dotted version (``2025.2.7`` < ``2027``)."""
    _, v = item
    if not v:
        return (0, ())
    try:
        return (1, tuple(int(part) for part in v.split(".")))
    except ValueError:
        return (0, ())


def _os_scan(
    app: str, glob_pattern: Optional[str] = None
) -> list[tuple[Path, Optional[str]]]:
    """Dispatch to the per-app OS scanner. Unknown apps return ``[]``."""
    if app == "maya":
        return _os_scan_maya(glob_pattern or _MACOS_MAYA_GLOB)
    if app == "flame":
        return _os_scan_flame(glob_pattern or _MACOS_FLAME_GLOB)
    return []


# ---------------------------------------------------------------------------
# Layer 2 — Toolkit enrichment
# ---------------------------------------------------------------------------


def _toolkit_enrichment(
    project_id: int, sg_find: Callable[..., Any]
) -> tuple[Optional[Path], Optional[Path]]:
    """Resolve ``(pipeline_config_root, tank_command)`` for a project.

    Queries ``PipelineConfiguration`` for the project and returns the first
    Advanced Setup (localized) config whose ``mac_path`` exists on disk and
    ships a working ``tank`` binary. Returns ``(None, None)`` if none found
    or if the SG query fails.
    """
    try:
        configs = sg_find(
            "PipelineConfiguration",
            [["project", "is", {"type": "Project", "id": project_id}]],
            ["id", "code", "mac_path"],
        )
    except Exception:
        return (None, None)

    for cfg in configs or []:
        mac_path = cfg.get("mac_path")
        if not mac_path:
            continue
        root = Path(mac_path)
        tank_cmd = root / "tank"
        if root.is_dir() and tank_cmd.is_file():
            return (root, tank_cmd)

    return (None, None)


# ---------------------------------------------------------------------------
# Layer 3 — SG Software entity enrichment
# ---------------------------------------------------------------------------


def _sg_software_enrichment(
    engine: str,
    project_id: Optional[int],
    sg_find: Callable[..., Any],
) -> Optional[dict[str, Any]]:
    """Query the SG ``Software`` entity for the given engine.

    Prefers rows with a populated ``mac_path`` or ``version_names``. Returns
    ``None`` on SG error. Empty stubs are still returned (the caller then
    flags them as warning-only).
    """
    filters: list[Any] = [["engine", "is", engine]]
    if project_id is not None:
        filters.append(
            ["projects", "in", [{"type": "Project", "id": project_id}]]
        )
    try:
        rows = sg_find(
            "Software",
            filters,
            ["id", "code", "engine", "mac_path", "version_names", "projects"],
        )
    except Exception:
        return None

    if not rows:
        return None
    for row in rows:
        if row.get("mac_path") or row.get("version_names"):
            return row
    return rows[0]


def _pick_fpt_version(
    version_names: Any,
    os_hits: list[tuple[Path, Optional[str]]],
) -> Optional[tuple[Path, Optional[str]]]:
    """Match the FPT-selected version(s) against local installs.

    ``version_names`` is the SG ``Software.version_names`` value — a
    comma-separated string ("2027.0.1, 2026.2.3") or a list. Returns the
    highest local hit whose version exactly matches, or prefix-matches in
    either direction (FPT "2027" matches local "2027.0.1" and vice versa),
    so a coarse FPT selection still resolves a specific local install.
    Returns ``None`` when nothing is selected in FPT or nothing matches.
    """
    if not version_names:
        return None
    if isinstance(version_names, str):
        wanted = [v.strip() for v in version_names.split(",") if v.strip()]
    elif isinstance(version_names, (list, tuple)):
        wanted = [str(v).strip() for v in version_names if str(v).strip()]
    else:
        return None
    if not wanted:
        return None

    def matches(local: Optional[str], sel: str) -> bool:
        if not local:
            return False
        return (
            local == sel
            or local.startswith(sel + ".")
            or sel.startswith(local + ".")
        )

    # Exact matches first, then prefix matches; os_hits is newest-first.
    for hit in os_hits:
        if any(hit[1] == sel for sel in wanted):
            return hit
    for hit in os_hits:
        if any(matches(hit[1], sel) for sel in wanted):
            return hit
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_app(
    app: str,
    project_id: Optional[int] = None,
    sg_find: Optional[Callable[..., Any]] = None,
    glob_pattern: Optional[str] = None,
) -> Optional[ResolvedApp]:
    """Resolve a DCC application on this machine with SG enrichment.

    Args:
        app: app name, case-insensitive (``"maya"``, ``"nuke"``, ...).
        project_id: ShotGrid Project id. Without it, enrichment layers are
            skipped entirely and you get a bare OS-scan result.
        sg_find: a callable with the shotgun_api3 ``Shotgun.find`` signature.
            When ``None``, SG-backed layers are skipped.
        glob_pattern: override the OS-scan glob, for tests.

    Returns:
        A ``ResolvedApp`` whose ``binary`` attribute exists on disk, or
        ``None`` when the app is not installed locally. Callers should treat
        ``None`` as "cannot launch here" regardless of SG metadata.
    """
    app_norm = app.strip().lower()

    # Layer 1 — discovery. OS scan is the source of truth: if nothing lives
    # under /Applications, there is nothing to launch.
    os_hits = _os_scan(app_norm, glob_pattern)
    if not os_hits:
        return None

    binary, version = os_hits[0]
    warnings: list[str] = []
    layers: list[str] = ["os_scan"]

    if len(os_hits) > 1:
        extras = ", ".join(str(h[0]) for h in os_hits[1:])
        warnings.append(
            f"multiple {app_norm} installs found ({len(os_hits)}); picked "
            f"newest: {binary} (other: {extras})"
        )

    result = ResolvedApp(
        app=app_norm,
        binary=binary,
        version=version,
        launch_method="open",
        warnings=warnings,
    )

    # Layers 2 and 3 are enrichment — any failure is captured as a warning
    # and never blocks the result.
    if sg_find is not None and project_id is not None:
        try:
            config_root, tank_cmd = _toolkit_enrichment(project_id, sg_find)
            if tank_cmd is not None:
                result.pipeline_config_path = config_root
                result.tank_command = tank_cmd
                result.launch_method = "tank"
                layers.append("toolkit_yaml")
        except Exception as exc:
            warnings.append(f"toolkit enrichment failed: {exc}")

        engine = _APP_TO_ENGINE.get(app_norm)
        if not engine:
            warnings.append(f"no engine mapping for app '{app_norm}'")
        else:
            try:
                sw = _sg_software_enrichment(engine, project_id, sg_find)
                if sw is not None:
                    result.engine = engine
                    layers.append("sg_software")
                    if not sw.get("mac_path") and not sw.get("version_names"):
                        warnings.append(
                            f"SG Software entity for {engine} is an empty "
                            f"stub (no mac_path, no versions)"
                        )
                    # The version selected in FPT (SG Software.version_names)
                    # is AUTHORITATIVE over the newest-local default: held-back
                    # versions are intentional in this pipeline, and machines
                    # carry several parallel releases for testing. Only when
                    # FPT selects nothing (empty stub) does newest-local apply.
                    preferred = _pick_fpt_version(
                        sw.get("version_names"), os_hits
                    )
                    if preferred is not None:
                        if preferred[0] != result.binary:
                            result.binary, result.version = preferred
                            layers.append("sg_version_pref")
                    elif sw.get("version_names"):
                        warnings.append(
                            f"FPT-selected {app_norm} version(s) "
                            f"'{sw['version_names']}' not installed locally; "
                            f"falling back to newest local "
                            f"({result.version}). Install the FPT-selected "
                            f"version or update the SG Software entity."
                        )
            except Exception as exc:
                warnings.append(f"sg software enrichment failed: {exc}")

    result.source_layers = layers
    return result
