"""
test_client_env.py
==================
Bucket E smoke tests for fpt_mcp/client.py environment variable handling.

Verifies that _validate_config() correctly detects:
  - Missing required environment variables
  - Placeholder values leaked from .env.example
  - Partial configurations (some set, some missing)
  - Valid configurations (all vars set with real values)
  - PROJECT_ID=0 falsy behavior (cross-project bug root cause)

Also verifies that _PROJECT_SCOPED_ENTITIES in server.py is non-empty
and contains the core production entities.

No ShotGrid connection or external dependencies required.
Run with:
    pytest tests/test_client_env.py -v
"""

import importlib
import os
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — reload client module under controlled env
# ---------------------------------------------------------------------------

def _reload_client_with_env(env: dict[str, str]):
    """Reload fpt_mcp.client with the given environment variables.

    Because client.py reads env vars at import time into module-level
    globals (SHOTGRID_URL, SCRIPT_NAME, SCRIPT_KEY, PROJECT_ID), we
    must reload the module to pick up new values.

    Args:
        env: Complete set of env vars to expose. Any SHOTGRID_* var not
             present in this dict will be absent from the environment.

    Returns:
        The freshly-reloaded fpt_mcp.client module.
    """
    import fpt_mcp.client as client_mod

    # Wipe all SHOTGRID_* vars, then overlay the requested ones.
    clean = {k: v for k, v in os.environ.items() if not k.startswith("SHOTGRID_")}
    clean.update(env)

    with patch.dict(os.environ, clean, clear=True):
        # Also suppress load_dotenv so .env on disk doesn't interfere
        with patch("dotenv.load_dotenv", lambda *a, **kw: None):
            importlib.reload(client_mod)

    return client_mod


# ---------------------------------------------------------------------------
# 1. Missing env vars — validation should raise
# ---------------------------------------------------------------------------

class TestMissingEnvVars:
    """When required vars are empty, _validate_config() must raise."""

    def test_all_missing(self):
        """All three required vars absent → EnvironmentError listing all."""
        client = _reload_client_with_env({})
        with pytest.raises(EnvironmentError, match="SHOTGRID_URL"):
            client._validate_config()

    def test_url_missing(self):
        """Only SHOTGRID_URL missing → EnvironmentError mentions it."""
        client = _reload_client_with_env({
            "SHOTGRID_SCRIPT_NAME": "test_script",
            "SHOTGRID_SCRIPT_KEY": "abc123def456",
        })
        with pytest.raises(EnvironmentError, match="SHOTGRID_URL"):
            client._validate_config()

    def test_script_name_missing(self):
        """Only SHOTGRID_SCRIPT_NAME missing → EnvironmentError mentions it."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://mysite.shotgrid.autodesk.com",
            "SHOTGRID_SCRIPT_KEY": "abc123def456",
        })
        with pytest.raises(EnvironmentError, match="SHOTGRID_SCRIPT_NAME"):
            client._validate_config()

    def test_script_key_missing(self):
        """Only SHOTGRID_SCRIPT_KEY missing → EnvironmentError mentions it."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://mysite.shotgrid.autodesk.com",
            "SHOTGRID_SCRIPT_NAME": "test_script",
        })
        with pytest.raises(EnvironmentError, match="SHOTGRID_SCRIPT_KEY"):
            client._validate_config()


# ---------------------------------------------------------------------------
# 2. Placeholder detection — values from .env.example should raise
# ---------------------------------------------------------------------------

class TestPlaceholderDetection:
    """When vars contain placeholder fragments from .env.example, must raise."""

    def test_placeholder_url_your_site(self):
        """SHOTGRID_URL containing 'YOUR_SITE' → detected as placeholder."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://YOUR_SITE.shotgrid.autodesk.com",
            "SHOTGRID_SCRIPT_NAME": "real_script",
            "SHOTGRID_SCRIPT_KEY": "abc123def456",
        })
        with pytest.raises(EnvironmentError, match="placeholder"):
            client._validate_config()

    def test_placeholder_url_yoursite_shotgrid(self):
        """SHOTGRID_URL containing 'yoursite.shotgrid' → detected."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://yoursite.shotgrid.autodesk.com",
            "SHOTGRID_SCRIPT_NAME": "real_script",
            "SHOTGRID_SCRIPT_KEY": "abc123def456",
        })
        with pytest.raises(EnvironmentError, match="placeholder"):
            client._validate_config()

    def test_placeholder_script_name(self):
        """SHOTGRID_SCRIPT_NAME = 'your_script_name' → detected."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://mysite.shotgrid.autodesk.com",
            "SHOTGRID_SCRIPT_NAME": "your_script_name",
            "SHOTGRID_SCRIPT_KEY": "abc123def456",
        })
        with pytest.raises(EnvironmentError, match="placeholder"):
            client._validate_config()

    def test_placeholder_script_key(self):
        """SHOTGRID_SCRIPT_KEY = 'your_script_key' → detected."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://mysite.shotgrid.autodesk.com",
            "SHOTGRID_SCRIPT_NAME": "real_script",
            "SHOTGRID_SCRIPT_KEY": "your_script_key",
        })
        with pytest.raises(EnvironmentError, match="placeholder"):
            client._validate_config()

    def test_placeholder_script_key_your_key(self):
        """SHOTGRID_SCRIPT_KEY = 'your_key' → detected."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://mysite.shotgrid.autodesk.com",
            "SHOTGRID_SCRIPT_NAME": "real_script",
            "SHOTGRID_SCRIPT_KEY": "your_key",
        })
        with pytest.raises(EnvironmentError, match="placeholder"):
            client._validate_config()

    def test_placeholder_case_insensitive(self):
        """Placeholder detection is case-insensitive."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://YOUR_site.shotgrid.autodesk.com",
            "SHOTGRID_SCRIPT_NAME": "Your_Script_Name",
            "SHOTGRID_SCRIPT_KEY": "YOUR_KEY",
        })
        with pytest.raises(EnvironmentError, match="placeholder"):
            client._validate_config()


# ---------------------------------------------------------------------------
# 3. PROJECT_ID=0 behavior — the cross-project bug root cause
# ---------------------------------------------------------------------------

class TestProjectIdZero:
    """PROJECT_ID defaults to 0 when unset; 0 is falsy in Python."""

    def test_project_id_zero_when_unset(self):
        """When SHOTGRID_PROJECT_ID is not in env, PROJECT_ID == 0."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://mysite.shotgrid.autodesk.com",
            "SHOTGRID_SCRIPT_NAME": "real_script",
            "SHOTGRID_SCRIPT_KEY": "abc123def456",
        })
        assert client.PROJECT_ID == 0

    def test_project_id_zero_is_falsy(self):
        """PROJECT_ID=0 evaluates as falsy — this is the cross-project guard."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://mysite.shotgrid.autodesk.com",
            "SHOTGRID_SCRIPT_NAME": "real_script",
            "SHOTGRID_SCRIPT_KEY": "abc123def456",
        })
        assert not client.PROJECT_ID

    def test_project_id_nonzero_is_truthy(self):
        """When SHOTGRID_PROJECT_ID is set to a real ID, PROJECT_ID is truthy."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://mysite.shotgrid.autodesk.com",
            "SHOTGRID_SCRIPT_NAME": "real_script",
            "SHOTGRID_SCRIPT_KEY": "abc123def456",
            "SHOTGRID_PROJECT_ID": "123",
        })
        assert client.PROJECT_ID == 123
        assert client.PROJECT_ID  # truthy

    def test_get_project_filter_empty_when_zero(self):
        """get_project_filter() returns empty dict when PROJECT_ID is 0."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://mysite.shotgrid.autodesk.com",
            "SHOTGRID_SCRIPT_NAME": "real_script",
            "SHOTGRID_SCRIPT_KEY": "abc123def456",
        })
        assert client.get_project_filter() == {}

    def test_get_project_filter_populated_when_set(self):
        """get_project_filter() returns project dict when PROJECT_ID > 0."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://mysite.shotgrid.autodesk.com",
            "SHOTGRID_SCRIPT_NAME": "real_script",
            "SHOTGRID_SCRIPT_KEY": "abc123def456",
            "SHOTGRID_PROJECT_ID": "456",
        })
        expected = {"type": "Project", "id": 456}
        assert client.get_project_filter() == expected


# ---------------------------------------------------------------------------
# 4. Valid config — all vars set with real-looking values
# ---------------------------------------------------------------------------

class TestValidConfig:
    """When all env vars are correctly set, validation should pass."""

    def test_valid_config_passes(self):
        """No exception when all three required vars have real values."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://mysite.shotgrid.autodesk.com",
            "SHOTGRID_SCRIPT_NAME": "fpt_mcp_api",
            "SHOTGRID_SCRIPT_KEY": "abc123def456ghi789",
            "SHOTGRID_PROJECT_ID": "42",
        })
        # Should not raise
        client._validate_config()

    def test_valid_config_without_project_id(self):
        """Validation passes even without PROJECT_ID (it's optional)."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://studio.shotgrid.autodesk.com",
            "SHOTGRID_SCRIPT_NAME": "pipeline_bot",
            "SHOTGRID_SCRIPT_KEY": "x9f2k4m7p1q3r5t8",
        })
        # Should not raise — PROJECT_ID is not validated by _validate_config
        client._validate_config()


# ---------------------------------------------------------------------------
# 5. Partial config — should list all missing ones
# ---------------------------------------------------------------------------

class TestPartialConfig:
    """When some vars are set and some missing, error lists all missing."""

    def test_url_and_key_missing(self):
        """Only SCRIPT_NAME set → error lists both SHOTGRID_URL and SHOTGRID_SCRIPT_KEY."""
        client = _reload_client_with_env({
            "SHOTGRID_SCRIPT_NAME": "real_script",
        })
        with pytest.raises(EnvironmentError) as exc_info:
            client._validate_config()
        msg = str(exc_info.value)
        assert "SHOTGRID_URL" in msg
        assert "SHOTGRID_SCRIPT_KEY" in msg
        # The one that IS set should NOT be in the error
        assert "SHOTGRID_SCRIPT_NAME" not in msg

    def test_only_url_set(self):
        """Only URL set → error lists SHOTGRID_SCRIPT_NAME and SHOTGRID_SCRIPT_KEY."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://mysite.shotgrid.autodesk.com",
        })
        with pytest.raises(EnvironmentError) as exc_info:
            client._validate_config()
        msg = str(exc_info.value)
        assert "SHOTGRID_SCRIPT_NAME" in msg
        assert "SHOTGRID_SCRIPT_KEY" in msg
        assert msg.count("SHOTGRID_URL") == 0  # URL is set, not missing

    def test_only_key_set(self):
        """Only KEY set → error lists SHOTGRID_URL and SHOTGRID_SCRIPT_NAME."""
        client = _reload_client_with_env({
            "SHOTGRID_SCRIPT_KEY": "abc123def456",
        })
        with pytest.raises(EnvironmentError) as exc_info:
            client._validate_config()
        msg = str(exc_info.value)
        assert "SHOTGRID_URL" in msg
        assert "SHOTGRID_SCRIPT_NAME" in msg


# ---------------------------------------------------------------------------
# 6. _PROJECT_SCOPED_ENTITIES — non-empty and contains core entities
# ---------------------------------------------------------------------------

class TestProjectScopedEntities:
    """Verify _PROJECT_SCOPED_ENTITIES is importable, non-empty, and correct."""

    def test_project_scoped_entities_non_empty(self):
        """_PROJECT_SCOPED_ENTITIES must be a non-empty frozenset."""
        from fpt_mcp.server import _PROJECT_SCOPED_ENTITIES
        assert isinstance(_PROJECT_SCOPED_ENTITIES, frozenset)
        assert len(_PROJECT_SCOPED_ENTITIES) > 0

    def test_project_scoped_entities_contains_core(self):
        """Must contain at least Asset, Shot, Sequence, Task."""
        from fpt_mcp.server import _PROJECT_SCOPED_ENTITIES
        core = {"Asset", "Shot", "Sequence", "Task"}
        missing = core - _PROJECT_SCOPED_ENTITIES
        assert not missing, (
            f"_PROJECT_SCOPED_ENTITIES is missing core entities: {missing}"
        )

    def test_project_scoped_entities_contains_production_types(self):
        """Must also contain Version, Note, PublishedFile (production essentials)."""
        from fpt_mcp.server import _PROJECT_SCOPED_ENTITIES
        production = {"Version", "Note", "PublishedFile"}
        missing = production - _PROJECT_SCOPED_ENTITIES
        assert not missing, (
            f"_PROJECT_SCOPED_ENTITIES is missing production entities: {missing}"
        )
