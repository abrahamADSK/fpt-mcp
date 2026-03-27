"""Toolkit-compatible path resolver (hybrid mode).

Generates file-system paths that follow tk-config-default2 conventions
so that publishes created via the ShotGrid API are compatible with
Toolkit loaders in Maya and Flame.

Templates are configured via environment variables in .env.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Templates from environment
# ---------------------------------------------------------------------------

TOOLKIT_ROOT = os.getenv("TOOLKIT_ROOT", "/mnt/projects")
TOOLKIT_ASSET_PATH = os.getenv(
    "TOOLKIT_ASSET_PATH",
    "{project}/assets/{asset_type}/{asset}/{step}",
)
TOOLKIT_SHOT_PATH = os.getenv(
    "TOOLKIT_SHOT_PATH",
    "{project}/sequences/{sequence}/{shot}/{step}",
)
TOOLKIT_PUBLISH_PATH = os.getenv(
    "TOOLKIT_PUBLISH_PATH",
    "{project}/publishes/{entity_type}/{entity}/{step}/{name}/v{version}",
)


def _sanitize(name: str) -> str:
    """Convert a display name to a filesystem-safe string."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def resolve_asset_path(
    project: str,
    asset_type: str,
    asset: str,
    step: str = "model",
) -> Path:
    """Return the working directory for an asset step.

    Example: /mnt/projects/MyProject/assets/Character/MRBone1/model
    """
    rel = TOOLKIT_ASSET_PATH.format(
        project=_sanitize(project),
        asset_type=_sanitize(asset_type),
        asset=_sanitize(asset),
        step=_sanitize(step),
    )
    return Path(TOOLKIT_ROOT) / rel


def resolve_shot_path(
    project: str,
    sequence: str,
    shot: str,
    step: str = "comp",
) -> Path:
    """Return the working directory for a shot step.

    Example: /mnt/projects/MyProject/sequences/SEQ010/SHOT010/comp
    """
    rel = TOOLKIT_SHOT_PATH.format(
        project=_sanitize(project),
        sequence=_sanitize(sequence),
        shot=_sanitize(shot),
        step=_sanitize(step),
    )
    return Path(TOOLKIT_ROOT) / rel


def resolve_publish_path(
    project: str,
    entity_type: str,
    entity: str,
    step: str,
    name: str,
    version: int,
) -> Path:
    """Return the publish directory for a specific version.

    Example: /mnt/projects/MyProject/publishes/Asset/MRBone1/model/turntable/v003
    """
    rel = TOOLKIT_PUBLISH_PATH.format(
        project=_sanitize(project),
        entity_type=_sanitize(entity_type),
        entity=_sanitize(entity),
        step=_sanitize(step),
        name=_sanitize(name),
        version=str(version).zfill(3),
    )
    return Path(TOOLKIT_ROOT) / rel


def next_version_number(publish_base: Path) -> int:
    """Scan existing version folders and return the next integer.

    If publish_base/v001 and publish_base/v002 exist → returns 3.
    If no versions exist → returns 1.
    """
    if not publish_base.exists():
        return 1

    max_ver = 0
    for child in publish_base.iterdir():
        if child.is_dir() and child.name.startswith("v"):
            try:
                ver = int(child.name[1:])
                max_ver = max(max_ver, ver)
            except ValueError:
                continue
    return max_ver + 1
