"""paths.py — write-path containment guard for the file-writing tools.

Two MCP tools write attacker-influenceable bytes to attacker-influenceable
filesystem locations:

* ``tk_publish`` copies a source file to a publish destination
  (``shutil.copy2``); in Mode 2 the destination is taken verbatim from the
  tool input.
* ``sg_download`` downloads a ShotGrid attachment / thumbnail to a download
  path taken verbatim from the tool input.

Because the tool arguments are produced by an LLM (which can be steered by
hostile production data — a ShotGrid Note, an Asset description, a filename),
a crafted call such as ``download_path="/Users/.../.ssh/authorized_keys"`` or
``publish_path="/etc/cron.d/x"`` would write outside any pipeline tree,
silently creating intermediate directories: an arbitrary-file-write primitive
scoped to the MCP server process.

This module anchors every write destination on a legitimate project root.
Containment is computed on the *real* path: :func:`os.path.realpath` collapses
``..``, resolves symlinks and absolutises the path **without requiring it to
exist** (exactly the publish/download case where the leaf does not exist yet).
That catches three escapes the detection-only ``safety.py`` regex cannot:

* dot-dot traversal (``root/../../etc`` → ``/etc``),
* absolute escapes with no ``..`` (``/etc/passwd``),
* symlink escapes (a symlink inside the root pointing out, or a symlinked
  root) — both sides are run through ``realpath``.

Enforcement policy
------------------
Decided in ``proposals/fpt-path-containment-allowlist.md`` (Option A) with a
**WARN-by-default** override:

* **Default (WARN)** — an out-of-root destination logs a structured warning
  via the existing ``fpt_mcp`` logger and is *allowed*. No current workflow
  breaks; the existing Mode-2 publish tests stay green without modification.
* **Strict (``FPT_MCP_STRICT_PATHS=1``)** — an out-of-root destination is
  *refused*: the caller returns the repo-standard ``{"error": ...}`` JSON
  envelope and does **not** write.

Allowed roots = the discovered ``TkConfig.project_root`` (when one is
resolvable) UNION the ``os.pathsep``-separated ``FPT_MCP_ALLOWED_WRITE_ROOTS``
env list.

Notes
-----
* ``safety.py`` keeps its path-traversal regex as a *detection-only*
  pre-filter; this module is the real containment boundary.
* The copy *source* (``tk_publish.local_path``) is intentionally NOT
  contained in this iteration — tracked as a follow-up in the proposal
  (reading an arbitrary file is a lesser, separate issue).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union

from fpt_mcp.logging_config import get_logger

#: ``os.pathsep``-separated list of absolute roots writes are allowed under.
_ALLOWED_ROOTS_ENV = "FPT_MCP_ALLOWED_WRITE_ROOTS"
#: When set to ``"1"`` an out-of-root destination is refused instead of warned.
_STRICT_ENV = "FPT_MCP_STRICT_PATHS"

_logger = get_logger(__name__)


class PathContainmentError(Exception):
    """Raised when a write destination resolves outside every allowed root."""

    def __init__(self, attempted: Path, roots: list[Path]):
        self.attempted = attempted
        self.roots = roots
        roots_str = os.pathsep.join(str(r) for r in roots) or "(none configured)"
        super().__init__(
            f"Refused: destination '{attempted}' is outside the allowed "
            f"project root(s): {roots_str}. Set {_ALLOWED_ROOTS_ENV} to a "
            f"directory tree where writes are permitted, or publish via a "
            f"PipelineConfiguration."
        )


def is_strict_paths() -> bool:
    """Return ``True`` when hard refusal is enabled (``FPT_MCP_STRICT_PATHS=1``).

    Read at call time so a test (or operator) can toggle the policy without
    re-importing the module.
    """
    return os.environ.get(_STRICT_ENV, "").strip() == "1"


def resolve_allowed_roots(
    project_root: Optional[Union[Path, str]] = None,
) -> list[Path]:
    """Build the allowed-roots set: discovered ``project_root`` ∪ env allowlist.

    Args:
        project_root: The discovered ``TkConfig.project_root`` when one is
            resolvable, else ``None``. ``sg_download`` has no pipeline config
            in scope, so it passes ``None`` and relies on the env allowlist.

    Returns:
        A de-duplicated, order-preserving list of real (``os.path.realpath``)
        roots. May be empty when neither a project root nor the env var is
        configured — in that case every destination is "outside" and the
        WARN/STRICT policy in :func:`enforce_write_containment` applies.
    """
    roots: list[Path] = []
    if project_root is not None:
        roots.append(Path(os.path.realpath(str(project_root))))

    env = os.environ.get(_ALLOWED_ROOTS_ENV, "").strip()
    if env:
        for entry in env.split(os.pathsep):
            entry = entry.strip()
            if entry:
                roots.append(Path(os.path.realpath(entry)))

    # De-duplicate while preserving order (a discovered root may also appear
    # in the env list).
    seen: set[Path] = set()
    unique: list[Path] = []
    for root in roots:
        if root not in seen:
            seen.add(root)
            unique.append(root)
    return unique


def ensure_within_roots(candidate: Union[str, Path], roots: list[Path]) -> Path:
    """Return the realpath of *candidate* if it lies inside one of *roots*.

    Both the candidate and each root are normalised with
    :func:`os.path.realpath` so the comparison is between real on-disk
    locations (``..`` collapsed, symlinks resolved, absolutised). The candidate
    need not exist — ``realpath`` resolves existing components and leaves the
    non-existent leaf literal, which is exactly a publish/download target.

    Args:
        candidate: The proposed write destination.
        roots: Allowed roots, typically from :func:`resolve_allowed_roots`.

    Returns:
        The real, absolute ``Path`` of *candidate* when contained.

    Raises:
        PathContainmentError: If the real candidate is neither equal to, nor
            relative to, any real root (including when *roots* is empty).
    """
    real = Path(os.path.realpath(str(candidate)))
    for root in roots:
        real_root = Path(os.path.realpath(str(root)))
        if real == real_root or real.is_relative_to(real_root):
            return real
    raise PathContainmentError(
        real, [Path(os.path.realpath(str(r))) for r in roots]
    )


def enforce_write_containment(
    candidate: Union[str, Path],
    roots: list[Path],
    *,
    tool_name: str,
) -> Optional[str]:
    """Apply the WARN/STRICT containment policy to a write destination.

    This is the single chokepoint both file-writing tools call. It wraps
    :func:`ensure_within_roots` with the policy decided in the proposal.

    Args:
        candidate: The proposed write destination.
        roots: Allowed roots from :func:`resolve_allowed_roots`.
        tool_name: Originating tool (``"tk_publish"`` / ``"sg_download"``),
            included in the structured log line.

    Returns:
        ``None`` when the write may proceed — either the destination is
        contained, *or* it is outside the roots but strict mode is off (WARN:
        a structured warning is logged and the write is allowed).

        An error message ``str`` when the write must be refused (destination
        outside the roots **and** ``FPT_MCP_STRICT_PATHS=1``). The caller
        serializes it into the repo-standard ``{"error": ...}`` envelope and
        skips the write.
    """
    try:
        ensure_within_roots(candidate, roots)
        return None
    except PathContainmentError as exc:
        roots_str = os.pathsep.join(str(r) for r in roots) or "(none configured)"
        if is_strict_paths():
            _logger.warning(
                "path containment BLOCK tool=%s dest=%s allowed_roots=%s",
                tool_name, exc.attempted, roots_str,
            )
            return str(exc)
        _logger.warning(
            "path containment WARN (allowed; set %s=1 to enforce) "
            "tool=%s dest=%s allowed_roots=%s",
            _STRICT_ENV, tool_name, exc.attempted, roots_str,
        )
        return None
