"""
test_tk_publish.py — Phase 3.5
===============================
Tests for the tk_publish workflow (server.py → tk_publish_tool).

Coverage:
  1. TestPublishMode1Full — Full Mode 1 publish: resolve → copy → create type → link task → register
  2. TestPublishMode2Explicit — Mode 2 publish with explicit path
  3. TestPublishAutoVersion — Auto-increment version from existing versions
  4. TestPublishFindOrCreateType — Creates PublishedFileType if not found
  5. TestPublishTaskLinking — Links PublishedFile to correct Task
  6. TestPublishFileCopy — Source file copied to publish path

All tests run offline (no ShotGrid connection required).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fpt_mcp.server import tk_publish_tool, TkPublishInput
from fpt_mcp.tk_config import TkConfig, TkConfigError


# ---------------------------------------------------------------------------
# Helper: run async tool in sync tests
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine in a new event loop."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Helper: build a standard TkPublishInput
# ---------------------------------------------------------------------------

def _make_input(**overrides) -> TkPublishInput:
    """Build a TkPublishInput with sensible defaults.

    Note: publish_type must match the Toolkit template naming convention.
    For Mode 1, the server infers a template name from publish_type + entity_type:
      publish_type="maya" + entity_type="Asset" → searches for "maya_asset_publish"
    So we use "maya" (not "Maya Scene") for Mode 1 tests.
    For Mode 2 (explicit path), publish_type can be anything (e.g. "Maya Scene")
    because no template resolution occurs.
    """
    defaults = {
        "entity_type": "Asset",
        "entity_id": 1001,
        "publish_type": "maya",
        "step": "model",
        "name": "main",
        "comment": None,
        "local_path": None,
        "publish_path": None,
        "version_number": None,
        "extension": "ma",
    }
    defaults.update(overrides)
    return TkPublishInput(**defaults)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def publish_tk_config(tk_config):
    """Return the tk_config from conftest.py — already has templates.yml parsed.

    The project_root is a tmp_path subdir, so file copy tests write safely.
    """
    return tk_config


@pytest.fixture
def mock_sg_find_one():
    """Build an AsyncMock for sg_find_one that dispatches by entity_type.

    Default behaviour:
      - PublishedFileType → returns {"type": "PublishedFileType", "id": 6001, "code": "Maya Scene"}
      - Asset  → returns SAMPLE_ASSETS[0] (hero_robot, Character)
      - Shot   → returns SAMPLE_SHOTS[0] (SH010)
      - Task   → returns SAMPLE_TASKS[0] (Model task linked to hero_robot)
      - else   → returns None

    Override per-test by replacing .side_effect.
    """
    from tests.conftest import SAMPLE_ASSETS, SAMPLE_SHOTS, SAMPLE_TASKS

    async def _dispatcher(entity_type: str, filters: list, fields: list[str]):
        if entity_type == "PublishedFileType":
            # Return a PFT matching the requested code from the filter
            code = "maya"
            for f in filters:
                if isinstance(f, list) and f[0] == "code" and f[1] == "is":
                    code = f[2]
            return {"type": "PublishedFileType", "id": 6001, "code": code}
        if entity_type == "Asset":
            return {
                "type": "Asset", "id": 1001, "code": "hero_robot",
                "sg_asset_type": "Character",
            }
        if entity_type == "Shot":
            return {
                "type": "Shot", "id": 2001, "code": "SH010",
                "sg_sequence": {"type": "Sequence", "id": 3001, "name": "SEQ01"},
            }
        if entity_type == "Task":
            return {
                "type": "Task", "id": 4001, "content": "Model",
            }
        return None

    mock = AsyncMock(side_effect=_dispatcher)
    return mock


@pytest.fixture
def mock_sg_create():
    """AsyncMock for sg_create — returns a dict with auto-id."""
    async def _create(entity_type: str, data: dict):
        return {"type": entity_type, "id": 9999, **data}

    return AsyncMock(side_effect=_create)


@pytest.fixture
def patch_publish_deps(publish_tk_config, mock_sg_find_one, mock_sg_create):
    """Patch all server.py dependencies for tk_publish_tool.

    Patches:
      - _get_tk_config → returns publish_tk_config
      - sg_find_one → mock_sg_find_one dispatcher
      - sg_create → mock_sg_create
      - PROJECT_ID → 123

    Yields (tk_config, sg_find_one_mock, sg_create_mock).
    """
    async def _get_config():
        return publish_tk_config

    with patch("fpt_mcp.server._get_tk_config", side_effect=_get_config), \
         patch("fpt_mcp.server.sg_find_one", mock_sg_find_one), \
         patch("fpt_mcp.server.sg_create", mock_sg_create), \
         patch("fpt_mcp.server.PROJECT_ID", 123):
        yield publish_tk_config, mock_sg_find_one, mock_sg_create


@pytest.fixture
def patch_publish_no_config(mock_sg_find_one, mock_sg_create):
    """Patch tk_publish_tool with _get_tk_config returning None (no PipelineConfig).

    Useful for Mode 2 tests.
    """
    async def _get_config():
        return None

    with patch("fpt_mcp.server._get_tk_config", side_effect=_get_config), \
         patch("fpt_mcp.server.sg_find_one", mock_sg_find_one), \
         patch("fpt_mcp.server.sg_create", mock_sg_create), \
         patch("fpt_mcp.server.PROJECT_ID", 123):
        yield mock_sg_find_one, mock_sg_create


# ===========================================================================
# 1. TestPublishMode1Full
# ===========================================================================

class TestPublishMode1Full:
    """Full Mode 1 publish: resolve → copy → create type → link task → register."""

    def test_mode1_creates_published_file(self, patch_publish_deps):
        """tk_publish creates a PublishedFile with correct fields in Mode 1."""
        tk_config, sg_find_one_mock, sg_create_mock = patch_publish_deps

        params = _make_input(
            entity_type="Asset",
            entity_id=1001,
            publish_type="maya",
            step="model",
            name="main",
            extension="ma",
        )
        result = json.loads(_run(tk_publish_tool(params)))

        # Should not be an error
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        # Should have created a PublishedFile
        assert result["id"] == 9999
        assert result["version_number"] >= 1
        assert "path" in result
        assert result["entity"]["code"] == "hero_robot"

    def test_mode1_path_uses_template(self, patch_publish_deps):
        """Mode 1 publish path is resolved from Toolkit templates."""
        tk_config, _, _ = patch_publish_deps

        params = _make_input(extension="ma")
        result = json.loads(_run(tk_publish_tool(params)))

        assert "error" not in result
        # Path should contain the project_root prefix
        assert str(tk_config.project_root) in result["path"]
        # Path should contain asset type structure from the template
        assert "Character" in result["path"] or "assets" in result["path"]

    def test_mode1_response_includes_template(self, patch_publish_deps):
        """Mode 1 response includes the template name and project_root."""
        _, _, _ = patch_publish_deps

        params = _make_input(extension="ma")
        result = json.loads(_run(tk_publish_tool(params)))

        assert "error" not in result
        assert "template" in result
        assert "project_root" in result

    def test_mode1_sets_project(self, patch_publish_deps):
        """Mode 1 publish sets the project field in the PublishedFile data."""
        _, _, sg_create_mock = patch_publish_deps

        params = _make_input(extension="ma")
        _run(tk_publish_tool(params))

        # sg_create should have been called for PublishedFile
        calls = [c for c in sg_create_mock.call_args_list if c[0][0] == "PublishedFile"]
        assert len(calls) == 1
        pf_data = calls[0][0][1]
        assert pf_data["project"] == {"type": "Project", "id": 123}

    def test_mode1_includes_comment(self, patch_publish_deps):
        """When comment is provided, it goes into the description field."""
        _, _, sg_create_mock = patch_publish_deps

        params = _make_input(comment="Initial publish for review", extension="ma")
        _run(tk_publish_tool(params))

        calls = [c for c in sg_create_mock.call_args_list if c[0][0] == "PublishedFile"]
        assert len(calls) == 1
        pf_data = calls[0][0][1]
        assert pf_data["description"] == "Initial publish for review"


# ===========================================================================
# 2. TestPublishMode2Explicit
# ===========================================================================

class TestPublishMode2Explicit:
    """Mode 2 publish with explicit path (no PipelineConfiguration)."""

    def test_mode2_uses_explicit_path(self, patch_publish_no_config, tmp_path):
        """Mode 2 publish stores the explicit publish_path."""
        sg_find_one_mock, sg_create_mock = patch_publish_no_config

        # Pre-create the publish_path file: in Mode 2 with no local_path,
        # tk_publish requires the file to already exist on disk (otherwise
        # we'd be registering a PublishedFile pointing at nothing).
        publish_file = tmp_path / "publishes" / "hero_robot_model_v001.ma"
        publish_file.parent.mkdir(parents=True, exist_ok=True)
        publish_file.write_text("// already-published Maya scene")
        explicit_path = str(publish_file)

        params = _make_input(publish_type="Maya Scene", publish_path=explicit_path)
        result = json.loads(_run(tk_publish_tool(params)))

        assert "error" not in result
        assert result["path"] == explicit_path

    def test_mode2_without_path_returns_error(self, patch_publish_no_config):
        """Mode 2 without publish_path returns a clear error message."""
        sg_find_one_mock, sg_create_mock = patch_publish_no_config

        params = _make_input(publish_type="Maya Scene")  # no publish_path, no PipelineConfig
        result = json.loads(_run(tk_publish_tool(params)))

        assert "error" in result
        assert "publish_path" in result["error"].lower() or "No PipelineConfiguration" in result["error"]

    def test_mode2_does_not_include_template(self, patch_publish_no_config, tmp_path):
        """Mode 2 response should NOT include template or project_root."""
        sg_find_one_mock, sg_create_mock = patch_publish_no_config

        # Pre-create the publish_path file (see test_mode2_uses_explicit_path).
        publish_file = tmp_path / "publishes" / "test_v001.ma"
        publish_file.parent.mkdir(parents=True, exist_ok=True)
        publish_file.write_text("// already-published Maya scene")
        explicit_path = str(publish_file)

        params = _make_input(publish_type="Maya Scene", publish_path=explicit_path)
        result = json.loads(_run(tk_publish_tool(params)))

        assert "error" not in result
        assert "template" not in result
        assert "project_root" not in result

    def test_mode2_copies_source_file(self, patch_publish_no_config, tmp_path):
        """Mode 2 copies local_path to publish_path when both are provided."""
        sg_find_one_mock, sg_create_mock = patch_publish_no_config

        # Create a source file
        source = tmp_path / "source" / "hero.ma"
        source.parent.mkdir(parents=True)
        source.write_text("// Maya ASCII scene file")

        publish_dir = tmp_path / "publishes"
        publish_file = publish_dir / "hero_v001.ma"

        params = _make_input(
            publish_type="Maya Scene",
            local_path=str(source),
            publish_path=str(publish_file),
        )
        result = json.loads(_run(tk_publish_tool(params)))

        assert "error" not in result
        assert publish_file.exists()
        assert publish_file.read_text() == "// Maya ASCII scene file"
        assert result["file_copied"] is True


# ===========================================================================
# 3. TestPublishAutoVersion
# ===========================================================================

class TestPublishAutoVersion:
    """Auto-increment version from existing versions on disk."""

    def test_auto_version_empty_dir(self, patch_publish_deps):
        """With no existing versions, auto-version starts at 1."""
        _, _, _ = patch_publish_deps

        params = _make_input(extension="ma")
        result = json.loads(_run(tk_publish_tool(params)))

        assert "error" not in result
        assert result["version_number"] == 1

    def test_auto_version_with_existing(self, patch_publish_deps):
        """With existing versions on disk, auto-version returns max+1."""
        tk_config, _, _ = patch_publish_deps

        # Create fake existing version files in the expected publish dir
        # Template: maya_asset_publish → @asset_root/publish/maya/{name}.v{version}.{maya_extension}
        # Resolved: assets/Character/hero_robot/model/publish/maya/main.v001.ma
        publish_dir = tk_config.project_root / "assets" / "Character" / "hero_robot" / "model" / "publish" / "maya"
        publish_dir.mkdir(parents=True, exist_ok=True)
        (publish_dir / "main.v001.ma").write_text("v1")
        (publish_dir / "main.v002.ma").write_text("v2")

        params = _make_input(extension="ma")
        result = json.loads(_run(tk_publish_tool(params)))

        assert "error" not in result
        assert result["version_number"] == 3

    def test_explicit_version_overrides_auto(self, patch_publish_deps):
        """When version_number is explicitly set, auto-version is skipped."""
        _, _, _ = patch_publish_deps

        params = _make_input(version_number=10, extension="ma")
        result = json.loads(_run(tk_publish_tool(params)))

        assert "error" not in result
        assert result["version_number"] == 10


# ===========================================================================
# 4. TestPublishFindOrCreateType
# ===========================================================================

class TestPublishFindOrCreateType:
    """Creates PublishedFileType if not found in ShotGrid."""

    def test_existing_type_reused(self, patch_publish_no_config, tmp_path):
        """When PublishedFileType already exists, it is reused (no create call)."""
        sg_find_one_mock, sg_create_mock = patch_publish_no_config

        # Pre-create the publish_path file (Mode 2 with no local_path).
        # Without this the new guard short-circuits before reaching the
        # PublishedFileType lookup, making the assertion vacuously true.
        publish_file = tmp_path / "pub" / "test.ma"
        publish_file.parent.mkdir(parents=True, exist_ok=True)
        publish_file.write_text("// already-published Maya scene")
        explicit_path = str(publish_file)
        params = _make_input(publish_type="Maya Scene", publish_path=explicit_path)
        _run(tk_publish_tool(params))

        # sg_create should only be called once — for the PublishedFile itself
        # Because the mock_sg_find_one returns a PFT matching the requested code
        create_calls = sg_create_mock.call_args_list
        pft_creates = [c for c in create_calls if c[0][0] == "PublishedFileType"]
        assert len(pft_creates) == 0

    def test_missing_type_is_created(self, patch_publish_no_config, tmp_path):
        """When PublishedFileType doesn't exist, it is created automatically."""
        sg_find_one_mock, sg_create_mock = patch_publish_no_config

        # Override find_one to return None for PublishedFileType
        original_side_effect = sg_find_one_mock.side_effect

        async def _find_one_no_pft(entity_type, filters, fields):
            if entity_type == "PublishedFileType":
                return None
            return await original_side_effect(entity_type, filters, fields)

        sg_find_one_mock.side_effect = _find_one_no_pft

        # Pre-create the publish_path file (Mode 2 with no local_path
        # requires the file to already exist on disk).
        publish_file = tmp_path / "pub" / "hero.abc"
        publish_file.parent.mkdir(parents=True, exist_ok=True)
        publish_file.write_text("// already-published Alembic")
        explicit_path = str(publish_file)
        params = _make_input(publish_type="Alembic Cache", publish_path=explicit_path)
        _run(tk_publish_tool(params))

        # sg_create should have been called for PublishedFileType
        create_calls = sg_create_mock.call_args_list
        pft_creates = [c for c in create_calls if c[0][0] == "PublishedFileType"]
        assert len(pft_creates) == 1
        assert pft_creates[0][0][1]["code"] == "Alembic Cache"

    def test_created_type_used_in_publish(self, patch_publish_no_config, tmp_path):
        """A newly created PublishedFileType is linked to the PublishedFile."""
        sg_find_one_mock, sg_create_mock = patch_publish_no_config

        original_side_effect = sg_find_one_mock.side_effect

        async def _find_one_no_pft(entity_type, filters, fields):
            if entity_type == "PublishedFileType":
                return None
            return await original_side_effect(entity_type, filters, fields)

        sg_find_one_mock.side_effect = _find_one_no_pft

        # Pre-create the publish_path file (Mode 2 with no local_path).
        publish_file = tmp_path / "pub" / "hero.abc"
        publish_file.parent.mkdir(parents=True, exist_ok=True)
        publish_file.write_text("// already-published Alembic")
        explicit_path = str(publish_file)
        params = _make_input(publish_type="Alembic Cache", publish_path=explicit_path)
        _run(tk_publish_tool(params))

        # The PublishedFile create call should reference the newly created PFT id
        pf_calls = [c for c in sg_create_mock.call_args_list if c[0][0] == "PublishedFile"]
        assert len(pf_calls) == 1
        pf_data = pf_calls[0][0][1]
        # The PFT was created with id 9999 (from mock_sg_create)
        assert pf_data["published_file_type"]["id"] == 9999


# ===========================================================================
# 5. TestPublishTaskLinking
# ===========================================================================

class TestPublishTaskLinking:
    """Links PublishedFile to correct Task."""

    def test_task_linked_when_found(self, patch_publish_deps):
        """When a matching Task exists, the PublishedFile.task field is set."""
        _, _, sg_create_mock = patch_publish_deps

        params = _make_input(step="model", extension="ma")
        result = json.loads(_run(tk_publish_tool(params)))

        assert "error" not in result
        assert result["task"] == "Model"

        # Verify the PublishedFile data includes task
        pf_calls = [c for c in sg_create_mock.call_args_list if c[0][0] == "PublishedFile"]
        assert len(pf_calls) == 1
        pf_data = pf_calls[0][0][1]
        assert pf_data["task"] == {"type": "Task", "id": 4001}

    def test_no_task_when_not_found(self, patch_publish_deps):
        """When no matching Task exists, the task field is omitted."""
        _, sg_find_one_mock, sg_create_mock = patch_publish_deps

        original_side_effect = sg_find_one_mock.side_effect

        async def _find_one_no_task(entity_type, filters, fields):
            if entity_type == "Task":
                return None
            return await original_side_effect(entity_type, filters, fields)

        sg_find_one_mock.side_effect = _find_one_no_task

        params = _make_input(step="model", extension="ma")
        result = json.loads(_run(tk_publish_tool(params)))

        assert "error" not in result
        assert result["task"] is None

        # Verify the PublishedFile data does NOT include task
        pf_calls = [c for c in sg_create_mock.call_args_list if c[0][0] == "PublishedFile"]
        pf_data = pf_calls[0][0][1]
        assert "task" not in pf_data

    def test_task_queried_with_correct_step(self, patch_publish_deps):
        """Task query uses the correct step filter from input params."""
        _, sg_find_one_mock, _ = patch_publish_deps

        params = _make_input(step="rig", extension="ma")
        _run(tk_publish_tool(params))

        # Find the sg_find_one call for Task
        task_calls = [
            c for c in sg_find_one_mock.call_args_list
            if c[0][0] == "Task"
        ]
        assert len(task_calls) == 1
        filters = task_calls[0][0][1]
        # Should filter by entity link and step
        step_filter = [f for f in filters if "step" in str(f)]
        assert len(step_filter) == 1
        assert "rig" in str(step_filter[0])


# ===========================================================================
# 6. TestPublishFileCopy
# ===========================================================================

class TestPublishFileCopy:
    """Source file copied to publish path."""

    def test_file_copied_mode1(self, patch_publish_deps):
        """In Mode 1, local_path is copied to the resolved template path."""
        tk_config, _, _ = patch_publish_deps

        # Create a source file
        source = tk_config.project_root / "_source" / "hero.ma"
        source.parent.mkdir(parents=True)
        source.write_text("// Maya ASCII scene content v1")

        params = _make_input(
            local_path=str(source),
            extension="ma",
        )
        result = json.loads(_run(tk_publish_tool(params)))

        assert "error" not in result
        assert result["file_copied"] is True

        # The publish path should exist and have the same content
        published = Path(result["path"])
        assert published.exists(), f"Published file not found at {published}"
        assert published.read_text() == "// Maya ASCII scene content v1"

    def test_file_not_copied_when_no_local_path(self, patch_publish_deps):
        """When local_path is None, no file copy occurs."""
        _, _, _ = patch_publish_deps

        params = _make_input(extension="ma")
        result = json.loads(_run(tk_publish_tool(params)))

        assert "error" not in result
        assert result["file_copied"] is False

    def test_publish_dir_created_automatically(self, patch_publish_deps):
        """Mode 1 publish creates parent directories automatically."""
        tk_config, _, _ = patch_publish_deps

        source = tk_config.project_root / "_source" / "auto_dir_test.ma"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("// test content")

        params = _make_input(local_path=str(source), extension="ma")
        result = json.loads(_run(tk_publish_tool(params)))

        assert "error" not in result
        published = Path(result["path"])
        assert published.parent.exists()

    def test_file_content_preserved(self, patch_publish_deps):
        """Binary-safe: file content is identical after copy."""
        tk_config, _, _ = patch_publish_deps

        source = tk_config.project_root / "_source" / "binary_test.ma"
        source.parent.mkdir(parents=True, exist_ok=True)
        # Write some binary-like content
        content = bytes(range(256))
        source.write_bytes(content)

        params = _make_input(local_path=str(source), extension="ma")
        result = json.loads(_run(tk_publish_tool(params)))

        assert "error" not in result
        published = Path(result["path"])
        assert published.read_bytes() == content
