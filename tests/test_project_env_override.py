"""Regression tests for ``project_env_override`` (Chat 69, option B).

The Qt console must NEVER silently operate on the static ``.env`` project. It
binds the spawned MCP servers to the project it was *launched for* (the ShotGrid
AMI / user-menu context in ``self._context["project_id"]``); when no project was
passed it forces ``SHOTGRID_PROJECT_ID="0"`` ("no project") so a project-scoped
``sg_create`` fails instead of writing to a default, and the SYSTEM_PROMPT
project-context gate makes the assistant ask the user which project to use.
``project_env_override`` builds that override, applied in ``ClaudeWorker.run``;
``client.py`` restores an injected value after its ``load_dotenv(override=True)``
so it wins over ``.env``.
"""
from fpt_mcp.qt.claude_worker import project_env_override


def test_valid_launch_project_id_binds_that_project():
    # AMI fired from within a project → bind to that project.
    assert project_env_override({"project_id": 1310}) == {"SHOTGRID_PROJECT_ID": "1310"}


def test_string_project_id_is_coerced():
    # The AMI/CLI may hand the id over as a string; still honoured.
    assert project_env_override({"project_id": "1310"}) == {"SHOTGRID_PROJECT_ID": "1310"}


def test_no_project_id_forces_no_project_sentinel():
    # Global user menu or standalone launch → "0", NEVER the .env default.
    assert project_env_override({}) == {"SHOTGRID_PROJECT_ID": "0"}
    assert project_env_override(None) == {"SHOTGRID_PROJECT_ID": "0"}
    assert project_env_override({"entity_type": "Asset", "entity_id": 1}) == {"SHOTGRID_PROJECT_ID": "0"}


def test_malformed_or_zero_project_id_forces_sentinel():
    # A non-numeric, zero or negative id must resolve to "no project", not crash
    # and not leak a bogus value (client.py parses SHOTGRID_PROJECT_ID with int()).
    assert project_env_override({"project_id": "not-a-number"}) == {"SHOTGRID_PROJECT_ID": "0"}
    assert project_env_override({"project_id": 0}) == {"SHOTGRID_PROJECT_ID": "0"}
    assert project_env_override({"project_id": -5}) == {"SHOTGRID_PROJECT_ID": "0"}
