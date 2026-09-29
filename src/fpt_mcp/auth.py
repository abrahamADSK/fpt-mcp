"""Authenticate as the signed-in human rather than as the shared API Script.

The API Script key acts with one service identity for everyone: ShotGrid's event
log attributes every write to the script, and anything able to reach this server
inherits the script's full permission role. A user session fixes both — actions
carry the person's identity and their permissions, and no session means no
access at all.

Two paths, and only two:

  1. **A cached session.** Toolkit keeps it in ``~/Library/Caches/Shotgun/``,
     one level above the per-site and Desktop folders, so a single login is
     shared with Flow Production Tracking Desktop and with ``tank``.
  2. **The App Session Launcher.** Opens the site in the default browser, waits
     for approval, and returns a token. It imports no Qt at all, so it works
     identically from the Qt consoles and from a plain terminal.

What this module never does is call ``ShotgunAuthenticator.get_user()``. That
helper prints a method-selection menu and blocks on ``input()`` — harmless in a
terminal, fatal in an MCP server on stdio, where it raises EOFError and takes
the session with it.
"""

from __future__ import annotations

import logging
import os
import webbrowser
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Where a Toolkit core may live, most specific first. A pipeline configuration
# localises its own core, and that is the one the studio chose; the pip-installed
# sgtk is the portable fallback that needs no Desktop install.
_CORE_PATHS = (
    Path("/Users/Shared/FPT_MCP/setup/install/core/python"),
)


class AuthUnavailable(RuntimeError):
    """Toolkit core is not importable, so user identity cannot be resolved."""


class SiteRejectsBrowserAuth(RuntimeError):
    """The site has the App Session Launcher disabled.

    Without it there is no headless path: Toolkit falls back to a terminal
    prompt, which an MCP server cannot answer. The operator has to sign in
    through Desktop or a Qt console instead.
    """


@dataclass(frozen=True)
class Session:
    """A resolved human identity."""

    host: str
    login: str
    token: str


def _import_tank():
    """Import ``tank``, preferring a pipeline-localised core over the pip one."""
    try:
        import tank  # noqa: F401  (pip-installed sgtk)
        return tank
    except ImportError:
        pass

    import sys
    for path in _CORE_PATHS:
        if not path.is_dir():
            continue
        sys.path.insert(0, str(path))
        try:
            import tank  # noqa: F401
            return tank
        except ImportError:
            sys.path.pop(0)

    raise AuthUnavailable(
        "Toolkit core (sgtk) is not importable. Reinstall with "
        "`.venv/bin/pip install -e .`, which pulls it from Autodesk's "
        "repository, or install Flow Production Tracking Desktop."
    )


def cached_session() -> Session | None:
    """Return the cached session, or None if absent or expired.

    Never prompts. Safe to call from any context, including an MCP server.
    """
    _import_tank()
    from tank.authentication import ShotgunAuthenticator

    try:
        user = ShotgunAuthenticator().get_default_user()
    except Exception as exc:
        logger.debug("get_default_user failed: %s", exc)
        return None

    if user is None:
        return None

    try:
        if user.are_credentials_expired():
            logger.info("Cached session for %s has expired.", user.login)
            return None
    except Exception as exc:
        # An unanswerable expiry check is treated as expired: better to
        # re-authenticate than to hand out a token that will fail mid-call.
        logger.debug("Expiry check failed (%s); treating as expired.", exc)
        return None

    token = user.impl.get_session_token()
    return Session(host=user.host, login=user.login, token=token)


def browser_session(host: str) -> Session:
    """Authenticate through the browser and return the new session.

    Blocks until the user approves in the browser, or the launcher gives up.
    Requires the App Session Launcher to be enabled on the site.
    """
    _import_tank()
    from tank.authentication import app_session_launcher, site_info

    info = site_info.SiteInfo()
    info.reload(host)
    if not getattr(info, "app_session_launcher_enabled", False):
        raise SiteRejectsBrowserAuth(
            f"{host} has the App Session Launcher disabled, so there is no "
            f"browser sign-in path. Sign in through Flow Production Tracking "
            f"Desktop, then retry."
        )

    result = app_session_launcher.process(
        host, browser_open_callback=webbrowser.open
    )
    if not result:
        raise RuntimeError("Browser authentication was not completed.")

    sg_url, login, token, _metadata = result
    return Session(host=sg_url, login=login, token=token)


def ensure_session(host: str | None = None) -> Session:
    """Return a valid session, authenticating through the browser if needed.

    This is the interactive entry point — call it from the console, never from
    the MCP server, so the browser opens where a person is watching.
    """
    existing = cached_session()
    if existing is not None:
        return existing

    target = host or os.getenv("SHOTGRID_URL", "")
    if not target:
        raise RuntimeError(
            "No cached session and no host to authenticate against. "
            "Set SHOTGRID_URL."
        )
    return browser_session(target)


def session_env(session: Session) -> dict[str, str]:
    """Environment for a child process that should act as this human.

    ``client.py`` prefers these over the script key, so injecting them into the
    `claude` subprocess is what makes every downstream ShotGrid call carry the
    person's identity.
    """
    return {
        "SHOTGRID_SESSION_TOKEN": session.token,
        "SHOTGRID_LOGIN": session.login,
        "SHOTGRID_URL": session.host,
    }


def _main() -> int:
    """Sign in from a terminal: ``python -m fpt_mcp.auth``.

    Exists because the browser flow needs no Qt, so a plain terminal — a Claude
    Code session, an SSH-less shell, a first-time setup — can establish the
    session just as well as the Qt console. The token lands in the shared
    Toolkit cache, so everything else (the console, the MCP server, ``tank``,
    Desktop) picks it up afterwards without signing in again.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m fpt_mcp.auth",
        description="Authenticate with Flow Production Tracking as yourself.",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Report the cached session without authenticating.",
    )
    parser.add_argument("--host", default=None, help="Site URL (default: SHOTGRID_URL)")
    args = parser.parse_args()

    try:
        if args.status:
            session = cached_session()
            if session is None:
                print("No valid cached session. Run without --status to sign in.")
                return 1
            print(f"Signed in as {session.login} on {session.host}")
            return 0

        session = ensure_session(args.host)
    except (AuthUnavailable, SiteRejectsBrowserAuth) as exc:
        print(f"error: {exc}")
        return 2
    except Exception as exc:
        print(f"error: sign-in did not complete: {exc}")
        return 2

    print(f"Signed in as {session.login} on {session.host}")
    print("The session is cached and shared — no other component needs to sign in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
