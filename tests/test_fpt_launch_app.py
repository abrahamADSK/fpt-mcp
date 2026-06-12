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

from fpt_mcp.server import (  # noqa: E402 — _run helper above stays adjacent to the imports it enables
    FptLaunchAppInput,
    _project_id_for_entity,
    fpt_launch_app_tool,
)
from fpt_mcp.software_resolver import ResolvedApp  # noqa: E402


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
            str(resolved_tank.tank_command), "Asset", "42", "maya_2027"
        ]
        assert "pid" not in data
        assert data["source_layers"] == ["os_scan", "toolkit_yaml", "sg_software"]

    def test_tank_fallback_to_launch_app_when_no_version(
        self, fake_sg, tmp_path: Path
    ):
        """When the OS scan cannot parse a version, fall back to the
        generic ``launch_<app>`` tank command instead of ``<app>_<ver>``."""
        binary = tmp_path / "mayaRC" / "Maya.app"
        binary.mkdir(parents=True)
        tank = tmp_path / "tank"
        tank.write_text("#!/bin/sh", encoding="utf-8")
        tank.chmod(0o755)
        versionless = ResolvedApp(
            app="maya",
            binary=binary,
            version=None,
            engine="tk-maya",
            launch_method="tank",
            tank_command=tank,
            pipeline_config_path=tmp_path,
            source_layers=["os_scan", "toolkit_yaml"],
            warnings=[],
        )
        params = FptLaunchAppInput(
            app="maya", entity_type="Asset", entity_id=42, dry_run=True
        )
        with patch("fpt_mcp.server.get_sg", return_value=fake_sg), \
             patch("fpt_mcp.server.resolve_app", return_value=versionless):
            raw = _run(fpt_launch_app_tool(params))
        data = json.loads(raw)
        assert data["argv"][-1] == "launch_maya"

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
        assert call_argv[-1] == "maya_2027"
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


# ---------------------------------------------------------------------------
# Flame context launch — direct startApplication route (Chat 65)
# ---------------------------------------------------------------------------


@pytest.fixture
def resolved_flame(tmp_path: Path) -> ResolvedApp:
    binary = tmp_path / "flame_2027.0.1" / "bin" / "startApplication"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    return ResolvedApp(
        app="flame",
        binary=binary,
        version="2027.0.1",
        engine="tk-flame",
        launch_method="open",
        source_layers=["os_scan", "sg_software"],
        warnings=[],
    )


class TestFlameDirectLaunch:
    """route='auto'/'direct' composes startApplication with guard rails."""

    def _params(self, **kw) -> FptLaunchAppInput:
        return FptLaunchAppInput(
            app="flame", entity_type="Asset", entity_id=42, dry_run=True, **kw
        )

    def _sg(self, project_name: str) -> MagicMock:
        sg = MagicMock()
        # 1st find_one: entity → owning project; 2nd: Project → name.
        sg.find_one.side_effect = [
            {"id": 42, "project": {"type": "Project", "id": 1244}},
            {"id": 1244, "name": project_name},
        ]
        return sg

    def test_direct_launch_composes_start_project(self, resolved_flame):
        """SG name slugified with tk-flame's rule, validated locally."""
        sg = self._sg("FPT2025-25 basic test")  # → FPT2025_25_basic_test
        with patch("fpt_mcp.server.get_sg", return_value=sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_flame), \
             patch("fpt_mcp.launcher._local_flame_projects",
                   return_value=["FPT2025_25_basic_test", "other_proj"]), \
             patch("fpt_mcp.launcher._flame_running", return_value=False):
            data = json.loads(_run(fpt_launch_app_tool(self._params())))
        assert "error" not in data
        assert data["sg_project_name"] == "FPT2025-25 basic test"
        assert data["flame_project"] == "FPT2025_25_basic_test"
        assert data["route"] == "auto"
        assert data["argv"] == [
            str(resolved_flame.binary),
            "--start-project=FPT2025_25_basic_test",
            "--create-workspace",
            "--closed-libs",
        ]

    def test_workspace_param_uses_start_workspace(self, resolved_flame):
        sg = self._sg("MyProj")
        with patch("fpt_mcp.server.get_sg", return_value=sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_flame), \
             patch("fpt_mcp.launcher._local_flame_projects",
                   return_value=["MyProj"]), \
             patch("fpt_mcp.launcher._flame_running", return_value=False):
            data = json.loads(_run(
                fpt_launch_app_tool(self._params(workspace="comp"))
            ))
        assert "--start-workspace=comp" in data["argv"]
        assert "--create-workspace" not in data["argv"]

    def test_project_not_local_errors_with_guidance(self, resolved_flame):
        """The direct route must NEVER pass an unverified project name —
        Flame errors on unknown names; tk-flame can create them instead."""
        sg = self._sg("Totally Unknown Show")
        with patch("fpt_mcp.server.get_sg", return_value=sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_flame), \
             patch("fpt_mcp.launcher._local_flame_projects",
                   return_value=["FPT2025_25_basic_test"]), \
             patch("fpt_mcp.launcher._flame_running", return_value=False):
            data = json.loads(_run(fpt_launch_app_tool(self._params())))
        assert "does not exist on this workstation" in data["error"]
        assert "route='toolkit'" in data["error"]
        assert "argv" not in data  # nothing launchable was composed

    def test_running_flame_refused_without_force(self, resolved_flame):
        sg = self._sg("MyProj")
        with patch("fpt_mcp.server.get_sg", return_value=sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_flame), \
             patch("fpt_mcp.launcher._local_flame_projects",
                   return_value=["MyProj"]), \
             patch("fpt_mcp.launcher._flame_running", return_value=True):
            data = json.loads(_run(fpt_launch_app_tool(self._params())))
        assert "already running" in data["error"]
        assert "force=true" in data["error"]

    def test_running_flame_force_overrides(self, resolved_flame):
        sg = self._sg("MyProj")
        with patch("fpt_mcp.server.get_sg", return_value=sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_flame), \
             patch("fpt_mcp.launcher._local_flame_projects",
                   return_value=["MyProj"]), \
             patch("fpt_mcp.launcher._flame_running", return_value=True):
            data = json.loads(_run(
                fpt_launch_app_tool(self._params(force=True))
            ))
        assert "error" not in data
        assert "--start-project=MyProj" in data["argv"]

    def test_route_toolkit_without_tank_errors(self, resolved_flame):
        """route='toolkit' is an explicit ask — without a tank CLI it must
        fail loudly, not silently degrade to the direct route."""
        sg = self._sg("MyProj")
        with patch("fpt_mcp.server.get_sg", return_value=sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_flame):
            data = json.loads(_run(
                fpt_launch_app_tool(self._params(route="toolkit"))
            ))
        assert "route='toolkit' requested but no usable Toolkit tank CLI" \
            in data["error"]

    def test_case_insensitive_local_match(self, resolved_flame):
        sg = self._sg("myproj")
        with patch("fpt_mcp.server.get_sg", return_value=sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_flame), \
             patch("fpt_mcp.launcher._local_flame_projects",
                   return_value=["MyProj"]), \
             patch("fpt_mcp.launcher._flame_running", return_value=False):
            data = json.loads(_run(fpt_launch_app_tool(self._params())))
        assert data["flame_project"] == "MyProj"


class TestRouteParam:
    def test_maya_route_direct_skips_tank(self, fake_sg, resolved_tank):
        """route='direct' must bypass an available tank CLI for maya too."""
        params = FptLaunchAppInput(
            app="maya", entity_type="Asset", entity_id=42,
            dry_run=True, route="direct",
        )
        with patch("fpt_mcp.server.get_sg", return_value=fake_sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_tank):
            data = json.loads(_run(fpt_launch_app_tool(params)))
        assert data["argv"][0] == "open"
        assert any("without Toolkit context" in w for w in data["warnings"])

    def test_invalid_route_rejected_by_model(self):
        with pytest.raises(ValueError):
            FptLaunchAppInput(
                app="maya", entity_type="Asset", entity_id=42, route="ssh"
            )
