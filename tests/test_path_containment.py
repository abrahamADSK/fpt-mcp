"""
test_path_containment.py
========================
Tests for the write-path containment guard (``src/fpt_mcp/paths.py``) and its
wiring into the two file-writing tools (``tk_publish``, ``sg_download``).

Coverage:
  1. TestEnsureWithinRoots         — the core containment predicate
  2. TestResolveAllowedRoots       — discovered ∪ env allowlist assembly
  3. TestStrictPathsFlag           — FPT_MCP_STRICT_PATHS env toggle
  4. TestEnforceWriteContainment   — WARN/STRICT policy at the chokepoint
  5. TestTkPublishContainment      — tk_publish integration (Mode 2)
  6. TestSgDownloadContainment     — sg_download integration

Policy under test (proposal: WARN-by-default override):
  * contained destination               → write proceeds
  * outside + default (WARN)             → logged warning, write ALLOWED
  * outside + FPT_MCP_STRICT_PATHS=1     → {"error": ...}, write REFUSED
  * FPT_MCP_ALLOWED_WRITE_ROOTS honored  → a root listed there contains writes

All tests run offline (no ShotGrid connection required).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from fpt_mcp.paths import (
    PathContainmentError,
    ensure_within_roots,
    enforce_write_containment,
    is_strict_paths,
    resolve_allowed_roots,
)
from fpt_mcp.server import SgDownloadInput, TkPublishInput, sg_download_tool, tk_publish_tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine in a fresh event loop."""
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_containment_env(monkeypatch):
    """Start every test from a known env state.

    The guard reads FPT_MCP_STRICT_PATHS / FPT_MCP_ALLOWED_WRITE_ROOTS at call
    time, so leaking either between tests (or from a dev .env) would make the
    WARN/STRICT assertions flaky. Each test sets what it needs explicitly.
    """
    monkeypatch.delenv("FPT_MCP_STRICT_PATHS", raising=False)
    monkeypatch.delenv("FPT_MCP_ALLOWED_WRITE_ROOTS", raising=False)


def _make_publish_input(**overrides) -> TkPublishInput:
    """Build a Mode-2 TkPublishInput with sensible defaults."""
    defaults = {
        "entity_type": "Asset",
        "entity_id": 1001,
        "publish_type": "Maya Scene",
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


# ===========================================================================
# 1. TestEnsureWithinRoots — the core predicate
# ===========================================================================

class TestEnsureWithinRoots:
    """os.path.realpath + is_relative_to containment, escape detection."""

    def test_contained_path_returns_realpath(self, tmp_path):
        root = tmp_path / "project"
        root.mkdir()
        target = root / "publish" / "hero_v001.ma"  # leaf need not exist

        result = ensure_within_roots(target, [root])

        assert result == Path(os.path.realpath(str(target)))

    def test_dotdot_traversal_escape_raises(self, tmp_path):
        root = tmp_path / "project"
        root.mkdir()
        escape = root / ".." / ".." / "etc" / "passwd"

        with pytest.raises(PathContainmentError):
            ensure_within_roots(escape, [root])

    def test_absolute_escape_without_dotdot_raises(self, tmp_path):
        """The regex in safety.py misses this; real containment catches it."""
        root = tmp_path / "project"
        root.mkdir()

        with pytest.raises(PathContainmentError):
            ensure_within_roots("/etc/passwd", [root])

    def test_symlink_inside_root_pointing_outside_raises(self, tmp_path):
        root = tmp_path / "project"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        link = root / "escape_link"
        link.symlink_to(outside)  # symlink inside the root → out of tree
        target = link / "loot.txt"

        with pytest.raises(PathContainmentError):
            ensure_within_roots(target, [root])

    def test_symlinked_root_compared_on_realpath(self, tmp_path):
        """A symlinked root and a contained target both realpath-resolve."""
        real_root = tmp_path / "real_project"
        real_root.mkdir()
        link_root = tmp_path / "linked_project"
        link_root.symlink_to(real_root)
        target = link_root / "publish" / "x.ma"

        result = ensure_within_roots(target, [link_root])

        assert result == Path(os.path.realpath(str(target)))
        assert str(result).startswith(str(real_root))

    def test_nonexistent_leaf_under_existing_root_passes(self, tmp_path):
        root = tmp_path / "project"
        root.mkdir()
        target = root / "does" / "not" / "exist" / "yet.ma"

        result = ensure_within_roots(target, [root])

        assert str(result).startswith(str(Path(os.path.realpath(str(root)))))

    def test_multiple_roots_candidate_under_second_passes(self, tmp_path):
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        target = root_b / "publish" / "x.ma"

        result = ensure_within_roots(target, [root_a, root_b])

        assert str(result).startswith(str(Path(os.path.realpath(str(root_b)))))

    def test_empty_roots_always_raises(self, tmp_path):
        with pytest.raises(PathContainmentError):
            ensure_within_roots(tmp_path / "x.ma", [])

    def test_root_itself_is_contained(self, tmp_path):
        root = tmp_path / "project"
        root.mkdir()

        result = ensure_within_roots(root, [root])

        assert result == Path(os.path.realpath(str(root)))


# ===========================================================================
# 2. TestResolveAllowedRoots — discovered ∪ env allowlist
# ===========================================================================

class TestResolveAllowedRoots:
    """Allowed roots = discovered project_root UNION FPT_MCP_ALLOWED_WRITE_ROOTS."""

    def test_project_root_only(self, tmp_path):
        root = tmp_path / "project"
        root.mkdir()

        roots = resolve_allowed_roots(root)

        assert roots == [Path(os.path.realpath(str(root)))]

    def test_env_only_when_no_project_root(self, tmp_path, monkeypatch):
        env_root = tmp_path / "downloads"
        env_root.mkdir()
        monkeypatch.setenv("FPT_MCP_ALLOWED_WRITE_ROOTS", str(env_root))

        roots = resolve_allowed_roots(None)

        assert roots == [Path(os.path.realpath(str(env_root)))]

    def test_union_of_project_and_env(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        env_root = tmp_path / "extra"
        project.mkdir()
        env_root.mkdir()
        monkeypatch.setenv("FPT_MCP_ALLOWED_WRITE_ROOTS", str(env_root))

        roots = resolve_allowed_roots(project)

        assert Path(os.path.realpath(str(project))) in roots
        assert Path(os.path.realpath(str(env_root))) in roots

    def test_env_pathsep_separated_list(self, tmp_path, monkeypatch):
        r1 = tmp_path / "r1"
        r2 = tmp_path / "r2"
        r1.mkdir()
        r2.mkdir()
        monkeypatch.setenv(
            "FPT_MCP_ALLOWED_WRITE_ROOTS", os.pathsep.join([str(r1), str(r2)])
        )

        roots = resolve_allowed_roots(None)

        assert Path(os.path.realpath(str(r1))) in roots
        assert Path(os.path.realpath(str(r2))) in roots

    def test_duplicate_roots_collapsed(self, tmp_path, monkeypatch):
        root = tmp_path / "project"
        root.mkdir()
        monkeypatch.setenv("FPT_MCP_ALLOWED_WRITE_ROOTS", str(root))

        # The project root also appears in the env list → one entry only.
        roots = resolve_allowed_roots(root)

        assert roots == [Path(os.path.realpath(str(root)))]

    def test_empty_when_nothing_configured(self):
        assert resolve_allowed_roots(None) == []


# ===========================================================================
# 3. TestStrictPathsFlag
# ===========================================================================

class TestStrictPathsFlag:
    def test_default_is_warn(self):
        assert is_strict_paths() is False

    def test_strict_enabled_by_one(self, monkeypatch):
        monkeypatch.setenv("FPT_MCP_STRICT_PATHS", "1")
        assert is_strict_paths() is True

    def test_other_values_are_not_strict(self, monkeypatch):
        for value in ("0", "true", "yes", ""):
            monkeypatch.setenv("FPT_MCP_STRICT_PATHS", value)
            assert is_strict_paths() is False


# ===========================================================================
# 4. TestEnforceWriteContainment — WARN/STRICT policy
# ===========================================================================

class TestEnforceWriteContainment:
    def test_contained_returns_none(self, tmp_path):
        root = tmp_path / "project"
        root.mkdir()
        target = root / "x.ma"

        err = enforce_write_containment(target, [root], tool_name="tk_publish")

        assert err is None

    def test_outside_warn_mode_returns_none(self, tmp_path):
        """Default WARN: outside the root is ALLOWED (no current workflow breaks)."""
        root = tmp_path / "project"
        root.mkdir()
        outside = tmp_path / "elsewhere" / "x.ma"

        err = enforce_write_containment(outside, [root], tool_name="tk_publish")

        assert err is None

    def test_outside_strict_mode_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FPT_MCP_STRICT_PATHS", "1")
        root = tmp_path / "project"
        root.mkdir()
        outside = tmp_path / "elsewhere" / "x.ma"

        err = enforce_write_containment(outside, [root], tool_name="tk_publish")

        assert err is not None
        assert "Refused" in err
        assert "outside" in err

    def test_contained_strict_mode_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FPT_MCP_STRICT_PATHS", "1")
        root = tmp_path / "project"
        root.mkdir()
        target = root / "x.ma"

        err = enforce_write_containment(target, [root], tool_name="tk_publish")

        assert err is None

    def test_env_root_honored_strict(self, tmp_path, monkeypatch):
        """A FPT_MCP_ALLOWED_WRITE_ROOTS root contains writes even with no config."""
        monkeypatch.setenv("FPT_MCP_STRICT_PATHS", "1")
        env_root = tmp_path / "sandbox"
        env_root.mkdir()
        monkeypatch.setenv("FPT_MCP_ALLOWED_WRITE_ROOTS", str(env_root))

        roots = resolve_allowed_roots(None)
        contained = enforce_write_containment(
            env_root / "ok.ma", roots, tool_name="sg_download"
        )
        outside = enforce_write_containment(
            tmp_path / "nope.ma", roots, tool_name="sg_download"
        )

        assert contained is None
        assert outside is not None


# ===========================================================================
# 5. TestTkPublishContainment — tk_publish (Mode 2) integration
# ===========================================================================

def _patch_tk_publish_no_config():
    """Patch server deps so tk_publish runs Mode 2 (no PipelineConfiguration)."""
    async def _no_config():
        return None

    async def _find_one(entity_type, filters, fields):
        if entity_type == "PublishedFileType":
            return {"type": "PublishedFileType", "id": 6001, "code": "Maya Scene"}
        if entity_type == "Asset":
            return {"type": "Asset", "id": 1001, "code": "hero_robot"}
        if entity_type == "Task":
            return {"type": "Task", "id": 4001, "content": "Model"}
        return None

    async def _create(entity_type, data):
        return {"type": entity_type, "id": 9999, **data}

    return patch.multiple(
        "fpt_mcp.server",
        _get_tk_config=AsyncMock(side_effect=_no_config),
        sg_find_one=AsyncMock(side_effect=_find_one),
        sg_create=AsyncMock(side_effect=_create),
        PROJECT_ID=123,
    )


class TestTkPublishContainment:
    """Mode 2 publish honors the containment policy at the copy site."""

    def test_contained_path_passes_strict(self, tmp_path, monkeypatch):
        """(a) A path under an allowed root copies even in STRICT mode."""
        monkeypatch.setenv("FPT_MCP_STRICT_PATHS", "1")
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        monkeypatch.setenv("FPT_MCP_ALLOWED_WRITE_ROOTS", str(sandbox))

        source = tmp_path / "src.ma"
        source.write_text("// scene")
        publish_path = sandbox / "publish" / "hero_v001.ma"

        params = _make_publish_input(local_path=str(source), publish_path=str(publish_path))
        with _patch_tk_publish_no_config():
            result = json.loads(_run(tk_publish_tool(params)))

        assert "error" not in result, result
        assert publish_path.exists()
        assert publish_path.read_text() == "// scene"
        assert result["file_copied"] is True

    def test_escape_strict_refuses_and_does_not_write(self, tmp_path, monkeypatch):
        """(b) STRICT: an escaping path returns {"error":...} and writes nothing."""
        monkeypatch.setenv("FPT_MCP_STRICT_PATHS", "1")
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        monkeypatch.setenv("FPT_MCP_ALLOWED_WRITE_ROOTS", str(sandbox))

        source = tmp_path / "src.ma"
        source.write_text("// scene")
        outside = tmp_path / "outside" / "evil" / "x.ma"

        params = _make_publish_input(local_path=str(source), publish_path=str(outside))
        with _patch_tk_publish_no_config():
            result = json.loads(_run(tk_publish_tool(params)))

        assert "error" in result
        assert "Refused" in result["error"]
        # The intermediate directory chain must NOT have been fabricated.
        assert not (tmp_path / "outside").exists()

    def test_escape_warn_default_allows_and_writes(self, tmp_path, monkeypatch):
        """(c) Default WARN: an escaping path is allowed and the file is copied."""
        # No FPT_MCP_STRICT_PATHS → WARN default.
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        monkeypatch.setenv("FPT_MCP_ALLOWED_WRITE_ROOTS", str(sandbox))

        source = tmp_path / "src.ma"
        source.write_text("// scene")
        outside = tmp_path / "outside" / "x.ma"

        params = _make_publish_input(local_path=str(source), publish_path=str(outside))
        with _patch_tk_publish_no_config():
            result = json.loads(_run(tk_publish_tool(params)))

        assert "error" not in result, result
        assert outside.exists()
        assert outside.read_text() == "// scene"

    def test_env_root_honored(self, tmp_path, monkeypatch):
        """(d) FPT_MCP_ALLOWED_WRITE_ROOTS alone contains a Mode-2 publish (strict)."""
        monkeypatch.setenv("FPT_MCP_STRICT_PATHS", "1")
        sandbox = tmp_path / "declared_tree"
        sandbox.mkdir()
        monkeypatch.setenv("FPT_MCP_ALLOWED_WRITE_ROOTS", str(sandbox))

        source = tmp_path / "src.ma"
        source.write_text("// scene")
        publish_path = sandbox / "nested" / "deep" / "hero_v001.ma"

        params = _make_publish_input(local_path=str(source), publish_path=str(publish_path))
        with _patch_tk_publish_no_config():
            result = json.loads(_run(tk_publish_tool(params)))

        assert "error" not in result, result
        assert publish_path.exists()


# ===========================================================================
# 6. TestSgDownloadContainment — sg_download integration
# ===========================================================================

def _patch_sg_download(write_sink):
    """Patch server deps for sg_download.

    ``write_sink`` is a fake download that records the path it would write and
    actually creates the file, so tests can assert whether a write happened.
    """
    async def _find_one(entity_type, filters, fields):
        return {"type": entity_type, "id": 1, "image": "http://example/thumb.png"}

    return patch.multiple(
        "fpt_mcp.server",
        sg_find_one=AsyncMock(side_effect=_find_one),
        sg_download_attachment=AsyncMock(side_effect=write_sink),
    )


def _make_download_input(download_path: str) -> SgDownloadInput:
    return SgDownloadInput(
        entity_type="Version", entity_id=1, field_name="image",
        download_path=download_path,
    )


class TestSgDownloadContainment:
    """sg_download honors the containment policy before writing the file."""

    @staticmethod
    async def _fake_download(attachment, file_path):
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("downloaded")
        return file_path

    def test_contained_path_passes(self, tmp_path, monkeypatch):
        """(a) A download under an allowed root proceeds (strict)."""
        monkeypatch.setenv("FPT_MCP_STRICT_PATHS", "1")
        sandbox = tmp_path / "downloads"
        sandbox.mkdir()
        monkeypatch.setenv("FPT_MCP_ALLOWED_WRITE_ROOTS", str(sandbox))
        dest = sandbox / "thumb.png"

        params = _make_download_input(str(dest))
        with _patch_sg_download(self._fake_download):
            result = json.loads(_run(sg_download_tool(params)))

        assert "error" not in result, result
        assert dest.exists()

    def test_escape_strict_refuses_and_does_not_write(self, tmp_path, monkeypatch):
        """(b) STRICT: an escaping download returns {"error":...} and writes nothing."""
        monkeypatch.setenv("FPT_MCP_STRICT_PATHS", "1")
        sandbox = tmp_path / "downloads"
        sandbox.mkdir()
        monkeypatch.setenv("FPT_MCP_ALLOWED_WRITE_ROOTS", str(sandbox))
        outside = tmp_path / "outside" / "loot.png"

        download_calls: list[str] = []

        async def _tracking_download(attachment, file_path):
            download_calls.append(file_path)
            return await self._fake_download(attachment, file_path)

        params = _make_download_input(str(outside))
        with _patch_sg_download(_tracking_download):
            result = json.loads(_run(sg_download_tool(params)))

        assert "error" in result
        assert "Refused" in result["error"]
        assert download_calls == []  # the write primitive was never reached
        assert not (tmp_path / "outside").exists()

    def test_dotdot_escape_strict_refuses(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FPT_MCP_STRICT_PATHS", "1")
        sandbox = tmp_path / "downloads"
        sandbox.mkdir()
        monkeypatch.setenv("FPT_MCP_ALLOWED_WRITE_ROOTS", str(sandbox))
        traversal = sandbox / ".." / ".." / "etc" / "x.png"

        params = _make_download_input(str(traversal))
        with _patch_sg_download(self._fake_download):
            result = json.loads(_run(sg_download_tool(params)))

        assert "error" in result
        assert "Refused" in result["error"]

    def test_escape_warn_default_allows_and_writes(self, tmp_path, monkeypatch):
        """(c) Default WARN: an escaping download is allowed and written."""
        sandbox = tmp_path / "downloads"
        sandbox.mkdir()
        monkeypatch.setenv("FPT_MCP_ALLOWED_WRITE_ROOTS", str(sandbox))
        outside = tmp_path / "outside" / "thumb.png"

        params = _make_download_input(str(outside))
        with _patch_sg_download(self._fake_download):
            result = json.loads(_run(sg_download_tool(params)))

        assert "error" not in result, result
        assert outside.exists()
