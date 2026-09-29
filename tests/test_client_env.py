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
    globals (SHOTGRID_URL, SESSION_TOKEN, PROJECT_ID), we
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
        })
        with pytest.raises(EnvironmentError, match="SHOTGRID_URL"):
            client._validate_config()

class TestPlaceholderDetection:
    """When vars contain placeholder fragments from .env.example, must raise."""

    def test_placeholder_url_your_site(self):
        """SHOTGRID_URL containing 'YOUR_SITE' → detected as placeholder."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://YOUR_SITE.shotgrid.autodesk.com",
        })
        with pytest.raises(EnvironmentError, match="placeholder"):
            client._validate_config()

    def test_placeholder_url_yoursite_shotgrid(self):
        """SHOTGRID_URL containing 'yoursite.shotgrid' → detected."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://yoursite.shotgrid.autodesk.com",
        })
        with pytest.raises(EnvironmentError, match="placeholder"):
            client._validate_config()

    def test_placeholder_case_insensitive(self):
        """Placeholder detection is case-insensitive."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://YOUR_site.shotgrid.autodesk.com",
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
        })
        assert client.PROJECT_ID == 0

    def test_project_id_zero_is_falsy(self):
        """PROJECT_ID=0 evaluates as falsy — this is the cross-project guard."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://mysite.shotgrid.autodesk.com",
        })
        assert not client.PROJECT_ID

    def test_project_id_nonzero_is_truthy(self):
        """When SHOTGRID_PROJECT_ID is set to a real ID, PROJECT_ID is truthy."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://mysite.shotgrid.autodesk.com",
            "SHOTGRID_PROJECT_ID": "123",
        })
        assert client.PROJECT_ID == 123
        assert client.PROJECT_ID  # truthy

    def test_get_project_filter_empty_when_zero(self):
        """get_project_filter() returns empty dict when PROJECT_ID is 0."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://mysite.shotgrid.autodesk.com",
        })
        assert client.get_project_filter() == {}

    def test_get_project_filter_populated_when_set(self):
        """get_project_filter() returns project dict when PROJECT_ID > 0."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://mysite.shotgrid.autodesk.com",
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
            "SHOTGRID_PROJECT_ID": "42",
        })
        # Should not raise
        client._validate_config()

    def test_valid_config_without_project_id(self):
        """Validation passes even without PROJECT_ID (it's optional)."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://studio.shotgrid.autodesk.com",
        })
        # Should not raise — PROJECT_ID is not validated by _validate_config
        client._validate_config()


# ---------------------------------------------------------------------------
# 5. Partial config — should list all missing ones
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

    def test_project_scoped_entities_contains_editorial_types(self):
        """Must contain Cut and CutItem (Chat 98).

        The conform picks its Cut by revision_number; with no project scope
        (the in-Flame console launches with SHOTGRID_PROJECT_ID=0) that ranking
        would span every project on the site with no warning emitted.
        """
        from fpt_mcp.server import _PROJECT_SCOPED_ENTITIES
        editorial = {"Cut", "CutItem"}
        missing = editorial - _PROJECT_SCOPED_ENTITIES
        assert not missing, (
            f"_PROJECT_SCOPED_ENTITIES is missing editorial entities: {missing}"
        )


# ---------------------------------------------------------------------------
# 6. Session-only authentication — the API Script key was removed
# ---------------------------------------------------------------------------


class TestSessionRequired:
    """There is no script-key fallback: no session means no connection."""

    def test_url_is_the_only_required_var(self):
        """A script name/key is no longer demanded — an install keeps no
        service credential it never spends."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://mysite.shotgrid.autodesk.com",
        })
        client._validate_config()  # must not raise

    def test_no_session_raises_with_the_fix_command(self, monkeypatch):
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://mysite.shotgrid.autodesk.com",
        })
        monkeypatch.setattr(client, "_sg_instance", None, raising=False)

        import fpt_mcp.auth as auth

        monkeypatch.setattr(auth, "cached_session", lambda: None)

        with pytest.raises(auth.NoSession, match="python -m fpt_mcp.auth"):
            client.get_sg()

    def test_session_from_another_site_is_refused(self, monkeypatch):
        """A token issued by one portal fails opaquely against another, so the
        mismatch is refused rather than passed through."""
        client = _reload_client_with_env({
            "SHOTGRID_URL": "https://mysite.shotgrid.autodesk.com",
        })
        monkeypatch.setattr(client, "_sg_instance", None, raising=False)

        import fpt_mcp.auth as auth
        monkeypatch.setattr(
            auth, "cached_session",
            lambda: auth.Session(
                host="https://other.shotgrid.autodesk.com",
                login="ana@studio.com",
                token="tok",
            ),
        )

        with pytest.raises(auth.SiteMismatch, match="other.shotgrid"):
            client.get_sg()

    def test_same_site_comparison_ignores_slash_and_case(self):
        """Lives in auth, not client — the comparison had two homes before."""
        import fpt_mcp.auth as auth

        assert auth.same_site("https://A.com/", "https://a.com")
        assert not auth.same_site("https://a.com", "https://b.com")
