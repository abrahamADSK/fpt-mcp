"""sg_errors.py — translate shotgun_api3 / connection exceptions into the
repo's standard structured-error JSON shape at the tool boundary.

Why this module exists
----------------------
`shotgun_api3` raises a small family of exceptions on auth, connectivity and
protocol failures (`AuthenticationFault`, `Fault`,
`MissingTwoFactorAuthenticationFault`, `ProtocolError`, plus
`socket`/`urllib`/SSL errors). Until now those propagated **uncaught** out of
every ShotGrid tool; FastMCP re-wrapped them as a generic
``ToolError("Error executing tool <name>: <str(exc)>")`` (protocol error
``-32042``). The model received an opaque one-line string with no
machine-readable classification: no ``error_type``, no actionable ``hint``,
no ``retryable`` flag.

This module closes that gap. `sg_errors_to_json` is an async decorator applied
to the str-returning ``*_impl`` / ``*_do_*`` tool-boundary functions. On a
recognised fault it returns ``json.dumps(to_structured_error(exc))`` — a
``{error, error_type, hint, retryable}`` object that reuses the existing
top-level ``error`` key so `_session_stats.classify_result_error` counts it as
a failed turn and `suggestions.maybe_annotate_with_suggestions` short-circuits
on it, exactly like today's ``ValidationError`` / ``TkConfigError`` paths.

Design boundaries (per proposals/fpt-sg-fault-to-json.md, Option A):
  * `client._sg_call` is left UNTOUCHED. It returns raw SDK data mid-pipeline
    (a ``list``/``dict``/``int`` its callers consume directly), so catching
    there would corrupt the data contract. It keeps logging the full traceback
    and re-raising; this module only *serialises* the caught exception where a
    JSON **string** is the legitimate return value.
  * Unrecognised exceptions are **re-raised** (`to_structured_error` returns
    ``None``) so a genuine bug (`KeyError`, `TypeError`) keeps its traceback
    and is never silently swallowed.
  * ``retryable`` is an **advisory** label only — there is no auto-retry.
  * OPSEC: the echoed server message is scrubbed of credential-shaped tokens
    and truncated to 300 chars (defence in depth — see `_safe_msg`).

Scope: fpt-specific for now. Porting the decorator to flame-mcp / maya-mcp as a
shared exception->JSON helper is a documented follow-up (proposal §9.6).
"""

from __future__ import annotations

import functools
import json
import re
import socket
import ssl
import urllib.error
from typing import Any, Awaitable, Callable

import shotgun_api3 as sg

# Cap on how much raw server/exception text is echoed back to the model.
_MAX_MSG = 300

# Redact credential-shaped values embedded in a free-text exception message.
# The common faults do NOT leak the key (e.g. AuthenticationFault is
# "API read() invalid script_name or api_key" — it names the fields, not the
# value), but `Fault` echoes arbitrary server text and `URLError` can embed a
# full URL, so we scrub defensively. The key tokens mirror logging_config's
# _SECRET_KEY_RE; longer alternatives are listed first so e.g. ``script_key``
# is matched whole rather than as a trailing ``key``. Only a ``key=value`` /
# ``key: value`` shape is redacted, so a helpful message that merely *names*
# a field ("... or api_key") is left intact.
_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(api_key|script_key|password|secret|token|key)\b(\s*[=:]\s*)(\S+)"
)


def _safe_msg(exc: BaseException) -> str:
    """Scrub credential-shaped tokens and truncate the exception text.

    Scrub first, then truncate, so a secret near the 300-char boundary cannot
    be left half-exposed by the cut.
    """
    text = str(exc) or exc.__class__.__name__
    text = _SECRET_VALUE_RE.sub(r"\1\2***redacted***", text)
    return text[:_MAX_MSG]


# (exception type, error_type, retryable, static hint). Ordered MOST-SPECIFIC
# FIRST: `isinstance` walks this list top-down, so every subclass must precede
# its base.
#
# shotgun_api3 taxonomy (verified live against 3.10.0):
#   ShotgunError
#   ├── Fault
#   │   ├── AuthenticationFault
#   │   ├── MissingTwoFactorAuthenticationFault
#   │   └── UserCredentialsNotAllowedForSSOAuthenticationFault
#   └── ShotgunFileDownloadError
#   Error
#   ├── ProtocolError
#   └── ResponseError
#
# Connection-stack exceptions are all OSError subclasses. NOTE: in Python 3
# `EnvironmentError` IS `OSError`, so its rule MUST come LAST — the specific
# OSError subclasses (SSLError / TimeoutError / URLError / ConnectionError)
# are matched before the catch-all `EnvironmentError` rule that folds the
# `_validate_config` config error into the same shape.
_RULES: list[tuple[type[BaseException], str, bool, str]] = [
    (
        sg.MissingTwoFactorAuthenticationFault,
        "two_factor_required",
        False,
        "This ShotGrid site requires two-factor auth; script-key auth cannot "
        "satisfy 2FA. Use a script exempt from 2FA, or contact your SG admin.",
    ),
    (
        sg.UserCredentialsNotAllowedForSSOAuthenticationFault,
        "sso_credentials_rejected",
        False,
        "The site enforces SSO and rejected script credentials. Verify the "
        "script is allowed for API access in SG Admin -> Scripts.",
    ),
    (
        sg.AuthenticationFault,
        "authentication_failed",
        False,
        "ShotGrid rejected the credentials. Check SHOTGRID_SCRIPT_NAME / "
        "SHOTGRID_SCRIPT_KEY in .env (SG Admin -> Scripts); the script may "
        "have been disabled or its key rotated.",
    ),
    (
        sg.ProtocolError,
        "protocol_error",
        True,
        "ShotGrid returned an HTTP/protocol error (often a transient 5xx or "
        "proxy issue). Retry shortly; if it persists, check the SG site status.",
    ),
    (
        sg.ResponseError,
        "malformed_response",
        True,
        "ShotGrid returned an unexpected/malformed response. Usually transient "
        "— retry; check for a proxy rewriting traffic.",
    ),
    (
        sg.ShotgunFileDownloadError,
        "download_failed",
        True,
        "The attachment download failed. Verify the attachment still exists "
        "and the entity/field reference is correct, then retry.",
    ),
    (
        sg.Fault,
        "shotgrid_api_fault",
        False,
        "ShotGrid returned an API fault. Check the entity type, field names "
        "and filter syntax — call search_sg_docs to confirm.",
    ),
    (
        ssl.SSLError,
        "ssl_error",
        False,
        "TLS handshake failed (often a wrong SHOTGRID_URL or a corporate "
        "cert). Verify the site URL and the machine's CA bundle.",
    ),
    (
        socket.timeout,  # alias of TimeoutError on Python 3.10+, listed for clarity
        "timeout",
        True,
        "ShotGrid did not respond within SHOTGRID_TIMEOUT_SECS (default 30s). "
        "The site may be slow or unreachable — retry; raise the timeout for "
        "very large queries.",
    ),
    (
        TimeoutError,
        "timeout",
        True,
        "ShotGrid timed out. Retry; raise SHOTGRID_TIMEOUT_SECS for very "
        "large queries.",
    ),
    (
        urllib.error.URLError,
        "connection_error",
        True,
        "Could not reach ShotGrid (DNS/refused/network). Verify SHOTGRID_URL "
        "and connectivity, then retry.",
    ),
    (
        ConnectionError,
        "connection_error",
        True,
        "Could not reach ShotGrid (network). Verify SHOTGRID_URL and "
        "connectivity, then retry.",
    ),
    (
        # EnvironmentError is an alias of OSError in Python 3, so this MUST be
        # the last rule. It folds the rich EnvironmentError raised by
        # client._validate_config (missing/placeholder credentials) into the
        # structured shape. hint is intentionally empty so `hint or msg` passes
        # the rich self-explanatory message through verbatim (truncated) rather
        # than attaching credential advice that would be wrong for a stray
        # non-config OSError that happens to reach here.
        EnvironmentError,
        "config_error",
        False,
        "",
    ),
]


def to_structured_error(exc: BaseException) -> dict[str, Any] | None:
    """Map a recognised SG / connection exception to the structured shape.

    Returns ``{error, error_type, hint, retryable}`` for a mapped exception, or
    ``None`` for anything unrecognised so the caller re-raises it (a genuine
    programming error must keep its traceback, never be swallowed).
    """
    for exc_type, error_type, retryable, hint in _RULES:
        if isinstance(exc, exc_type):
            msg = _safe_msg(exc)
            return {
                "error": msg,
                "error_type": error_type,
                "hint": hint or msg,
                "retryable": retryable,
            }
    return None


def sg_errors_to_json(
    func: Callable[..., Awaitable[str]],
) -> Callable[..., Awaitable[str]]:
    """Decorate an async str-returning tool-boundary function.

    On a recognised shotgun_api3 / connection fault, return the structured
    error JSON; otherwise re-raise unchanged. The success path is an exact
    pass-through, so decorated tools behave identically when they succeed.
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            structured = to_structured_error(exc)
            if structured is None:
                raise  # not a recognised fault — keep the traceback
            return json.dumps(structured)

    return wrapper
