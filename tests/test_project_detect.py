"""Tests for the console-side recent-project detector (Chat 69).

``detect_recent_project`` resolves a smart default for ``SHOTGRID_PROJECT_ID``
at console launch from the user's most recent ``EventLogEntry`` with a project.
Creds come from ``_resolve_creds`` (the console keeps them out of ``os.environ``,
so the detector re-reads ``.env``). It must be strictly best-effort (never raise)
and skip events without a project.
"""
import fpt_mcp.qt.project_detect as pd


class _FakeSG:
    def __init__(self, user, events):
        self._user = user
        self._events = events

    def find_one(self, *a, **k):
        return self._user

    def find(self, *a, **k):
        return self._events


_VALID_CREDS = {
    "SHOTGRID_URL": "https://x.shotgrid.autodesk.com",
    "SHOTGRID_SCRIPT_NAME": "s",
    "SHOTGRID_SCRIPT_KEY": "k",
}


def _patch(monkeypatch, user, events, creds=_VALID_CREDS):
    monkeypatch.setattr(pd, "_resolve_creds", lambda: dict(creds))
    import shotgun_api3
    monkeypatch.setattr(shotgun_api3, "Shotgun", lambda *a, **k: _FakeSG(user, events))


def test_returns_first_event_project(monkeypatch):
    _patch(monkeypatch, {"id": 88}, [
        {"project": {"type": "Project", "id": 1310, "name": "sandbox"}},
    ])
    assert pd.detect_recent_project("abraham") == {"id": 1310, "name": "sandbox"}


def test_skips_events_without_project(monkeypatch):
    _patch(monkeypatch, {"id": 88}, [
        {"project": None},
        {"project": None},
        {"project": {"type": "Project", "id": 1244, "name": "real"}},
    ])
    assert pd.detect_recent_project("abraham") == {"id": 1244, "name": "real"}


def test_no_user_returns_none(monkeypatch):
    _patch(monkeypatch, None, [])
    assert pd.detect_recent_project("ghost") is None


def test_no_project_events_returns_none(monkeypatch):
    _patch(monkeypatch, {"id": 88}, [{"project": None}])
    assert pd.detect_recent_project("abraham") is None


def test_missing_creds_returns_none(monkeypatch):
    # _resolve_creds yields nothing → bail before any network call.
    monkeypatch.setattr(pd, "_resolve_creds", lambda: {})
    assert pd.detect_recent_project("abraham") is None


def test_empty_login_returns_none():
    # Guard runs before creds resolution; no patching needed.
    assert pd.detect_recent_project("") is None


def test_exception_is_swallowed(monkeypatch):
    monkeypatch.setattr(pd, "_resolve_creds", lambda: dict(_VALID_CREDS))
    import shotgun_api3

    def _boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(shotgun_api3, "Shotgun", _boom)
    assert pd.detect_recent_project("abraham") is None
