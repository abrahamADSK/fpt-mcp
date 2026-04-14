"""Tests for fpt_mcp.software_resolver.

Unit tests use a synthetic filesystem under ``tmp_path`` to avoid
relying on ``/Applications`` layout. Integration tests run against
the real machine and are skipped when the expected paths are absent
(Maya not installed, PipelineConfiguration not present, etc.).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from fpt_mcp.software_resolver import (
    _APP_TO_ENGINE,
    ResolvedApp,
    _os_scan_maya,
    _sg_software_enrichment,
    _toolkit_enrichment,
    resolve_app,
)


# ---------------------------------------------------------------------------
# Synthetic filesystem helpers
# ---------------------------------------------------------------------------


def _make_maya_install(root: Path, version: str) -> Path:
    """Create a fake Maya install at ``root/maya<version>/Maya.app``."""
    app = root / f"maya{version}" / "Maya.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text("<plist/>", encoding="utf-8")
    return app


def _maya_glob(root: Path) -> str:
    return str(root / "maya*" / "Maya.app")


# ---------------------------------------------------------------------------
# Layer 1 — OS scan
# ---------------------------------------------------------------------------


class TestOsScanMaya:
    def test_empty_directory_returns_nothing(self, tmp_path: Path):
        assert _os_scan_maya(_maya_glob(tmp_path)) == []

    def test_single_install(self, tmp_path: Path):
        app = _make_maya_install(tmp_path, "2027")
        hits = _os_scan_maya(_maya_glob(tmp_path))
        assert len(hits) == 1
        assert hits[0][0] == app
        assert hits[0][1] == "2027"

    def test_multiple_installs_sorted_newest_first(self, tmp_path: Path):
        _make_maya_install(tmp_path, "2024")
        _make_maya_install(tmp_path, "2027")
        _make_maya_install(tmp_path, "2025")
        hits = _os_scan_maya(_maya_glob(tmp_path))
        versions = [v for _, v in hits]
        assert versions == ["2027", "2025", "2024"]

    def test_minor_version_parses(self, tmp_path: Path):
        _make_maya_install(tmp_path, "2027.1")
        hits = _os_scan_maya(_maya_glob(tmp_path))
        assert hits[0][1] == "2027.1"

    def test_unparseable_version_does_not_crash(self, tmp_path: Path, monkeypatch):
        # Build a path that matches the glob but not the version regex.
        bogus = tmp_path / "mayaRC" / "Maya.app" / "Contents"
        bogus.mkdir(parents=True)
        # Still one numeric install so the sort function is exercised.
        _make_maya_install(tmp_path, "2027")
        hits = _os_scan_maya(_maya_glob(tmp_path))
        # Numeric version sorts ahead of unparseable one.
        assert hits[0][1] == "2027"
        assert len(hits) == 2


# ---------------------------------------------------------------------------
# Layer 2 — Toolkit enrichment
# ---------------------------------------------------------------------------


class TestToolkitEnrichment:
    def test_returns_none_when_no_pipeline_config(self):
        sg = MagicMock()
        sg.return_value = []
        assert _toolkit_enrichment(1244, sg) == (None, None)

    def test_returns_none_when_mac_path_empty(self):
        sg = MagicMock(return_value=[
            {"id": 661, "code": "Primary", "mac_path": None}
        ])
        assert _toolkit_enrichment(1244, sg) == (None, None)

    def test_returns_none_when_tank_missing(self, tmp_path: Path):
        # mac_path exists but no tank binary inside.
        (tmp_path / "config").mkdir()
        sg = MagicMock(return_value=[
            {"id": 661, "code": "Primary", "mac_path": str(tmp_path)}
        ])
        assert _toolkit_enrichment(1244, sg) == (None, None)

    def test_returns_paths_when_valid(self, tmp_path: Path):
        (tmp_path / "config").mkdir()
        tank = tmp_path / "tank"
        tank.write_text("#!/bin/sh\necho tank", encoding="utf-8")
        tank.chmod(0o755)
        sg = MagicMock(return_value=[
            {"id": 661, "code": "Primary", "mac_path": str(tmp_path)}
        ])
        root, cmd = _toolkit_enrichment(1244, sg)
        assert root == tmp_path
        assert cmd == tank

    def test_sg_exception_returns_none(self):
        sg = MagicMock(side_effect=RuntimeError("network down"))
        assert _toolkit_enrichment(1244, sg) == (None, None)


# ---------------------------------------------------------------------------
# Layer 3 — SG Software enrichment
# ---------------------------------------------------------------------------


class TestSgSoftwareEnrichment:
    def test_no_rows_returns_none(self):
        sg = MagicMock(return_value=[])
        assert _sg_software_enrichment("tk-maya", 1244, sg) is None

    def test_returns_populated_row_over_stub(self):
        stub = {"id": 3, "mac_path": None, "version_names": None}
        real = {"id": 7, "mac_path": "/Applications/Autodesk/maya2027/Maya.app",
                "version_names": ["2027"]}
        sg = MagicMock(return_value=[stub, real])
        row = _sg_software_enrichment("tk-maya", 1244, sg)
        assert row is real

    def test_returns_stub_when_nothing_populated(self):
        stub = {"id": 3, "mac_path": None, "version_names": None}
        sg = MagicMock(return_value=[stub])
        assert _sg_software_enrichment("tk-maya", 1244, sg) is stub

    def test_sg_exception_returns_none(self):
        sg = MagicMock(side_effect=RuntimeError("nope"))
        assert _sg_software_enrichment("tk-maya", 1244, sg) is None


# ---------------------------------------------------------------------------
# Public API — resolve_app
# ---------------------------------------------------------------------------


class TestResolveApp:
    def test_returns_none_when_not_installed(self, tmp_path: Path):
        assert resolve_app("maya", glob_pattern=_maya_glob(tmp_path)) is None

    def test_bare_os_result_no_sg(self, tmp_path: Path):
        _make_maya_install(tmp_path, "2027")
        result = resolve_app("maya", glob_pattern=_maya_glob(tmp_path))
        assert result is not None
        assert result.version == "2027"
        assert result.launch_method == "open"
        assert result.source_layers == ["os_scan"]
        assert result.engine is None
        assert result.tank_command is None

    def test_multiple_installs_warns(self, tmp_path: Path):
        _make_maya_install(tmp_path, "2025")
        _make_maya_install(tmp_path, "2027")
        result = resolve_app("maya", glob_pattern=_maya_glob(tmp_path))
        assert result is not None
        assert result.version == "2027"
        assert any("multiple maya installs" in w for w in result.warnings)

    def test_case_insensitive_app_name(self, tmp_path: Path):
        _make_maya_install(tmp_path, "2027")
        result = resolve_app("MAYA", glob_pattern=_maya_glob(tmp_path))
        assert result is not None
        assert result.app == "maya"

    def test_toolkit_enrichment_upgrades_launch_method(self, tmp_path: Path):
        install = tmp_path / "install"
        install.mkdir()
        _make_maya_install(install, "2027")

        config = tmp_path / "config"
        config.mkdir()
        tank = config / "tank"
        tank.write_text("#!/bin/sh", encoding="utf-8")
        tank.chmod(0o755)

        def fake_find(entity, filters, fields):
            if entity == "PipelineConfiguration":
                return [{"id": 661, "code": "Primary", "mac_path": str(config)}]
            if entity == "Software":
                return []
            return []

        result = resolve_app(
            "maya", project_id=1244, sg_find=fake_find,
            glob_pattern=_maya_glob(install),
        )
        assert result is not None
        assert result.launch_method == "tank"
        assert result.tank_command == tank
        assert result.pipeline_config_path == config
        assert "toolkit_yaml" in result.source_layers

    def test_sg_software_empty_stub_still_sets_engine(self, tmp_path: Path):
        _make_maya_install(tmp_path, "2027")
        stub = {"id": 3, "code": "Maya", "mac_path": None,
                "version_names": None, "projects": []}

        def fake_find(entity, filters, fields):
            if entity == "Software":
                return [stub]
            return []

        result = resolve_app(
            "maya", project_id=1244, sg_find=fake_find,
            glob_pattern=_maya_glob(tmp_path),
        )
        assert result is not None
        assert result.engine == "tk-maya"
        assert "sg_software" in result.source_layers
        assert any("empty stub" in w for w in result.warnings)

    def test_sg_software_populated_sets_engine_no_warning(self, tmp_path: Path):
        _make_maya_install(tmp_path, "2027")
        real = {"id": 7, "code": "Maya", "mac_path": "/somewhere",
                "version_names": ["2027"], "projects": [{"id": 1244}]}

        def fake_find(entity, filters, fields):
            if entity == "Software":
                return [real]
            return []

        result = resolve_app(
            "maya", project_id=1244, sg_find=fake_find,
            glob_pattern=_maya_glob(tmp_path),
        )
        assert result is not None
        assert result.engine == "tk-maya"
        assert not any("empty stub" in w for w in result.warnings)

    def test_sg_exception_does_not_break_os_result(self, tmp_path: Path):
        _make_maya_install(tmp_path, "2027")
        sg = MagicMock(side_effect=RuntimeError("boom"))
        result = resolve_app(
            "maya", project_id=1244, sg_find=sg,
            glob_pattern=_maya_glob(tmp_path),
        )
        assert result is not None
        assert result.version == "2027"
        # The underlying _toolkit_enrichment swallows the exception and
        # returns (None, None), so launch_method stays "open".
        assert result.launch_method == "open"
        assert "os_scan" in result.source_layers

    def test_no_engine_mapping_warning(self, tmp_path: Path, monkeypatch):
        """An installed app without an engine mapping warns but still resolves."""
        _make_maya_install(tmp_path, "2027")
        monkeypatch.delitem(_APP_TO_ENGINE, "maya")
        sg = MagicMock(return_value=[])
        result = resolve_app(
            "maya", project_id=1244, sg_find=sg,
            glob_pattern=_maya_glob(tmp_path),
        )
        assert result is not None
        assert any("no engine mapping" in w for w in result.warnings)
        assert result.engine is None


# ---------------------------------------------------------------------------
# Integration tests — real machine, skipped when prerequisites absent
# ---------------------------------------------------------------------------


_REAL_MAYA_APP = Path("/Applications/Autodesk/maya2027/Maya.app")
_REAL_PIPELINE_CONFIG = Path("/Users/Shared/FPT_MCP/setup")
_REAL_TANK = _REAL_PIPELINE_CONFIG / "tank"


@pytest.mark.skipif(
    not _REAL_MAYA_APP.exists(),
    reason="Maya 2027 not installed under /Applications/Autodesk/",
)
def test_integration_os_scan_finds_real_maya():
    hits = _os_scan_maya()
    assert hits, "expected at least one Maya install under /Applications"
    assert all(p.exists() for p, _ in hits)
    assert any(str(p).endswith("/Maya.app") for p, _ in hits)


@pytest.mark.skipif(
    not _REAL_MAYA_APP.exists(),
    reason="Maya 2027 not installed under /Applications/Autodesk/",
)
def test_integration_resolve_maya_no_sg():
    result = resolve_app("maya")
    assert result is not None
    assert result.binary.exists()
    assert result.launch_method == "open"
    assert "os_scan" in result.source_layers


@pytest.mark.skipif(
    not _REAL_TANK.exists(),
    reason="MCP_project_Abraham PipelineConfiguration not present",
)
def test_integration_toolkit_enrichment_with_fake_sg_find():
    """Exercises Layer 2 against the real tank binary using a fake sg_find
    that returns the known PipelineConfiguration row."""
    def fake_find(entity: str, filters: list, fields: list) -> list[dict[str, Any]]:
        if entity == "PipelineConfiguration":
            return [{
                "id": 661,
                "code": "Primary",
                "mac_path": str(_REAL_PIPELINE_CONFIG),
            }]
        return []

    root, cmd = _toolkit_enrichment(1244, fake_find)
    assert root == _REAL_PIPELINE_CONFIG
    assert cmd == _REAL_TANK
    assert cmd.exists()


@pytest.mark.skipif(
    not (_REAL_MAYA_APP.exists() and _REAL_TANK.exists()),
    reason="Maya or PipelineConfiguration not present",
)
def test_integration_full_resolve_maya_with_fake_sg():
    def fake_find(entity, filters, fields):
        if entity == "PipelineConfiguration":
            return [{
                "id": 661, "code": "Primary",
                "mac_path": str(_REAL_PIPELINE_CONFIG),
            }]
        if entity == "Software":
            return [{
                "id": 3, "code": "Maya", "engine": "tk-maya",
                "mac_path": None, "version_names": None, "projects": [],
            }]
        return []

    result = resolve_app("maya", project_id=1244, sg_find=fake_find)
    assert result is not None
    assert result.binary.exists()
    assert result.launch_method == "tank"
    assert result.tank_command == _REAL_TANK
    assert result.engine == "tk-maya"
    assert set(result.source_layers) == {"os_scan", "toolkit_yaml", "sg_software"}
    assert any("empty stub" in w for w in result.warnings)
