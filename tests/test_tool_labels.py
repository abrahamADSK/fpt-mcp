"""Bucket E — Structural test: install.sh TOOLS list vs @mcp.tool registrations.

Ensures the TOOLS list in install.sh (Step 6 — pre-approve MCP tools)
matches exactly the set of @mcp.tool(name="...") decorators found in the
source code. A mismatch means either:
  - A tool was added to server.py but not to install.sh (users get
    permission prompts on first use).
  - A tool was removed from server.py but left in install.sh (dead
    permission entry, harmless but confusing).

Both directions are tested. No ShotGrid connection or MCP SDK required.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"
SRC_DIR = REPO_ROOT / "src"


def _extract_install_sh_tools() -> set[str]:
    """Parse the TOOLS=[ ... ] array from install.sh and return bare tool names.

    The install.sh TOOLS list contains bare tool names (e.g. 'sg_find') which
    are prefixed with 'mcp__fpt-mcp__' at runtime. We extract only the bare
    names for comparison against @mcp.tool registrations.
    """
    content = INSTALL_SH.read_text(encoding="utf-8")

    # The TOOLS array spans multiple lines:
    #   TOOLS = [
    #       "sg_find", "sg_create", ...
    #   ]
    # We capture everything between TOOLS = [ and the closing ]
    match = re.search(
        r'TOOLS\s*=\s*\[\s*(.*?)\s*\]',
        content,
        re.DOTALL,
    )
    assert match, "Could not find TOOLS=[ ... ] array in install.sh"

    raw = match.group(1)
    # Extract all double-quoted strings
    tools = set(re.findall(r'"([^"]+)"', raw))
    assert tools, "TOOLS array in install.sh is empty"
    return tools


def _extract_mcp_tool_names() -> set[str]:
    """Scan all .py files under src/ for @mcp.tool(name="...") and return tool names."""
    tools: set[str] = set()
    for py_file in SRC_DIR.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r'@mcp\.tool\(name="([^"]+)"\)', content):
            tools.add(match.group(1))
    assert tools, "No @mcp.tool registrations found in src/"
    return tools


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestToolLabelsSync:
    """Verify install.sh TOOLS list matches @mcp.tool registrations in source."""

    @pytest.fixture(scope="class")
    def install_tools(self) -> set[str]:
        return _extract_install_sh_tools()

    @pytest.fixture(scope="class")
    def source_tools(self) -> set[str]:
        return _extract_mcp_tool_names()

    def test_install_sh_exists(self):
        """install.sh must exist at repo root."""
        assert INSTALL_SH.is_file(), f"install.sh not found at {INSTALL_SH}"

    def test_no_tools_in_install_missing_from_source(self, install_tools, source_tools):
        """Every tool in install.sh TOOLS must have a @mcp.tool in source."""
        extra = install_tools - source_tools
        assert not extra, (
            f"install.sh lists tools not found in source: {sorted(extra)}. "
            f"Remove them from install.sh or add @mcp.tool registrations."
        )

    def test_no_tools_in_source_missing_from_install(self, install_tools, source_tools):
        """Every @mcp.tool in source must be listed in install.sh TOOLS."""
        missing = source_tools - install_tools
        assert not missing, (
            f"Source has @mcp.tool registrations not in install.sh: {sorted(missing)}. "
            f"Add them to the TOOLS array in install.sh (Step 6)."
        )

    def test_exact_match(self, install_tools, source_tools):
        """The two sets must be identical."""
        assert install_tools == source_tools, (
            f"install.sh TOOLS and @mcp.tool registrations diverge.\n"
            f"  Only in install.sh: {sorted(install_tools - source_tools)}\n"
            f"  Only in source:     {sorted(source_tools - install_tools)}"
        )

    def test_install_sh_tool_count(self, install_tools, source_tools):
        """Sanity check: both sets should have the expected number of tools."""
        # As of 2026-04-16: 14 @mcp.tool registrations
        # (6 direct SG + fpt_bulk + fpt_reporting + 2 Toolkit + fpt_launch_app
        #  + search_sg_docs + learn_pattern + session_stats)
        assert len(source_tools) >= 14, (
            f"Expected at least 14 @mcp.tool registrations, found {len(source_tools)}"
        )

    def test_install_sh_uses_correct_prefix(self):
        """The install.sh must build tool names with the mcp__fpt-mcp__ prefix."""
        content = INSTALL_SH.read_text(encoding="utf-8")
        assert "mcp__fpt-mcp__" in content, (
            "install.sh does not use the 'mcp__fpt-mcp__' prefix for tool names"
        )
