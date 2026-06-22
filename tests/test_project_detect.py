"""Tests for the console-side recent-project detector (Chat 69).

``detect_recent_project`` resolves a smart default for ``SHOTGRID_PROJECT_ID``
at console launch from the user's most recent ``EventLogEntry`` with a project.
It must be strictly best-effort (never raise) and skip events without a project.
"""
import fpt_mcp.qt.project_detect as pd


class _FakeSG:
    def __init__(self, user, events):
        self._user = user
        self._events = events

    def find_one(self, entity_type, filters, fields):
        return self._user

    def find(self, entity_type, filters, fields, order=None, limit=None):
        return self._events


def _creds(monkeypatch):
    monkeypatch.setenv("SHOTGRID_URL", "https://x.shotgrid.autodesk.com")
    monkeypatch.setenv("SHOTGRID_SCRIPT_NAME", "s")
    monkeypatch.setenv("SHOTGRID_SCRIPT_KEY", "k")


def _patch_sg(monkeypatch, user, events):
    import shotgun_api3
    monkeypatch.setattr(shotgun_api3, "Shotgun", lambda *a, **k: _FakeSG(user, events))


def test_returns_first_event_project(monkeypatch):
    _creds(monkeypatch)
    _patch_sg(monkeypatch, {"id": 88}, [
        {"project": {"type": "Project", "id": 1310, "name": "sandbox"}},
    ])
    assert pd.detect_recent_project("abraham") == {"id": 1310, "name": "sandbox"}


def test_skips_events_without_project(monkeypatch):
    _creds(monkeypatch)
    _patch_sg(monkeypatch, {"id": 88}, [
        {"project": None},
        {"project": None},
        {"project": {"type": "Project", "id": 1244, "name": "real"}},
    ])
    assert pd.detect_recent_project("abraham") == {"id": 1244, "name": "real"}


def test_no_user_returns_none(monkeypatch):
    _creds(monkeypatch)
    _patch_sg(monkeypatch, None, [])
    assert pd.detect_recent_project("ghost") is None


def test_no_project_events_returns_none(monkeypatch):
    _creds(monkeypatch)
    _patch_sg(monkeypatch, {"id": 88}, [{"project": None}])
    assert pd.detect_recent_project("abraham") is None


def test_missing_creds_returns_none(monkeypatch):
    for k in ("SHOTGRID_URL", "SHOTGRID_SCRIPT_NAME", "SHOTGRID_SCRIPT_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert pd.detect_recent_project("abraham") is None


def test_empty_login_returns_none(monkeypatch):
    _creds(monkeypatch)
    assert pd.detect_recent_project("") is None


def test_exception_is_swallowed(monkeypatch):
    _creds(monkeypatch)
    import shotgun_api3

    def _boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(shotgun_api3, "Shotgun", _boom)
    assert pd.detect_recent_project("abraham") is None
