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
# Sequence → Step Task resolution (launch into sequence_layout, not base)
# ---------------------------------------------------------------------------


@pytest.fixture
def seq_sg():
    """SG mock for a Sequence launch: find_one → project; find → its Tasks."""
    sg = MagicMock()
    sg.find_one.return_value = {
        "id": 1662, "project": {"type": "Project", "id": 1244}
    }
    sg.find.return_value = [
        {"id": 6753, "content": "Layout",
         "step": {"type": "Step", "id": 142, "name": "Layout"}},
        {"id": 6752, "content": "Master Lighting",
         "step": {"type": "Step", "id": 143, "name": "Master Lighting"}},
    ]
    return sg


class TestSequenceTaskResolution:
    def test_sequence_defaults_to_layout_task(self, seq_sg, resolved_tank):
        params = FptLaunchAppInput(
            app="maya", entity_type="Sequence", entity_id=1662, dry_run=True
        )
        with patch("fpt_mcp.server.get_sg", return_value=seq_sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_tank):
            data = json.loads(_run(fpt_launch_app_tool(params)))
        # tank Task 6753 (Layout), NOT the step-less Sequence 1662.
        assert data["argv"] == [
            str(resolved_tank.tank_command), "Task", "6753", "maya_2027"
        ]
        assert data["resolved_task"]["step"] == "Layout"
        assert data["resolved_task"]["launched_as"] == "Task 6753"

    def test_sequence_step_override_master_lighting(self, seq_sg, resolved_tank):
        params = FptLaunchAppInput(
            app="maya", entity_type="Sequence", entity_id=1662,
            step="Master Lighting", dry_run=True,
        )
        with patch("fpt_mcp.server.get_sg", return_value=seq_sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_tank):
            data = json.loads(_run(fpt_launch_app_tool(params)))
        assert data["argv"][1:3] == ["Task", "6752"]
        assert data["resolved_task"]["step"] == "Master Lighting"

    def test_sequence_missing_step_task_errors(self, resolved_tank):
        sg = MagicMock()
        sg.find_one.return_value = {"id": 1662, "project": {"id": 1244}}
        sg.find.return_value = [
            {"id": 6752, "content": "Master Lighting",
             "step": {"name": "Master Lighting"}},
        ]
        params = FptLaunchAppInput(
            app="maya", entity_type="Sequence", entity_id=1662, dry_run=True
        )
        with patch("fpt_mcp.server.get_sg", return_value=sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_tank):
            data = json.loads(_run(fpt_launch_app_tool(params)))
        assert "error" in data
        assert "Layout" in data["error"]
        assert "argv" not in data  # broken launch refused, not composed

    def test_sequence_sg_error_falls_back_to_entity(self, resolved_tank):
        """A task-resolution SG failure never blocks: keep the entity launch."""
        sg = MagicMock()
        sg.find_one.return_value = {"id": 1662, "project": {"id": 1244}}
        sg.find.side_effect = RuntimeError("SG down")
        params = FptLaunchAppInput(
            app="maya", entity_type="Sequence", entity_id=1662, dry_run=True
        )
        with patch("fpt_mcp.server.get_sg", return_value=sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_tank):
            data = json.loads(_run(fpt_launch_app_tool(params)))
        assert data["argv"][1:3] == ["Sequence", "1662"]
        assert "resolved_task" not in data

    def test_asset_launch_not_task_resolved(self, fake_sg, resolved_tank):
        """Asset launches keep entity-level context — no task resolution."""
        params = FptLaunchAppInput(
            app="maya", entity_type="Asset", entity_id=42, dry_run=True
        )
        with patch("fpt_mcp.server.get_sg", return_value=fake_sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_tank):
            data = json.loads(_run(fpt_launch_app_tool(params)))
        assert data["argv"] == [
            str(resolved_tank.tank_command), "Asset", "42", "maya_2027"
        ]
        assert "resolved_task" not in data


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
    """route='auto'/'direct': native-link discovery drives the launch.

    Chat 93 contract (user-approved): read each local project's
    shotgunProjectName from its metadata clib (READ-ONLY); exactly one
    linked -> open it; several -> INCONSISTENT error (1:1 relation, never
    a menu); none -> choice_required listing (the caller asks the user).
    """

    def _params(self, **kw) -> FptLaunchAppInput:
        return FptLaunchAppInput(
            app="flame", entity_type="Asset", entity_id=42, dry_run=True, **kw
        )

    def _sg(self, project_name: str) -> MagicMock:
        sg = MagicMock()
        # 1st find_one: entity -> owning project; 2nd: Project -> name.
        sg.find_one.side_effect = [
            {"id": 42, "project": {"type": "Project", "id": 1244}},
            {"id": 1244, "name": project_name},
        ]
        return sg

    def _launch(self, resolved_flame, sg, pairs, links, params,
                running=False, source="stone_wire"):
        """Run the tool with the link-discovery seams patched."""
        with patch("fpt_mcp.server.get_sg", return_value=sg), \
             patch("fpt_mcp.server.resolve_app",
                   return_value=resolved_flame), \
             patch("fpt_mcp.launcher._local_flame_projects_with_paths",
                   return_value=(pairs, source)), \
             patch("fpt_mcp.launcher._read_fpt_link",
                   side_effect=lambda n, p: links.get(n)), \
             patch("fpt_mcp.launcher._flame_running", return_value=running):
            return json.loads(_run(fpt_launch_app_tool(params)))

    PAIRS = [
        ("AUTODESK_UNIVERSITY_2026_MCP", "/var/opt/proj/AU"),
        ("MCP_PROJECT_ABRAHAM", "/var/opt/proj/MPA"),
    ]

    def test_linked_project_auto_opens(self, resolved_flame):
        """Exactly one project linked to the FPT project -> opens directly."""
        data = self._launch(
            resolved_flame, self._sg("MCP_project_Abraham"), self.PAIRS,
            {"AUTODESK_UNIVERSITY_2026_MCP": "MCP_project_Abraham",
             "MCP_PROJECT_ABRAHAM": ""},
            self._params(),
        )
        assert "error" not in data
        assert data["fpt_linked"] is True
        assert data["flame_project"] == "AUTODESK_UNIVERSITY_2026_MCP"
        assert data["argv"][1] == \
            "--start-project=AUTODESK_UNIVERSITY_2026_MCP"

    def test_link_match_is_case_insensitive(self, resolved_flame):
        data = self._launch(
            resolved_flame, self._sg("mcp_project_abraham"), self.PAIRS,
            {"AUTODESK_UNIVERSITY_2026_MCP": "MCP_project_Abraham",
             "MCP_PROJECT_ABRAHAM": ""},
            self._params(),
        )
        assert data["fpt_linked"] is True
        assert data["flame_project"] == "AUTODESK_UNIVERSITY_2026_MCP"

    def test_multiple_linked_is_inconsistency_error(self, resolved_flame):
        """1:1 relation: several claimants is corruption, never a menu."""
        data = self._launch(
            resolved_flame, self._sg("MCP_project_Abraham"), self.PAIRS,
            {"AUTODESK_UNIVERSITY_2026_MCP": "MCP_project_Abraham",
             "MCP_PROJECT_ABRAHAM": "MCP_project_Abraham"},
            self._params(),
        )
        assert "INCONSISTENT" in data["error"]
        assert "AUTODESK_UNIVERSITY_2026_MCP" in data["error"]
        assert "MCP_PROJECT_ABRAHAM" in data["error"]
        # Remediation pointer: since Chat 98 the MCP write path is gone, so
        # the only way to break a link is Flame's own FPT menu.
        assert "Flow Production Tracking menu" in data["error"]
        assert "argv" not in data  # nothing launched

    def test_none_linked_returns_choice_required(self, resolved_flame):
        data = self._launch(
            resolved_flame, self._sg("MCP_project_Abraham"), self.PAIRS,
            {"AUTODESK_UNIVERSITY_2026_MCP": "", "MCP_PROJECT_ABRAHAM": ""},
            self._params(),
        )
        assert data["choice_required"] is True
        listing = {e["name"]: e["fpt_link"]
                   for e in data["local_flame_projects"]}
        assert set(listing) == {"AUTODESK_UNIVERSITY_2026_MCP",
                                "MCP_PROJECT_ABRAHAM"}
        assert "fpt_link" in data["hint"]  # set the link after opening
        assert "argv" not in data

    def test_none_linked_degraded_source_warns(self, resolved_flame):
        """A dir-scan fallback listing may be incomplete -> surfaced."""
        data = self._launch(
            resolved_flame, self._sg("MCP_project_Abraham"), self.PAIRS,
            {"AUTODESK_UNIVERSITY_2026_MCP": "", "MCP_PROJECT_ABRAHAM": ""},
            self._params(), source="dir-scan",
        )
        assert data["choice_required"] is True
        assert "dir-scan" in data["warning"]

    def test_workspace_param_uses_start_workspace(self, resolved_flame):
        data = self._launch(
            resolved_flame, self._sg("MCP_project_Abraham"), self.PAIRS,
            {"AUTODESK_UNIVERSITY_2026_MCP": "MCP_project_Abraham",
             "MCP_PROJECT_ABRAHAM": ""},
            self._params(workspace="comp"),
        )
        assert "--start-workspace=comp" in data["argv"]
        assert "--create-workspace" not in data["argv"]

    def test_running_flame_refused_without_force(self, resolved_flame):
        data = self._launch(
            resolved_flame, self._sg("MCP_project_Abraham"), self.PAIRS,
            {"AUTODESK_UNIVERSITY_2026_MCP": "MCP_project_Abraham",
             "MCP_PROJECT_ABRAHAM": ""},
            self._params(), running=True,
        )
        assert "already running" in data["error"]
        assert "force=true" in data["error"]

    def test_running_flame_force_overrides(self, resolved_flame):
        data = self._launch(
            resolved_flame, self._sg("MCP_project_Abraham"), self.PAIRS,
            {"AUTODESK_UNIVERSITY_2026_MCP": "MCP_project_Abraham",
             "MCP_PROJECT_ABRAHAM": ""},
            self._params(force=True), running=True,
        )
        assert "error" not in data
        assert data["argv"][1] == \
            "--start-project=AUTODESK_UNIVERSITY_2026_MCP"

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

    def test_list_projects_enumerates_with_links(self, resolved_flame):
        """list_projects reports every local project WITH its stored link."""
        data = self._launch(
            resolved_flame, self._sg("MCP_project_Abraham"), self.PAIRS,
            {"AUTODESK_UNIVERSITY_2026_MCP": "MCP_project_Abraham",
             "MCP_PROJECT_ABRAHAM": ""},
            self._params(list_projects=True),
        )
        assert data["choice_required"] is True
        listing = {e["name"]: e["fpt_link"]
                   for e in data["local_flame_projects"]}
        assert listing["AUTODESK_UNIVERSITY_2026_MCP"] == \
            "MCP_project_Abraham"
        assert listing["MCP_PROJECT_ABRAHAM"] == ""
        assert "argv" not in data

    def test_flame_project_overrides_discovery(self, resolved_flame):
        """Explicit user choice wins over discovery (case-insensitive)."""
        data = self._launch(
            resolved_flame, self._sg("MCP_project_Abraham"), self.PAIRS,
            {"AUTODESK_UNIVERSITY_2026_MCP": "MCP_project_Abraham",
             "MCP_PROJECT_ABRAHAM": ""},
            self._params(flame_project="mcp_project_abraham"),
        )
        assert "error" not in data
        assert data["flame_project"] == "MCP_PROJECT_ABRAHAM"
        assert data["argv"][1] == "--start-project=MCP_PROJECT_ABRAHAM"

    def test_flame_project_unknown_errors_with_local_list(
        self, resolved_flame
    ):
        data = self._launch(
            resolved_flame, self._sg("MCP_project_Abraham"), self.PAIRS,
            {"AUTODESK_UNIVERSITY_2026_MCP": "", "MCP_PROJECT_ABRAHAM": ""},
            self._params(flame_project="NO_SUCH_PROJECT"),
        )
        assert "error" in data
        assert "MCP_PROJECT_ABRAHAM" in data["error"]


class TestReadFptLink:
    """Unit tests for the clib link parser against synthetic binaries.

    Layout validated on real 2025/2026/2027 specimens (Chat 93): the
    shotgunProjectName slot is the 4-byte big-endian length-prefixed
    string IMMEDIATELY before the "Dolby Vision" settings field.
    """

    @staticmethod
    def _clib(link: str) -> bytes:
        name = link.encode()
        return (
            b"\x00" * 32  # arbitrary preamble
            + len(name).to_bytes(4, "big") + name
            + len(b"Dolby Vision 2.9").to_bytes(4, "big")
            + b"Dolby Vision 2.9"
            + b"\x00" * 8
        )

    def _write(self, tmp_path, payload: bytes) -> tuple[str, str]:
        home = tmp_path / "PROJ"
        (home / "catalog").mkdir(parents=True)
        (home / "catalog" / ".#project.000.clib").write_bytes(payload)
        return "PROJ", str(home)

    def test_linked_value_parsed(self, tmp_path):
        from fpt_mcp.launcher import _read_fpt_link
        name, home = self._write(tmp_path, self._clib("MCP_project_Abraham"))
        assert _read_fpt_link(name, home) == "MCP_project_Abraham"

    def test_unlinked_empty_string(self, tmp_path):
        from fpt_mcp.launcher import _read_fpt_link
        name, home = self._write(tmp_path, self._clib(""))
        assert _read_fpt_link(name, home) == ""

    def test_missing_marker_is_none(self, tmp_path):
        from fpt_mcp.launcher import _read_fpt_link
        name, home = self._write(tmp_path, b"\x00" * 64)
        assert _read_fpt_link(name, home) is None

    def test_missing_clib_is_none(self, tmp_path):
        from fpt_mcp.launcher import _read_fpt_link
        assert _read_fpt_link("GHOST", str(tmp_path / "nowhere")) is None


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


# ---------------------------------------------------------------------------
# Maya single-instance guard — Command Port already bound (parity w/ Flame)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _maya_port_closed_by_default():
    """Default every test in this module to "no Maya on the Command Port".

    ``fpt_launch_app`` probes ``_maya_command_port_open`` on every maya
    launch. Without this, a test run on a workstation with a live Maya (port
    8100 bound) would hit a real socket and the guard would fire, breaking the
    happy-path maya tests. We patch the symbol — never a real socket — so the
    suite is deterministic regardless of what is running locally. The guard
    tests below patch it again inside their own ``with`` block, which nests
    inside (and therefore takes precedence over) this default.
    """
    with patch(
        "fpt_mcp.launcher._maya_command_port_open", return_value=False
    ):
        yield


class TestMayaSingleInstanceGuard:
    """A running Maya owns the Command Port; a second launch is refused.

    Mirrors ``TestFlameDirectLaunch``'s running-instance tests but for Maya:
    the guard probes ``_maya_command_port_open`` (patched here, never a real
    socket) and fires for ALL maya routes before any argv is composed.
    """

    def _maya(self, **kw) -> FptLaunchAppInput:
        return FptLaunchAppInput(
            app="maya", entity_type="Asset", entity_id=42, dry_run=True, **kw
        )

    def test_running_maya_refused_without_force(self, fake_sg, resolved_tank):
        with patch("fpt_mcp.server.get_sg", return_value=fake_sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_tank), \
             patch("fpt_mcp.launcher._maya_command_port_open",
                   return_value=True):
            data = json.loads(_run(fpt_launch_app_tool(self._maya())))
        assert "Command Port" in data["error"]
        assert "force=true" in data["error"]
        assert "argv" not in data  # refused before any argv is composed

    def test_running_maya_force_overrides(self, fake_sg, resolved_tank):
        with patch("fpt_mcp.server.get_sg", return_value=fake_sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_tank), \
             patch("fpt_mcp.launcher._maya_command_port_open",
                   return_value=True):
            data = json.loads(_run(
                fpt_launch_app_tool(self._maya(force=True))
            ))
        assert "error" not in data
        assert data["argv"] == [
            str(resolved_tank.tank_command), "Asset", "42", "maya_2027"
        ]

    def test_no_running_maya_launches_normally(self, fake_sg, resolved_tank):
        with patch("fpt_mcp.server.get_sg", return_value=fake_sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_tank), \
             patch("fpt_mcp.launcher._maya_command_port_open",
                   return_value=False):
            data = json.loads(_run(fpt_launch_app_tool(self._maya())))
        assert "error" not in data
        assert data["argv"] == [
            str(resolved_tank.tank_command), "Asset", "42", "maya_2027"
        ]

    def test_guard_scoped_to_maya_only(self, resolved_flame):
        """A live Command Port must NOT block a Flame launch — the guard is
        keyed on ``result.app == 'maya'``."""
        sg = MagicMock()
        # 1st find_one: entity → owning project; 2nd: Project → name.
        sg.find_one.side_effect = [
            {"id": 42, "project": {"type": "Project", "id": 1244}},
            {"id": 1244, "name": "MyProj"},
        ]
        params = FptLaunchAppInput(
            app="flame", entity_type="Asset", entity_id=42, dry_run=True
        )
        with patch("fpt_mcp.server.get_sg", return_value=sg), \
             patch("fpt_mcp.server.resolve_app", return_value=resolved_flame), \
             patch("fpt_mcp.launcher._local_flame_projects_with_paths",
                   return_value=([("MyProj", "/var/opt/proj/MyProj")],
                                 "stone_wire")), \
             patch("fpt_mcp.launcher._read_fpt_link",
                   side_effect=lambda n, p: "MyProj"), \
             patch("fpt_mcp.launcher._flame_running", return_value=False), \
             patch("fpt_mcp.launcher._maya_command_port_open",
                   return_value=True):
            data = json.loads(_run(fpt_launch_app_tool(params)))
        assert "error" not in data
        assert "--start-project=MyProj" in data["argv"]
