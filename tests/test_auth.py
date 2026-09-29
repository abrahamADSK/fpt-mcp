"""
test_auth.py
============
Tests for fpt_mcp.auth — resolving a human identity instead of the API Script.

The interactive path is never exercised for real: `browser_session` would open
a browser and block on a person. Everything here stubs `tank` so the decision
logic is testable without Toolkit, a network, or a login.
"""

import sys
import types

import pytest

from fpt_mcp import auth


def _fake_tank(user=None, asl_enabled=True, asl_result=None):
    """Build a stub `tank` package with just the surface auth.py touches."""
    tank = types.ModuleType("tank")
    authentication = types.ModuleType("tank.authentication")

    class _Authenticator:
        def get_default_user(self):
            return user

    class _SiteInfo:
        def __init__(self):
            self.app_session_launcher_enabled = asl_enabled

        def reload(self, host, http_proxy=None):
            pass

    site_info = types.ModuleType("tank.authentication.site_info")
    site_info.SiteInfo = _SiteInfo

    launcher = types.ModuleType("tank.authentication.app_session_launcher")
    launcher.process = lambda host, browser_open_callback=None, **kw: asl_result

    # browser_session() persists through session_cache — without it the token
    # would evaporate on process exit and the next call would silently fall
    # back to the script key.
    session_cache = types.ModuleType("tank.authentication.session_cache")
    session_cache.cached = []
    session_cache.cache_session_data = (
        lambda h, user, tok, meta=None: session_cache.cached.append((h, user, tok))
    )
    session_cache.set_current_host = lambda h: None
    session_cache.set_current_user = lambda h, user: None
    session_cache.get_current_host = lambda: None
    session_cache.get_current_user = lambda host: None
    session_cache.get_recent_hosts = lambda: []
    session_cache.delete_session_data = lambda h, user: None

    authentication.ShotgunAuthenticator = _Authenticator
    authentication.site_info = site_info
    authentication.app_session_launcher = launcher
    authentication.session_cache = session_cache
    tank.authentication = authentication

    return {
        "tank": tank,
        "tank.authentication": authentication,
        "tank.authentication.site_info": site_info,
        "tank.authentication.app_session_launcher": launcher,
        "tank.authentication.session_cache": session_cache,
    }


@pytest.fixture
def stub_tank(monkeypatch):
    def _install(**kwargs):
        for name, mod in _fake_tank(**kwargs).items():
            monkeypatch.setitem(sys.modules, name, mod)
    return _install


class _User:
    def __init__(self, login, host, token, expired=False):
        self.login, self.host = login, host
        self._expired, self._token = expired, token
        self.impl = types.SimpleNamespace(get_session_token=lambda: self._token)

    def are_credentials_expired(self):
        return self._expired


class TestCachedSession:
    """The non-interactive path — must never prompt."""

    def test_returns_session_when_valid(self, stub_tank):
        stub_tank(user=_User("ana@studio.com", "https://s.shotgrid.com", "tok"))

        s = auth.cached_session()

        assert s.login == "ana@studio.com"
        assert s.token == "tok"
        assert s.host == "https://s.shotgrid.com"

    def test_none_when_no_cached_user(self, stub_tank):
        stub_tank(user=None)
        assert auth.cached_session() is None

    def test_none_when_expired(self, stub_tank):
        """An expired session must not be handed out — it fails mid-call."""
        stub_tank(user=_User("ana@studio.com", "https://s", "tok", expired=True))
        assert auth.cached_session() is None

    def test_unanswerable_expiry_check_counts_as_expired(self, stub_tank):
        class _Broken(_User):
            def are_credentials_expired(self):
                raise RuntimeError("no network")

        stub_tank(user=_Broken("ana@studio.com", "https://s", "tok"))
        assert auth.cached_session() is None


class TestBrowserSession:
    """The App Session Launcher path — Qt-free, but site-gated."""

    def test_returns_session_from_launcher(self, stub_tank):
        stub_tank(
            user=None,
            asl_result=("https://s.shotgrid.com", "ana@studio.com", "newtok", None),
        )

        s = auth.browser_session("https://s.shotgrid.com")

        assert (s.login, s.token) == ("ana@studio.com", "newtok")

    def test_raises_when_site_disables_launcher(self, stub_tank):
        """No launcher and no Qt means no headless path — say so plainly."""
        stub_tank(user=None, asl_enabled=False)

        with pytest.raises(auth.SiteRejectsBrowserAuth) as exc:
            auth.browser_session("https://s.shotgrid.com")

        assert "Desktop" in str(exc.value)

    def test_raises_when_user_does_not_complete(self, stub_tank):
        stub_tank(user=None, asl_result=None)

        with pytest.raises(RuntimeError):
            auth.browser_session("https://s.shotgrid.com")


class TestEnsureSession:
    """Cached first, browser only as a fallback."""

    def test_prefers_cache_and_does_not_open_browser(self, stub_tank):
        stub_tank(
            user=_User("ana@studio.com", "https://s", "cached"),
            asl_result=("https://s", "ana@studio.com", "SHOULD-NOT-BE-USED", None),
        )

        assert auth.ensure_session().token == "cached"

    def test_falls_back_to_browser(self, stub_tank):
        stub_tank(
            user=None,
            asl_result=("https://s", "ana@studio.com", "fresh", None),
        )

        assert auth.ensure_session(host="https://s").token == "fresh"

    def test_errors_without_host_or_cache(self, stub_tank, monkeypatch):
        monkeypatch.delenv("SHOTGRID_URL", raising=False)
        stub_tank(user=None)

        with pytest.raises(RuntimeError, match="SHOTGRID_URL"):
            auth.ensure_session()


class TestSessionEnv:
    """What gets handed to the child process."""

    def test_carries_token_login_and_host(self):
        env = auth.session_env(
            auth.Session(host="https://s", login="ana@studio.com", token="tok")
        )

        assert env["SHOTGRID_SESSION_TOKEN"] == "tok"
        assert env["SHOTGRID_LOGIN"] == "ana@studio.com"
        assert env["SHOTGRID_URL"] == "https://s"

    def test_never_carries_the_script_key(self):
        """The whole point: the child must not receive a service credential."""
        env = auth.session_env(
            auth.Session(host="https://s", login="ana@studio.com", token="tok")
        )

        assert not any("SCRIPT" in k for k in env)


class TestUnavailable:
    def test_raises_when_tank_cannot_be_imported(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "tank", None)
        monkeypatch.setattr(auth, "_CORE_PATHS", ())

        with pytest.raises(auth.AuthUnavailable, match="pip install"):
            auth.cached_session()


class TestSwitching:
    """Changing site or user — the cache holds one session per site."""

    def test_logout_deletes_the_cached_session(self, monkeypatch):
        deleted = []
        sc = types.ModuleType("tank.authentication.session_cache")
        sc.get_current_host = lambda: "https://a.shotgrid.com"
        sc.get_current_user = lambda host: "ana@studio.com"
        sc.delete_session_data = lambda h, u: deleted.append((h, u))

        mods = _fake_tank()
        mods["tank.authentication"].session_cache = sc
        mods["tank.authentication.session_cache"] = sc
        for name, mod in mods.items():
            monkeypatch.setitem(sys.modules, name, mod)

        host, login = auth.logout()

        assert deleted == [("https://a.shotgrid.com", "ana@studio.com")]
        assert (host, login) == ("https://a.shotgrid.com", "ana@studio.com")

    def test_logout_without_a_host_is_an_error(self, monkeypatch):
        sc = types.ModuleType("tank.authentication.session_cache")
        sc.get_current_host = lambda: None
        mods = _fake_tank()
        mods["tank.authentication"].session_cache = sc
        mods["tank.authentication.session_cache"] = sc
        for name, mod in mods.items():
            monkeypatch.setitem(sys.modules, name, mod)

        with pytest.raises(RuntimeError, match="nothing to sign out"):
            auth.logout()

    def test_list_marks_the_current_site(self, monkeypatch):
        """Hosts come from Toolkit's list — cache dir names cannot be un-sanitised."""
        sc = types.ModuleType("tank.authentication.session_cache")
        sc.get_current_host = lambda: "https://b.shotgrid.com"
        sc.get_recent_hosts = lambda: ["https://a.shotgrid.com", "https://b.shotgrid.com"]
        sc.get_current_user = lambda host: {
            "https://a.shotgrid.com": "ana@studio.com",
            "https://b.shotgrid.com": "bea@studio.com",
        }.get(host)

        mods = _fake_tank()
        mods["tank.authentication"].session_cache = sc
        mods["tank.authentication.session_cache"] = sc
        for name, mod in mods.items():
            monkeypatch.setitem(sys.modules, name, mod)

        rows = auth.list_sessions()

        assert ("https://a.shotgrid.com", "ana@studio.com", False) in rows
        assert ("https://b.shotgrid.com", "bea@studio.com", True) in rows


class TestPersistence:
    """The sign-in must survive process exit, or it is worthless."""

    def test_browser_session_caches_the_token(self, monkeypatch):
        """Regression: process() returns a token but caches nothing.

        Without an explicit cache_session_data the sign-in evaporated on exit
        and the next call fell back to the script key, silently.
        """
        mods = _fake_tank(
            user=None,
            asl_result=("https://s.shotgrid.com", "ana@studio.com", "tok", None),
        )
        for name, mod in mods.items():
            monkeypatch.setitem(sys.modules, name, mod)

        auth.browser_session("https://s.shotgrid.com")

        assert mods["tank.authentication.session_cache"].cached == [
            ("https://s.shotgrid.com", "ana@studio.com", "tok")
        ]
