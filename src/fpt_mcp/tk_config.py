"""Toolkit config discovery — descubrimiento dinámico de PipelineConfiguration.

Lee la configuración real del proyecto (roots.yml, templates.yml) directamente
desde la PipelineConfiguration registrada en ShotGrid. Compatible con:
  - Configs locales (mac_path / linux_path / windows_path)
  - Distributed configs (descriptor → bundle cache) — TODO fase 2

Para tipos de fichero que no existen en tk-config-default2 (GLB, USD, etc.),
genera templates derivados siguiendo la convención existente.

No modifica la config del usuario. No requiere sgtk bootstrap.
"""

from __future__ import annotations

import os
import platform
import re
from pathlib import Path
from typing import Any, Optional

import yaml

# ---------------------------------------------------------------------------
# Cache (singleton per project)
# ---------------------------------------------------------------------------

_config_cache: dict[int, "TkConfig"] = {}


class TkConfigError(Exception):
    """Error en la resolución de Toolkit config."""


# ---------------------------------------------------------------------------
# Template key definitions — from tk-config-default2 defaults
# ---------------------------------------------------------------------------

# Keys with format specs that affect path rendering
KEY_FORMATS = {
    "version": "{:03d}",       # v001, v002...
    "version_four": "{:04d}",
    "SEQ": "{:04d}",
    "flame.frame": "{:08d}",
    "vred.frame": "{:05d}",
    "iteration": "{}",
    "width": "{}",
    "height": "{}",
}


# ---------------------------------------------------------------------------
# Derived templates for types NOT in tk-config-default2
# These follow the same convention: @{root}/publish/{tool}/{name}.v{version}.{ext}
# ---------------------------------------------------------------------------

DERIVED_TEMPLATES = {
    # Asset publishes — Vision3D pipeline
    "usd_asset_publish": "@asset_root/publish/usd/{name}.v{version}.usd",
    "fbx_asset_publish": "@asset_root/publish/fbx/{name}.v{version}.fbx",
    "texture_asset_publish": "@asset_root/publish/textures/{name}.v{version}.png",
    "review_asset_mov": "@asset_root/review/{Asset}_{name}_v{version}.mov",

    # Shot publishes
    "usd_shot_publish": "@shot_root/publish/usd/{name}.v{version}.usd",
    "fbx_shot_publish": "@shot_root/publish/fbx/{name}.v{version}.fbx",
    "camera_shot_fbx_publish": "@shot_root/publish/camera/{name}.v{version}.fbx",
    "exr_shot_render": "@shot_root/publish/renders/{name}/v{version}/{name}.v{version}.{SEQ}.exr",
    "review_shot_mov": "@shot_root/review/{Shot}_{name}_v{version}.mov",
}

# Map publish_type → template name for quick lookup
PUBLISH_TYPE_MAP = {
    # Asset types
    "Maya Scene":   {"asset": "maya_asset_publish", "shot": "maya_shot_publish"},
    "USD Scene":    {"asset": "usd_asset_publish", "shot": "usd_shot_publish"},
    "FBX Model":    {"asset": "fbx_asset_publish", "shot": "fbx_shot_publish"},
    "Texture":      {"asset": "texture_asset_publish", "shot": None},
    "Alembic Cache": {"asset": "asset_alembic_cache", "shot": None},
    # Shot types
    "Camera FBX":   {"asset": None, "shot": "camera_shot_fbx_publish"},
    "EXR Render":   {"asset": None, "shot": "exr_shot_render"},
    # Review (Version, not PublishedFile)
    "Review MOV":   {"asset": "review_asset_mov", "shot": "review_shot_mov"},
}


# ---------------------------------------------------------------------------
# TkConfig class — represents a project's resolved Toolkit configuration
# ---------------------------------------------------------------------------

class TkConfig:
    """Resolved Toolkit configuration for a specific project.

    Lazily loads and caches the config from the PipelineConfiguration path.
    """

    def __init__(
        self,
        project_root: Path,
        config_path: Path,
        templates_raw: dict[str, Any],
        keys_raw: dict[str, Any],
    ):
        self.project_root = project_root
        self.config_path = config_path
        self._templates_raw = templates_raw
        self._keys_raw = keys_raw

        # Parse path aliases (shot_root, asset_root, etc.)
        self._aliases: dict[str, str] = {}
        self._templates: dict[str, str] = {}
        self._parse_templates()

    def _parse_templates(self) -> None:
        """Parse templates.yml paths section into resolved template strings."""
        paths = self._templates_raw.get("paths", {})

        # First pass: extract aliases (strings without 'definition' key)
        for name, value in paths.items():
            if isinstance(value, str):
                self._aliases[name] = value
            elif isinstance(value, dict) and "definition" in value:
                self._templates[name] = value["definition"]

        # Add derived templates for types not in tk-config-default2
        for name, template in DERIVED_TEMPLATES.items():
            if name not in self._templates:
                self._templates[name] = template

    def resolve_alias(self, template: str) -> str:
        """Expand @alias references in a template string.

        Example: '@asset_root/publish/maya/...'
              → 'assets/{sg_asset_type}/{Asset}/{Step}/publish/maya/...'
        """
        for alias_name, alias_value in self._aliases.items():
            template = template.replace(f"@{alias_name}", alias_value)
        return template

    def get_template(self, template_name: str) -> Optional[str]:
        """Get a resolved template string by name, with aliases expanded."""
        raw = self._templates.get(template_name)
        if raw is None:
            return None
        return self.resolve_alias(raw)

    def list_templates(self, pattern: str = "") -> dict[str, str]:
        """List all templates, optionally filtered by pattern in name."""
        result = {}
        for name, raw in sorted(self._templates.items()):
            if pattern and pattern not in name:
                continue
            result[name] = self.resolve_alias(raw)
        return result

    def resolve_path(
        self,
        template_name: str,
        fields: dict[str, Any],
    ) -> Path:
        """Resolve a template to a full filesystem path.

        Args:
            template_name: Name of the template (e.g. 'maya_asset_publish')
            fields: Dict of template keys and their values. Example:
                {
                    "sg_asset_type": "Character",
                    "Asset": "hero_robot",
                    "Step": "model",
                    "name": "main",
                    "version": 3,
                    "maya_extension": "ma",
                }

        Returns:
            Full resolved path including project root.
        """
        template = self.get_template(template_name)
        if template is None:
            raise TkConfigError(
                f"Template '{template_name}' not found. "
                f"Available: {', '.join(sorted(self._templates.keys()))}"
            )

        # Replace Toolkit-style {key} tokens with field values
        resolved = template
        for key, value in fields.items():
            if key in KEY_FORMATS and isinstance(value, int):
                formatted = KEY_FORMATS[key].format(value)
            elif isinstance(value, int):
                formatted = str(value)
            else:
                formatted = _sanitize(str(value))
            resolved = resolved.replace(f"{{{key}}}", formatted)

        # Check for unresolved keys
        unresolved = re.findall(r"\{(\w[\w.]*)\}", resolved)
        if unresolved:
            raise TkConfigError(
                f"Unresolved template keys: {unresolved}. "
                f"Template '{template_name}' requires these fields."
            )

        return self.project_root / resolved

    def next_version(self, template_name: str, fields: dict[str, Any]) -> int:
        """Find the next available version number by scanning the filesystem.

        Resolves the template up to the version directory, then scans
        existing version files/folders.
        """
        # Try resolving with version=0 to get the parent pattern
        test_fields = {**fields, "version": 0}
        try:
            path_v0 = self.resolve_path(template_name, test_fields)
        except TkConfigError:
            return 1

        # The parent directory contains versioned files
        parent = path_v0.parent
        if not parent.exists():
            return 1

        # Extract version pattern from the filename
        filename = path_v0.name  # e.g. "main.v000.ma"
        # Find existing versions by scanning files matching the pattern
        max_ver = 0
        version_pattern = re.compile(r"\.v(\d{3,4})\.")

        for child in parent.iterdir():
            match = version_pattern.search(child.name)
            if match:
                try:
                    ver = int(match.group(1))
                    max_ver = max(max_ver, ver)
                except ValueError:
                    continue

        return max_ver + 1


# ---------------------------------------------------------------------------
# Discovery functions
# ---------------------------------------------------------------------------

def _sanitize(name: str) -> str:
    """Convert a display name to a filesystem-safe string."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name)


def _get_platform_path(entity: dict) -> Optional[str]:
    """Extract the config path for the current OS from a PipelineConfiguration."""
    system = platform.system()
    if system == "Darwin":
        return entity.get("mac_path")
    elif system == "Linux":
        return entity.get("linux_path")
    elif system == "Windows":
        return entity.get("windows_path")
    return None


def _read_yaml(path: Path) -> dict:
    """Read and parse a YAML file."""
    if not path.exists():
        raise TkConfigError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Default templates — tk-config-default2 conventions (fallback mode)
# ---------------------------------------------------------------------------

_DEFAULT_TEMPLATES_RAW = {
    "keys": {
        "version": {"type": "int", "format_spec": "03"},
        "maya_extension": {"type": "str", "default": "ma"},
    },
    "paths": {
        # Aliases
        "shot_root": "sequences/{Sequence}/{Shot}/{Step}",
        "asset_root": "assets/{sg_asset_type}/{Asset}/{Step}",
        "sequence_root": "sequences/{Sequence}",
        # Maya
        "maya_asset_publish": {"definition": "@asset_root/publish/maya/{name}.v{version}.{maya_extension}"},
        "maya_shot_publish": {"definition": "@shot_root/publish/maya/{name}.v{version}.{maya_extension}"},
        "maya_asset_work": {"definition": "@asset_root/work/maya/{name}.v{version}.{maya_extension}"},
        "maya_shot_work": {"definition": "@shot_root/work/maya/{name}.v{version}.{maya_extension}"},
        # Alembic
        "asset_alembic_cache": {"definition": "@asset_root/publish/caches/{name}.v{version}.abc"},
        # Nuke
        "nuke_asset_publish": {"definition": "@asset_root/publish/nuke/{name}.v{version}.nk"},
        "nuke_shot_publish": {"definition": "@shot_root/publish/nuke/{name}.v{version}.nk"},
    },
}


def _build_fallback_config(project_root: Path) -> TkConfig:
    """Create a TkConfig with tk-config-default2 default templates.

    Used when no PipelineConfiguration is found in ShotGrid (basic project
    without Advanced Setup). Paths follow standard conventions and are
    compatible with any tk-config-default2 based project.

    The project_root comes from PUBLISH_ROOT env var or a sensible default.
    """
    return TkConfig(
        project_root=project_root,
        config_path=Path("(fallback — no PipelineConfiguration)"),
        templates_raw=_DEFAULT_TEMPLATES_RAW,
        keys_raw=_DEFAULT_TEMPLATES_RAW.get("keys", {}),
    )


# ---------------------------------------------------------------------------
# Discovery functions
# ---------------------------------------------------------------------------

async def discover_config(
    project_id: int,
    sg_find_func,
    pipeline_config_name: str = "Primary",
) -> TkConfig:
    """Discover and load the Toolkit configuration for a project.

    Queries ShotGrid for the PipelineConfiguration, reads the local config
    (roots.yml, templates.yml), and builds a TkConfig with real templates.

    Args:
        project_id: ShotGrid project ID.
        sg_find_func: Async function to query ShotGrid (sg_find from client.py).
        pipeline_config_name: Name of the PipelineConfiguration (default: "Primary").

    Returns:
        TkConfig instance with resolved templates and project root.

    Raises:
        TkConfigError: If the config cannot be discovered or loaded.
    """
    # Check cache first
    if project_id in _config_cache:
        return _config_cache[project_id]

    # 1. Query PipelineConfiguration
    configs = await sg_find_func(
        "PipelineConfiguration",
        [
            ["project", "is", {"type": "Project", "id": project_id}],
            ["code", "is", pipeline_config_name],
        ],
        ["code", "mac_path", "linux_path", "windows_path", "descriptor"],
    )

    if not configs:
        raise TkConfigError(
            f"No PipelineConfiguration '{pipeline_config_name}' found "
            f"for project {project_id}."
        )

    pc = configs[0]

    # 2. Resolve config path
    descriptor = pc.get("descriptor")
    if descriptor:
        # Distributed config — resolve from Toolkit bundle cache.
        # The bundle cache location follows this convention:
        #   macOS:   ~/Library/Caches/Shotgun/<site>/bundle_cache/
        #   Linux:   ~/.shotgun/bundle_cache/
        #   Windows: %APPDATA%\Shotgun\bundle_cache\
        #
        # The descriptor dict contains the config source:
        #   {"type": "app_store", "name": "tk-config-default2", "version": "v1.2.3"}
        #   {"type": "git", "path": "https://github.com/user/tk-config-custom.git", "version": "v1.0.0"}
        #   {"type": "dev", "path": "/path/to/local/config"}
        #
        # For dev descriptors, the path points directly to the config.
        # For app_store/git, we need to find the cached bundle.
        #
        # TODO: Implement full descriptor resolution. For now, support 'dev' type
        # and fall back to default templates for others.
        if isinstance(descriptor, dict) and descriptor.get("type") == "dev":
            dev_path = descriptor.get("path")
            if dev_path:
                config_path = Path(dev_path)
                if config_path.exists():
                    return _build_from_config_path(config_path, project_id)

        raise TkConfigError(
            f"Distributed config (descriptor: {descriptor}) — "
            f"automatic resolution for '{descriptor.get('type', 'unknown')}' type "
            f"not yet implemented. Falling back to default templates.\n"
            f"Tip: use discover_or_fallback() to handle this gracefully."
        )

    config_path_str = _get_platform_path(pc)
    if not config_path_str:
        raise TkConfigError(
            f"PipelineConfiguration '{pipeline_config_name}' has no path "
            f"for {platform.system()}. Available paths: "
            f"mac={pc.get('mac_path')}, linux={pc.get('linux_path')}, "
            f"win={pc.get('windows_path')}"
        )

    config_path = Path(config_path_str)
    return _build_from_config_path(config_path, project_id)


def _build_from_config_path(config_path: Path, project_id: int) -> TkConfig:
    """Build a TkConfig from a local config directory path."""
    if not config_path.exists():
        raise TkConfigError(f"Config path does not exist: {config_path}")

    # Read roots.yml → project root
    roots_path = config_path / "config" / "core" / "roots.yml"
    roots = _read_yaml(roots_path)

    # Find primary (default) root
    primary_root = None
    for _root_name, root_data in roots.items():
        if isinstance(root_data, dict) and root_data.get("default", False):
            platform_key = {
                "Darwin": "mac_path",
                "Linux": "linux_path",
                "Windows": "windows_path",
            }.get(platform.system(), "mac_path")
            primary_root = root_data.get(platform_key)
            break

    if not primary_root:
        raise TkConfigError(
            f"No default storage root found in {roots_path}. "
            f"Contents: {roots}"
        )

    project_root = Path(primary_root)

    # Read templates.yml
    templates_path = config_path / "config" / "core" / "templates.yml"
    templates_raw = _read_yaml(templates_path)
    keys_raw = templates_raw.get("keys", {})

    # Build and cache TkConfig
    tk_config = TkConfig(
        project_root=project_root,
        config_path=config_path,
        templates_raw=templates_raw,
        keys_raw=keys_raw,
    )
    _config_cache[project_id] = tk_config
    return tk_config


async def discover_or_fallback(
    project_id: int,
    sg_find_func,
    pipeline_config_name: str = "Primary",
) -> TkConfig:
    """Discover the Toolkit config, falling back to defaults if not available.

    This is the recommended entry point. It tries, in order:

    1. **Full discovery**: Query PipelineConfiguration → read local config
       → real templates from the project's tk-config.

    2. **Fallback mode**: If no PipelineConfiguration exists, or if the config
       is distributed and not locally resolvable, uses tk-config-default2
       standard templates with PUBLISH_ROOT from the environment.

    The fallback produces paths compatible with any standard tk-config-default2
    project. PublishedFiles registered with these paths will be found by
    Toolkit loaders if the project later gets an Advanced Setup with the
    default config.

    Args:
        project_id: ShotGrid project ID.
        sg_find_func: Async function to query ShotGrid.
        pipeline_config_name: PipelineConfiguration name (default: "Primary").

    Returns:
        TkConfig — either from the real config or from fallback defaults.
    """
    # Check cache
    if project_id in _config_cache:
        return _config_cache[project_id]

    try:
        return await discover_config(project_id, sg_find_func, pipeline_config_name)
    except TkConfigError:
        pass

    # Fallback: use PUBLISH_ROOT from env, or derive from project name
    publish_root = os.getenv("PUBLISH_ROOT")
    if publish_root:
        project_root = Path(publish_root)
    else:
        # Try to get project name from SG for a sensible default path
        try:
            projects = await sg_find_func(
                "Project",
                [["id", "is", project_id]],
                ["name", "tank_name"],
            )
            if projects:
                tank_name = projects[0].get("tank_name") or projects[0].get("name", "project")
                # Use a standard location
                project_root = Path.home() / "ShotGrid" / _sanitize(tank_name)
            else:
                project_root = Path.home() / "ShotGrid" / f"project_{project_id}"
        except Exception:
            project_root = Path.home() / "ShotGrid" / f"project_{project_id}"

    tk_config = _build_fallback_config(project_root)
    _config_cache[project_id] = tk_config
    return tk_config


def clear_cache() -> None:
    """Clear the config cache (useful after config changes)."""
    _config_cache.clear()
