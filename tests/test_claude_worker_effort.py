"""Offline unit tests for the Qt console effort selector.

Covers ``build_backend_env``'s effort handling: the "auto" default clears
both reasoning-hardening env vars (so the CLI uses its adaptive default),
while the fixed levels (low/medium/high/max) force adaptive thinking off
at the chosen effort.

No ShotGrid connection, MCP SDK, or PySide6 is required — these import
only the worker module's pure-Python helpers and constants.
"""

from __future__ import annotations

import pytest

from fpt_mcp.qt.claude_worker import (
    AVAILABLE_EFFORTS,
    DEFAULT_EFFORT,
    _BACKEND_ENV_KEYS,
    build_backend_env,
)

_HARDENING_KEYS = (
    "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING",
    "CLAUDE_CODE_EFFORT_LEVEL",
)


def test_default_effort_is_auto():
    """The default effort must be "auto" and be the first combo entry."""
    assert DEFAULT_EFFORT == "auto"
    assert AVAILABLE_EFFORTS[0] == ("Auto", "auto")


@pytest.mark.parametrize("level", ["low", "medium", "high", "max"])
def test_fixed_level_forces_adaptive_off(level):
    """Fixed levels set adaptive-thinking off and the chosen effort."""
    env = build_backend_env("claude-opus-4-8", "anthropic", level)
    assert env["CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING"] == "1"
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == level


def test_auto_emits_empty_sentinels():
    """"auto" keeps both keys present but empty (the scrub sentinel)."""
    env = build_backend_env("claude-opus-4-8", "anthropic", "auto")
    assert env["CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING"] == ""
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == ""


def test_default_arg_behaves_as_auto():
    """Omitting the effort arg defaults to "auto" (empty sentinels)."""
    env = build_backend_env("claude-opus-4-8", "anthropic")
    assert env["CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING"] == ""
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == ""


def test_hardening_keys_are_in_backend_env_keys():
    """Both hardening keys must be members of the scrub list."""
    for key in _HARDENING_KEYS:
        assert key in _BACKEND_ENV_KEYS


def test_auto_scrub_removes_hardening_keys():
    """Replicate run()'s scrub on the auto case: keys must disappear."""
    env = build_backend_env("claude-opus-4-8", "anthropic", "auto")
    # Same loop run() uses to treat empty strings as "unset".
    for key in _BACKEND_ENV_KEYS:
        if env.get(key, None) == "":
            env.pop(key, None)
    for key in _HARDENING_KEYS:
        assert key not in env
