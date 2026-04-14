"""Tests for the ``fpt_launch_app`` MCP tool.

Uses dry_run=True for all happy-path tests to avoid actually spawning
Maya. Subprocess.Popen is still mocked where we want to verify the
exact argv that would be used.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _run(coro):
    """Run a coroutine to completion in the current thread.

    The MCP tool functions are async; the suite does not install
    pytest-asyncio, so we drive the coroutines synchronously via
    asyncio.run. Each call creates and tears down its own event loop.
    """
    return asyncio.run(coro)

from fpt_mcp.server import (
    FptLaunchAppInput,
    _project_id_for_entity,
    fpt_launch_app_tool,
)
from fpt_mcp.software_resolver import ResolvedApp


# ---------------------------------------------------------------------------
# _project_id_for_entity
# ---------------------------------------------------------------------------


class TestProjectIdForEntity:
    def test_project_returns_self_id(self):
        assert _project_id_for_entity("Project", 1244) == 1244

    def test_asset_uses_sg_lookup(self):
        sg = MagicMock()
        sg.find_one.return_value = {
            "id": 42, "project": {"type": "Project", "id": 1244}
        }
        with patch("fpt_mcp.server.get_sg", return_value=sg):
            assert _project_id_for_entity("Asset", 42) == 1244
        sg.find_one.assert_called_once_with(
            "Asset", [["id", "is", 42]], ["project"]
        )

    def test_no_project_field_returns_none(self):
        sg = MagicMock()
        sg.find_one.return_value = {"id": 42, "project": None}
        with patch("fpt_mcp.server.get_sg", return_value=sg):
            assert _project_id_for_entity("Asset", 42) is None

    def test_sg_error_returns_none(self):
        sg = MagicMock()
        sg.find_one.side_effect = RuntimeError("boom")
        with patch("fpt_mcp.server.get_sg", return_value=sg):
            assert _project_id_for_entity("Asset", 42) is None


# ---------------------------------------------------------------------------
# fpt_launch_app tool — happy and error paths
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_sg():
    sg = MagicMock()
    sg.find_one.return_value = {
        "id": 42, "project": {"type": "Project", "id": 1244}
    }
    return sg


@pytest.fixture
def resolved_tank(tmp_path: Path) -> ResolvedApp:
    binary = tmp_path / "maya2027" / "Maya.app"
    binary.mkdir(parents=True)
    tank = tmp_path / "config" / "tank"
    tank.parent.mkdir()
    tank.write_text("#!/bin/sh", encoding="utf-8")
    tank.chmod(0o755)
    return ResolvedApp(
        app="maya",
        binary=binary,
        version="2027",
        engine="tk-maya",
        launch_method="tank",
        tank_command=tank,
        pipeline_config_path=tmp_path / "config",
        source_layers=["os_scan", "toolkit_yaml", "sg_software"],
        warnings=[],
    )


@pytest.fixture
def resolved_open(tmp_path: Path) -> ResolvedApp:
    binary = tmp_path / "maya2027" / "Maya.app"
    binary.mkdir(parents=True)
    return ResolvedApp(
        app="maya",
        binary=binary,
        version="2027",
        launch_method="open",
        source_layers=["os_scan"],
        warnings=[],
    )


class TestFptLaunchAppTool:
    def test_not_installed_returns_error(self, fake_sg):
        params = FptLaunchAppInput(app="maya", entity_type="Asset", entity_id=42)
        with patch("fpt_mcp.server.get_sg", return_value=fake_sg), \
             patch("fpt_mcp.server.resolve_app", return_value=None) as r:
            raw = _run(fpt_launch_app_tool(params))
        data = json.loads(raw)
        assert "error" in data
        assert "not installed" in data["error"]
        r.assert_called_once()

    def test_tank_launch_dry_run(self, fake_sg, resolved_tank):
        params = FptLaunchAppInput(
            app="maya", entity_type="Asset", entity_id=42, dry_run=True
        )
        with patch("fpt_mcp.server.get_sg", return_value=fake_sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_tank):
            raw = _run(fpt_launch_app_tool(params))
        data = json.loads(raw)
        assert data["dry_run"] is True
        assert data["launch_method"] == "tank"
        assert data["project_id"] == 1244
        assert data["argv"] == [
            str(resolved_tank.tank_command), "Asset", "42", "launch_maya"
        ]
        assert "pid" not in data
        assert data["source_layers"] == ["os_scan", "toolkit_yaml", "sg_software"]

    def test_open_fallback_adds_warning(self, fake_sg, resolved_open):
        params = FptLaunchAppInput(
            app="maya", entity_type="Shot", entity_id=77, dry_run=True
        )
        with patch("fpt_mcp.server.get_sg", return_value=fake_sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_open):
            raw = _run(fpt_launch_app_tool(params))
        data = json.loads(raw)
        assert data["launch_method"] == "open"
        assert data["argv"] == ["open", "-a", str(resolved_open.binary)]
        assert any("without Toolkit context" in w for w in data["warnings"])

    def test_real_launch_captures_pid(self, fake_sg, resolved_tank):
        params = FptLaunchAppInput(
            app="maya", entity_type="Asset", entity_id=42
        )
        fake_proc = MagicMock()
        fake_proc.pid = 99999
        with patch("fpt_mcp.server.get_sg", return_value=fake_sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_tank), \
             patch("subprocess.Popen", return_value=fake_proc) as popen:
            raw = _run(fpt_launch_app_tool(params))
        data = json.loads(raw)
        assert data["pid"] == 99999
        popen.assert_called_once()
        call_argv = popen.call_args[0][0]
        assert call_argv[0] == str(resolved_tank.tank_command)
        assert call_argv[-1] == "launch_maya"
        # start_new_session must be set so the launched Maya survives the
        # MCP server process if the server is restarted.
        assert popen.call_args.kwargs.get("start_new_session") is True

    def test_subprocess_failure_reports_error_in_plan(
        self, fake_sg, resolved_tank
    ):
        params = FptLaunchAppInput(
            app="maya", entity_type="Asset", entity_id=42
        )
        with patch("fpt_mcp.server.get_sg", return_value=fake_sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_tank), \
             patch("subprocess.Popen", side_effect=OSError("permission denied")):
            raw = _run(fpt_launch_app_tool(params))
        data = json.loads(raw)
        # Plan is still returned (with the resolved binary, tank path, etc.)
        # and the error is surfaced so the LLM can explain it.
        assert data["launch_method"] == "tank"
        assert "error" in data
        assert "permission denied" in data["error"]
        assert "pid" not in data

    def test_project_id_inferred_from_entity(self, resolved_tank):
        params = FptLaunchAppInput(
            app="maya", entity_type="Asset", entity_id=42, dry_run=True
        )
        sg = MagicMock()
        sg.find_one.return_value = {
            "id": 42, "project": {"type": "Project", "id": 9999}
        }
        captured: dict = {}

        def fake_resolve(app, project_id=None, sg_find=None, glob_pattern=None):
            captured["project_id"] = project_id
            return resolved_tank

        with patch("fpt_mcp.server.get_sg", return_value=sg), \
             patch("fpt_mcp.server.resolve_app", side_effect=fake_resolve):
            _run(fpt_launch_app_tool(params))
        assert captured["project_id"] == 9999


# ---------------------------------------------------------------------------
# install.sh — guardrail test for the non-negotiable pre-approve rule
# ---------------------------------------------------------------------------


def test_install_sh_preapproves_fpt_launch_app():
    """install.sh MUST list fpt_launch_app in its TOOLS array.

    This guards the non-negotiable rule from CLAUDE.md: every tool added
    to server.py must be pre-approved in install.sh in the same commit,
    otherwise users get permission prompts on first use.
    """
    install_sh = Path(__file__).parent.parent / "install.sh"
    text = install_sh.read_text(encoding="utf-8")
    assert '"fpt_launch_app"' in text, (
        "fpt_launch_app is not in install.sh TOOLS array — add it to the "
        "pre-approval list to avoid permission prompts"
    )
