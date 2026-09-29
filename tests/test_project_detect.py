"""Tests for the console-side recent-project detector (Chat 69).

``detect_recent_project`` resolves a smart default for ``SHOTGRID_PROJECT_ID``
at console launch from the user's most recent ``EventLogEntry`` with a project.
The connection comes from ``fpt_mcp.auth.sg_connection``, which acts as the
signed-in human — there is no script key any more. It must be strictly
best-effort (never raise) and skip events without a project.
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


def _patch(monkeypatch, user, events, connect=None):
    """Stub the session-backed connection the detector now uses."""
    import fpt_mcp.auth as auth

    monkeypatch.setattr(
        auth, "sg_connection",
        connect or (lambda *a, **k: _FakeSG(user, events)),
    )


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


def test_no_session_returns_none(monkeypatch):
    """No signed-in user → None, not a crash. The console must still open."""
    import fpt_mcp.auth as auth

    def _no_session(*a, **k):
        raise RuntimeError("No valid Flow Production Tracking session.")

    monkeypatch.setattr(auth, "sg_connection", _no_session)
    assert pd.detect_recent_project("abraham") is None


def test_empty_login_returns_none():
    # Guard runs before creds resolution; no patching needed.
    assert pd.detect_recent_project("") is None


def test_exception_is_swallowed(monkeypatch):
    """Best-effort: a missing session or a dead network maps to None, not a raise."""
    import fpt_mcp.auth as auth

    def _boom(*a, **k):
        raise RuntimeError("no session")

    monkeypatch.setattr(auth, "sg_connection", _boom)
    assert pd.detect_recent_project("abraham") is None


# ── resolve_page_project (authoritative: the page → its project) ─────────────

class _FakeSGPage:
    def __init__(self, page):
        self._page = page

    def find_one(self, *a, **k):
        return self._page


def _patch_page(monkeypatch, page):
    import fpt_mcp.auth as auth

    monkeypatch.setattr(auth, "sg_connection", lambda *a, **k: _FakeSGPage(page))


def test_resolve_page_project(monkeypatch):
    _patch_page(monkeypatch, {"id": 10740, "project": {"type": "Project", "id": 1310, "name": "sandbox"}})
    assert pd.resolve_page_project(10740) == {"id": 1310, "name": "sandbox"}


def test_resolve_page_without_project_returns_none(monkeypatch):
    _patch_page(monkeypatch, {"id": 10740, "project": None})
    assert pd.resolve_page_project(10740) is None


def test_resolve_page_none_id_returns_none():
    assert pd.resolve_page_project(None) is None
    assert pd.resolve_page_project(0) is None


def test_resolve_page_without_session_returns_none(monkeypatch):
    import fpt_mcp.auth as auth

    def _no_session(*a, **k):
        raise RuntimeError("No valid Flow Production Tracking session.")

    monkeypatch.setattr(auth, "sg_connection", _no_session)
    assert pd.resolve_page_project(10740) is None
