"""Toolkit config discovery — dynamic PipelineConfiguration resolution.

Reads the real project configuration (roots.yml, templates.yml) directly
from the PipelineConfiguration registered in ShotGrid. Supports:
  - Local configs (mac_path / linux_path / windows_path)
  - Distributed configs (descriptor → bundle cache: dev, app_store, git)

Does not modify the user's config. Does not require sgtk bootstrap.
If the project has no PipelineConfiguration, discover_or_fallback() returns
None and tk_publish requests an explicit path from the user.
"""

from __future__ import annotations

import hashlib
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
    """Error during Toolkit config resolution."""


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


def _get_bundle_cache_root() -> Path:
    """Return the Toolkit bundle cache root for the current platform.

    Convention:
      - macOS:   ~/Library/Caches/Shotgun/bundle_cache/
      - Linux:   ~/.shotgun/bundle_cache/
      - Windows: %APPDATA%/Shotgun/bundle_cache/
    """
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Caches" / "Shotgun" / "bundle_cache"
    elif system == "Linux":
        return Path.home() / ".shotgun" / "bundle_cache"
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(appdata) / "Shotgun" / "bundle_cache"
    else:
        raise TkConfigError(f"Unsupported platform for bundle cache: {system}")


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
        if not isinstance(descriptor, dict):
            raise TkConfigError(
                f"Invalid descriptor format (expected dict, got "
                f"{type(descriptor).__name__}): {descriptor}"
            )

        desc_type = descriptor.get("type", "unknown")

        if desc_type == "dev":
            dev_path = descriptor.get("path")
            if dev_path:
                config_path = Path(dev_path)
                if config_path.exists():
                    return _build_from_config_path(config_path, project_id)
            raise TkConfigError(
                f"Dev descriptor path does not exist: {descriptor.get('path')}"
            )

        elif desc_type == "app_store":
            name = descriptor.get("name")
            version = descriptor.get("version")
            if not name or not version:
                raise TkConfigError(
                    f"app_store descriptor missing 'name' or 'version': {descriptor}"
                )
            cache_root = _get_bundle_cache_root()
            config_path = cache_root / "app_store" / name / version
            if not config_path.exists():
                raise TkConfigError(
                    f"Cached app_store config not found at: {config_path}\n"
                    f"Expected descriptor: {descriptor}\n"
                    f"Tip: open ShotGrid Desktop and let it download/cache "
                    f"the configuration, then retry."
                )
            return _build_from_config_path(config_path, project_id)

        elif desc_type == "git":
            repo_path = descriptor.get("path", "")
            version = descriptor.get("version")
            if not repo_path or not version:
                raise TkConfigError(
                    f"git descriptor missing 'path' or 'version': {descriptor}"
                )
            # ShotGrid convention: strip trailing .git, MD5-hash, truncate to 7 hex chars.
            normalized = repo_path.rstrip("/")
            if normalized.endswith(".git"):
                normalized = normalized[:-4]
            path_hash = hashlib.md5(normalized.encode("utf-8")).hexdigest()[:7]
            cache_root = _get_bundle_cache_root()
            config_path = cache_root / "git" / path_hash / version
            if not config_path.exists():
                raise TkConfigError(
                    f"Cached git config not found at: {config_path}\n"
                    f"Expected descriptor: {descriptor}\n"
                    f"(repo hash '{path_hash}' derived from '{normalized}')\n"
                    f"Tip: open ShotGrid Desktop and let it download/cache "
                    f"the configuration, then retry."
                )
            return _build_from_config_path(config_path, project_id)

        else:
            raise TkConfigError(
                f"Unsupported descriptor type '{desc_type}': {descriptor}\n"
                f"Supported types: dev, app_store, git."
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
) -> Optional[TkConfig]:
    """Discover the Toolkit configuration for a project.

    This is the recommended entry point. Returns the resolved TkConfig
    if the project has a PipelineConfiguration, or None if it doesn't.

    When None is returned, the caller (tk_publish) should ask the user
    for an explicit publish path instead of guessing a directory structure.

    Args:
        project_id: ShotGrid project ID.
        sg_find_func: Async function to query ShotGrid.
        pipeline_config_name: PipelineConfiguration name (default: "Primary").

    Returns:
        TkConfig if the project has a discoverable pipeline config, None otherwise.
    """
    # Check cache
    if project_id in _config_cache:
        return _config_cache[project_id]

    try:
        return await discover_config(project_id, sg_find_func, pipeline_config_name)
    except TkConfigError:
        return None


def clear_cache() -> None:
    """Clear the config cache (useful after config changes)."""
    _config_cache.clear()
