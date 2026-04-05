"""
test_toolkit_paths.py
=====================
Phase 3.2 — Toolkit path resolution against templates.yml.

7 test cases as defined in TESTING_PLAN.md Fase 3.2:
  1. test_tk_discover_config
  2. test_tk_resolve_path_asset
  3. test_tk_resolve_path_shot
  4. test_tk_next_version_empty
  5. test_tk_next_version_existing
  6. test_tk_templates_yml_parsing
  7. test_tk_fallback_no_config

All tests use unittest.mock + fixture templates.yml — no live ShotGrid
or real Toolkit installation required.
"""

import asyncio
import platform
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from fpt_mcp.tk_config import (
    TkConfig,
    TkConfigError,
    discover_config,
    discover_or_fallback,
    clear_cache,
    _build_from_config_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine synchronously for testing."""
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _clear_tk_cache():
    """Clear the TkConfig singleton cache before and after every test."""
    clear_cache()
    yield
    clear_cache()


# ---------------------------------------------------------------------------
# 1. test_tk_discover_config
# ---------------------------------------------------------------------------

class TestTkDiscoverConfig:
    """discover_config finds PipelineConfiguration and returns TkConfig."""

    def test_discovers_primary_config(
        self, tmp_path, templates_yml_path, mock_pipeline_config,
    ):
        """discover_config queries SG for PipelineConfiguration, reads local
        config files, and returns a TkConfig with the correct project_root.
        """
        # Build a fake config directory that _build_from_config_path expects
        config_dir = tmp_path / "mock_pipeline"
        core_dir = config_dir / "config" / "core"
        core_dir.mkdir(parents=True)

        # roots.yml — points project root at tmp_path/project
        project_root = tmp_path / "project"
        project_root.mkdir()

        platform_key = {
            "Darwin": "mac_path",
            "Linux": "linux_path",
            "Windows": "windows_path",
        }.get(platform.system(), "mac_path")

        roots = {
            "primary": {
                "default": True,
                platform_key: str(project_root),
            }
        }
        (core_dir / "roots.yml").write_text(yaml.dump(roots))

        # Copy fixture templates.yml
        import shutil
        shutil.copy(templates_yml_path, core_dir / "templates.yml")

        # Patch the PipelineConfiguration to point at our fake config dir
        pc = dict(mock_pipeline_config)
        pc["mac_path"] = str(config_dir)
        pc["linux_path"] = str(config_dir)
        pc["windows_path"] = str(config_dir)

        # Mock sg_find_func: async function returning [pc]
        sg_find = AsyncMock(return_value=[pc])

        result = _run(discover_config(project_id=123, sg_find_func=sg_find))

        assert isinstance(result, TkConfig)
        assert result.project_root == project_root
        assert result.config_path == config_dir

        # Verify SG was queried with correct filters
        sg_find.assert_awaited_once()
        call_args = sg_find.call_args
        assert call_args[0][0] == "PipelineConfiguration"


# ---------------------------------------------------------------------------
# 2. test_tk_resolve_path_asset
# ---------------------------------------------------------------------------

class TestTkResolvePathAsset:
    """resolve_path generates correct asset path from template + fields."""

    def test_maya_asset_publish(self, tk_config):
        """maya_asset_publish template resolves to the expected path."""
        fields = {
            "sg_asset_type": "Character",
            "Asset": "hero_robot",
            "Step": "model",
            "name": "main",
            "version": 3,
            "maya_extension": "ma",
        }
        result = tk_config.resolve_path("maya_asset_publish", fields)

        expected = (
            tk_config.project_root
            / "assets" / "Character" / "hero_robot" / "model"
            / "publish" / "maya" / "main.v003.ma"
        )
        assert result == expected

    def test_asset_alembic_cache(self, tk_config):
        """asset_alembic_cache template resolves correctly."""
        fields = {
            "sg_asset_type": "Prop",
            "Asset": "prop_sword",
            "Step": "model",
            "name": "main",
            "version": 12,
        }
        result = tk_config.resolve_path("asset_alembic_cache", fields)

        expected = (
            tk_config.project_root
            / "assets" / "Prop" / "prop_sword" / "model"
            / "publish" / "caches" / "main.v012.abc"
        )
        assert result == expected

    def test_unresolved_keys_raises(self, tk_config):
        """Missing fields raise TkConfigError with unresolved key names."""
        fields = {
            "sg_asset_type": "Character",
            # Missing: Asset, Step, name, version, maya_extension
        }
        with pytest.raises(TkConfigError, match="Unresolved template keys"):
            tk_config.resolve_path("maya_asset_publish", fields)

    def test_unknown_template_raises(self, tk_config):
        """Non-existent template name raises TkConfigError."""
        with pytest.raises(TkConfigError, match="not found"):
            tk_config.resolve_path("nonexistent_template", {})


# ---------------------------------------------------------------------------
# 3. test_tk_resolve_path_shot
# ---------------------------------------------------------------------------

class TestTkResolvePathShot:
    """resolve_path generates correct shot path from template + fields."""

    def test_maya_shot_work(self, tk_config):
        """maya_shot_work template resolves to the expected path."""
        fields = {
            "Sequence": "SEQ01",
            "Shot": "SH010",
            "Step": "anim",
            "name": "blocking",
            "version": 1,
            "maya_extension": "ma",
        }
        result = tk_config.resolve_path("maya_shot_work", fields)

        expected = (
            tk_config.project_root
            / "sequences" / "SEQ01" / "SH010" / "anim"
            / "work" / "maya" / "blocking.v001.ma"
        )
        assert result == expected

    def test_nuke_shot_publish(self, tk_config):
        """nuke_shot_publish template resolves correctly."""
        fields = {
            "Sequence": "SEQ01",
            "Shot": "SH020",
            "Step": "comp",
            "name": "main",
            "version": 7,
        }
        result = tk_config.resolve_path("nuke_shot_publish", fields)

        expected = (
            tk_config.project_root
            / "sequences" / "SEQ01" / "SH020" / "comp"
            / "publish" / "nuke" / "main.v007.nk"
        )
        assert result == expected

    def test_flame_shot_batch_no_alias(self, tk_config):
        """flame_shot_batch uses inline path (no @alias), resolves correctly."""
        fields = {
            "Sequence": "SEQ01",
            "Shot": "SH010",
            "version": 2,
        }
        result = tk_config.resolve_path("flame_shot_batch", fields)

        expected = (
            tk_config.project_root
            / "sequences" / "SEQ01" / "SH010"
            / "finishing" / "batch" / "SH010.v002.batch"
        )
        assert result == expected


# ---------------------------------------------------------------------------
# 4. test_tk_next_version_empty
# ---------------------------------------------------------------------------

class TestTkNextVersionEmpty:
    """next_version returns 1 when no versions exist on disk."""

    def test_no_existing_versions(self, tk_config):
        """When the parent directory does not exist, next_version returns 1."""
        fields = {
            "sg_asset_type": "Character",
            "Asset": "hero_robot",
            "Step": "model",
            "name": "main",
            "maya_extension": "ma",
        }
        result = tk_config.next_version("maya_asset_publish", fields)
        assert result == 1

    def test_empty_directory(self, tk_config):
        """When the parent directory exists but is empty, next_version returns 1."""
        fields = {
            "sg_asset_type": "Character",
            "Asset": "hero_robot",
            "Step": "model",
            "name": "main",
            "maya_extension": "ma",
        }
        # Create the parent directory but leave it empty
        parent = (
            tk_config.project_root
            / "assets" / "Character" / "hero_robot" / "model"
            / "publish" / "maya"
        )
        parent.mkdir(parents=True)

        result = tk_config.next_version("maya_asset_publish", fields)
        assert result == 1


# ---------------------------------------------------------------------------
# 5. test_tk_next_version_existing
# ---------------------------------------------------------------------------

class TestTkNextVersionExisting:
    """next_version scans existing version files and returns max + 1."""

    def test_two_existing_versions(self, tk_config):
        """With v001 and v002 files on disk, next_version returns 3."""
        fields = {
            "sg_asset_type": "Character",
            "Asset": "hero_robot",
            "Step": "model",
            "name": "main",
            "maya_extension": "ma",
        }
        parent = (
            tk_config.project_root
            / "assets" / "Character" / "hero_robot" / "model"
            / "publish" / "maya"
        )
        parent.mkdir(parents=True)

        # Create version files
        (parent / "main.v001.ma").touch()
        (parent / "main.v002.ma").touch()

        result = tk_config.next_version("maya_asset_publish", fields)
        assert result == 3

    def test_non_contiguous_versions(self, tk_config):
        """With v001 and v005 (gap), next_version returns 6."""
        fields = {
            "sg_asset_type": "Prop",
            "Asset": "prop_sword",
            "Step": "model",
            "name": "main",
            "maya_extension": "ma",
        }
        parent = (
            tk_config.project_root
            / "assets" / "Prop" / "prop_sword" / "model"
            / "publish" / "maya"
        )
        parent.mkdir(parents=True)

        (parent / "main.v001.ma").touch()
        (parent / "main.v005.ma").touch()

        result = tk_config.next_version("maya_asset_publish", fields)
        assert result == 6

    def test_ignores_unrelated_files(self, tk_config):
        """Files without version pattern are ignored."""
        fields = {
            "sg_asset_type": "Character",
            "Asset": "hero_robot",
            "Step": "model",
            "name": "main",
            "maya_extension": "ma",
        }
        parent = (
            tk_config.project_root
            / "assets" / "Character" / "hero_robot" / "model"
            / "publish" / "maya"
        )
        parent.mkdir(parents=True)

        (parent / "main.v003.ma").touch()
        (parent / "readme.txt").touch()
        (parent / "backup.ma").touch()

        result = tk_config.next_version("maya_asset_publish", fields)
        assert result == 4


# ---------------------------------------------------------------------------
# 6. test_tk_templates_yml_parsing
# ---------------------------------------------------------------------------

class TestTkTemplatesYmlParsing:
    """TkConfig correctly parses the fixture templates.yml: aliases,
    template definitions, and key metadata.
    """

    def test_aliases_extracted(self, tk_config):
        """shot_root, asset_root, sequence_root aliases are parsed."""
        templates = tk_config.list_templates()

        # All alias-using templates should be expanded (no '@' in values)
        for name, resolved in templates.items():
            assert "@" not in resolved, (
                f"Template '{name}' still contains unresolved alias: {resolved}"
            )

    def test_shot_root_alias_expansion(self, tk_config):
        """@shot_root expands to 'sequences/{Sequence}/{Shot}/{Step}'."""
        template = tk_config.get_template("maya_shot_work")
        assert template is not None
        assert template.startswith("sequences/{Sequence}/{Shot}/{Step}")

    def test_asset_root_alias_expansion(self, tk_config):
        """@asset_root expands to 'assets/{sg_asset_type}/{Asset}/{Step}'."""
        template = tk_config.get_template("maya_asset_work")
        assert template is not None
        assert template.startswith("assets/{sg_asset_type}/{Asset}/{Step}")

    def test_all_expected_templates_present(self, tk_config):
        """All templates from the fixture are loaded."""
        templates = tk_config.list_templates()

        expected_names = [
            "maya_shot_work", "maya_shot_publish",
            "maya_asset_work", "maya_asset_publish",
            "nuke_shot_work", "nuke_shot_publish",
            "nuke_asset_work", "nuke_asset_publish",
            "asset_alembic_cache",
            "flame_shot_batch", "flame_shot_render_exr",
            "houdini_shot_work",
            "hiero_project_work", "hiero_project_publish",
        ]
        for name in expected_names:
            assert name in templates, f"Template '{name}' not found in parsed config"

    def test_template_count(self, tk_config):
        """Fixture has the expected number of templates (not aliases)."""
        templates = tk_config.list_templates()
        # Fixture defines 18 templates (excluding the 3 aliases)
        assert len(templates) == 18

    def test_list_templates_filter(self, tk_config):
        """list_templates(pattern) filters by name substring."""
        maya_templates = tk_config.list_templates("maya")
        assert all("maya" in name for name in maya_templates)
        assert len(maya_templates) >= 4  # shot work/publish + asset work/publish

    def test_keys_raw_loaded(self, tk_config):
        """keys section from templates.yml is available via _keys_raw."""
        assert "version" in tk_config._keys_raw
        assert tk_config._keys_raw["version"]["format_spec"] == "03"
        assert "maya_extension" in tk_config._keys_raw


# ---------------------------------------------------------------------------
# 7. test_tk_fallback_no_config
# ---------------------------------------------------------------------------

class TestTkFallbackNoConfig:
    """discover_or_fallback returns None when no PipelineConfiguration exists."""

    def test_returns_none_when_no_config(self):
        """When sg_find returns empty list, discover_or_fallback returns None."""
        sg_find = AsyncMock(return_value=[])
        result = _run(discover_or_fallback(project_id=999, sg_find_func=sg_find))
        assert result is None

    def test_sg_queried_for_pipeline_config(self):
        """discover_or_fallback queries for 'Primary' PipelineConfiguration."""
        sg_find = AsyncMock(return_value=[])
        _run(discover_or_fallback(project_id=999, sg_find_func=sg_find))

        sg_find.assert_awaited_once()
        call_args = sg_find.call_args
        assert call_args[0][0] == "PipelineConfiguration"
        # Second arg is filters — should contain project filter
        filters = call_args[0][1]
        assert any("project" in str(f) for f in filters)

    def test_custom_pipeline_config_name(self):
        """discover_or_fallback passes custom pipeline_config_name to SG query."""
        sg_find = AsyncMock(return_value=[])
        _run(discover_or_fallback(
            project_id=999,
            sg_find_func=sg_find,
            pipeline_config_name="Secondary",
        ))

        call_args = sg_find.call_args
        filters = call_args[0][1]
        assert any("Secondary" in str(f) for f in filters)
