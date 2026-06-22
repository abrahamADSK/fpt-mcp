"""Regression tests for ``project_env_override`` (Chat 69).

The Qt console must bind the spawned MCP servers to the project it was
*launched for* — the ShotGrid AMI / user-menu context carried in
``self._context["project_id"]`` — rather than the static
``SHOTGRID_PROJECT_ID`` baked into ``.env``. Otherwise every ``sg_create`` /
``sg_find`` auto-links to a hardcoded project regardless of what the user
loaded in the web (the Chat 69 incident: templates landed on the wrong
project). ``project_env_override`` builds that per-launch env override, which
``ClaudeWorker.run`` applies to the ``claude`` subprocess env (inherited by
the MCP servers it spawns at startup).
"""
from fpt_mcp.qt.claude_worker import project_env_override


def test_launch_project_id_overrides_env():
    # AMI launch with an int project id → override targets that project.
    assert project_env_override({"project_id": 1310}) == {"SHOTGRID_PROJECT_ID": "1310"}


def test_string_project_id_is_coerced():
    # The AMI/CLI may hand the id over as a string; it must still be honoured.
    assert project_env_override({"project_id": "1310"}) == {"SHOTGRID_PROJECT_ID": "1310"}


def test_no_project_id_yields_no_override():
    # Standalone launch (fpt-console, no AMI context): the .env value stands.
    assert project_env_override({}) == {}
    assert project_env_override(None) == {}
    assert project_env_override({"entity_type": "Asset", "entity_id": 1}) == {}


def test_malformed_project_id_is_ignored():
    # A non-numeric or zero id must never crash the worker nor set a bogus var
    # (client.py parses SHOTGRID_PROJECT_ID with int(); 0 means "unset").
    assert project_env_override({"project_id": "not-a-number"}) == {}
    assert project_env_override({"project_id": 0}) == {}
