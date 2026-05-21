"""Regression tests for the ollama_mac num_ctx preflight (F1b keep_alive knob).

Context
-------
Ollama's Anthropic-compatible endpoint (``/v1/messages``) silently ignores
Modelfile ``num_ctx`` and falls back to 4096 tokens. Without an explicit
preflight against ``/api/generate``, every Mac-local inference spawned
from the Qt console would be truncated to 4096 tokens — wrecking multi-
turn 3D-creation workflows whose system prompt alone uses ~1,400 tokens.

F1b extends the preflight: ``keep_alive`` is now a config knob
(``ollama_keep_alive`` in ``config.json``) defaulting to ``"30m"`` so
5–15 min reading/typing gaps don't trigger a cold model reload. The
old hard-coded ``"10m"`` is retired.

These tests pin four behaviours of
``fpt_mcp.qt.claude_worker._preload_ollama_mac_model`` and
``fpt_mcp.qt.claude_worker.resolve_keep_alive``:

1. The constant ``OLLAMA_MAC_NUM_CTX`` stays at 8192 (tuned for 4B/9B
   models on Mac 24 GB unified memory).
2. A successful preflight issues a POST to ``<url>/api/generate`` with
   the expected JSON payload (model, num_ctx, keep_alive, stream=False)
   and Content-Type header.
3. The preflight is non-fatal: a transport-level exception from
   ``urlopen`` is swallowed (logged only) so the Qt worker can still
   spawn ``claude -p`` — the main call may succeed, just capped at the
   Ollama default 4096 ceiling.
4. ``resolve_keep_alive`` reads ``ollama_keep_alive`` from config.json,
   defaults to ``"30m"``, and rejects non-str/int types.

No Qt, no subprocess, no network. Pure monkeypatching of
``urllib.request.urlopen``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import types
import urllib.request
from pathlib import Path


# ── PySide6 stub ──────────────────────────────────────────────────────────────
# claude_worker imports PySide6 at the module level. Stub it out before the
# first import so tests run headless (CI / no Qt install). The preflight
# helper and resolve_keep_alive are pure-Python and never touch Qt at runtime.
if "PySide6" not in sys.modules and "PySide2" not in sys.modules:
    _pyside6 = types.ModuleType("PySide6")
    _qtcore = types.ModuleType("PySide6.QtCore")
    _qtwidgets = types.ModuleType("PySide6.QtWidgets")
    _qtgui = types.ModuleType("PySide6.QtGui")

    class _QThreadStub:
        def __init__(self, *a, **kw): ...
        def start(self) -> None: ...

    class _SignalStub:
        def __init__(self, *a, **kw): ...
        def connect(self, *a, **kw): ...
        def emit(self, *a, **kw): ...

    _qtcore.QThread = _QThreadStub
    _qtcore.Signal = _SignalStub
    _pyside6.QtCore = _qtcore
    sys.modules["PySide6"] = _pyside6
    sys.modules["PySide6.QtCore"] = _qtcore
    sys.modules["PySide6.QtWidgets"] = _qtwidgets
    sys.modules["PySide6.QtGui"] = _qtgui


from fpt_mcp.qt.claude_worker import (  # noqa: E402
    OLLAMA_MAC_NUM_CTX,
    _preload_ollama_mac_model,
    resolve_keep_alive,
)


# ---------------------------------------------------------------------------
# Constant pinning
# ---------------------------------------------------------------------------

def test_ollama_mac_num_ctx_is_8192() -> None:
    """OLLAMA_MAC_NUM_CTX is pinned at 8192 (Mac 24GB 4B/9B budget)."""
    assert OLLAMA_MAC_NUM_CTX == 8192


# ---------------------------------------------------------------------------
# Payload shape — the POST must hit /api/generate with options.num_ctx
# ---------------------------------------------------------------------------

class _FakeResponse:
    """Minimal stand-in for the response object returned by urlopen()."""

    def read(self) -> bytes:
        return b"{}"

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_preflight_posts_generate_with_num_ctx(monkeypatch) -> None:
    """Preflight POSTs /api/generate with the expected payload + headers."""
    captured: dict = {}

    def fake_urlopen(req, timeout=None):  # noqa: ARG001 — signature matches urlopen
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = dict(req.header_items())
        captured["data"] = req.data
        captured["timeout"] = timeout
        return _FakeResponse()

    # Patch at the module where the helper resolves the symbol.
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    _preload_ollama_mac_model(
        model="qwen3.5:4b",
        url="http://localhost:11434",
        num_ctx=OLLAMA_MAC_NUM_CTX,
        keep_alive="30m",  # F1b: pass resolved value (default "30m")
    )

    # URL: <url>/api/generate (NOT /v1/messages — that endpoint ignores num_ctx)
    assert captured["url"] == "http://localhost:11434/api/generate"
    # HTTP verb: POST (urllib infers POST when data is set)
    assert captured["method"] == "POST"
    # Content-Type header must be JSON. urllib lowercases/capitalizes keys,
    # so we check case-insensitively.
    headers_ci = {k.lower(): v for k, v in captured["headers"].items()}
    assert headers_ci.get("content-type") == "application/json"
    # Timeout: 120s (models can take a while on cold start)
    assert captured["timeout"] == 120

    # Body shape: model, empty prompt, options.num_ctx, keep_alive, stream=False
    body = json.loads(captured["data"].decode())
    assert body["model"] == "qwen3.5:4b"
    assert body["prompt"] == ""
    assert body["options"]["num_ctx"] == OLLAMA_MAC_NUM_CTX
    # keep_alive is now a caller-supplied parameter (F1b); the test passes
    # the default value explicitly to verify it flows through correctly.
    assert body["keep_alive"] == "30m"
    assert body["stream"] is False


def test_preflight_respects_custom_url(monkeypatch) -> None:
    """When a non-default URL is passed, the POST hits that URL, not localhost."""
    captured: dict = {}

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        captured["url"] = req.full_url
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    _preload_ollama_mac_model(
        model="qwen3.5-mcp",
        url="http://192.168.1.50:11434",
        num_ctx=4096,
    )

    assert captured["url"] == "http://192.168.1.50:11434/api/generate"


# ---------------------------------------------------------------------------
# Non-fatal behaviour — urlopen errors must NOT propagate
# ---------------------------------------------------------------------------

def test_preflight_swallows_transport_errors(monkeypatch) -> None:
    """Preflight must never raise — a network failure falls through to the
    main claude-subprocess call (which may still work, just capped at 4096).
    """
    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        raise ConnectionRefusedError("Ollama daemon down")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    # Must return normally — no exception propagation.
    _preload_ollama_mac_model(
        model="qwen3.5:4b",
        url="http://localhost:11434",
        num_ctx=OLLAMA_MAC_NUM_CTX,
    )


def test_preflight_swallows_timeout(monkeypatch) -> None:
    """Timeout from urlopen must also be swallowed (non-fatal)."""
    import socket

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        raise socket.timeout("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    _preload_ollama_mac_model(
        model="qwen3.5:4b",
        url="http://localhost:11434",
        num_ctx=OLLAMA_MAC_NUM_CTX,
    )


# ---------------------------------------------------------------------------
# F1b: resolve_keep_alive knob behaviour
# ---------------------------------------------------------------------------

def test_resolve_keep_alive_default_when_no_config() -> None:
    """When config.json does not exist, resolve_keep_alive returns '30m'."""
    result = resolve_keep_alive(config_path="/nonexistent/path/config.json")
    assert result == "30m"


def test_resolve_keep_alive_reads_string_from_config() -> None:
    """A string value in config.json is returned as-is."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as tmp:
        json.dump({"ollama_keep_alive": "1h"}, tmp)
        tmp_path = tmp.name
    assert resolve_keep_alive(config_path=tmp_path) == "1h"


def test_resolve_keep_alive_reads_int_from_config() -> None:
    """An integer value (seconds) in config.json is returned as int."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as tmp:
        json.dump({"ollama_keep_alive": 3600}, tmp)
        tmp_path = tmp.name
    assert resolve_keep_alive(config_path=tmp_path) == 3600


def test_resolve_keep_alive_rejects_invalid_types() -> None:
    """Non-str/int types (dict, list, None, bool) collapse to the default."""
    for bad_value in [None, True, False, [], {"k": "v"}]:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            json.dump({"ollama_keep_alive": bad_value}, tmp)
            tmp_path = tmp.name
        result = resolve_keep_alive(config_path=tmp_path)
        assert result == "30m", f"Expected '30m' for bad_value={bad_value!r}, got {result!r}"


def test_resolve_keep_alive_absent_key_returns_default() -> None:
    """When ollama_keep_alive key is absent, '30m' is returned."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as tmp:
        json.dump({"backend": "anthropic"}, tmp)
        tmp_path = tmp.name
    assert resolve_keep_alive(config_path=tmp_path) == "30m"


def test_preload_keep_alive_flows_into_payload(monkeypatch) -> None:
    """The keep_alive parameter supplied to _preload_ollama_mac_model
    reaches the /api/generate request body unchanged (F1b wire test).
    """
    captured: dict = {}

    class _FakeResp:
        def read(self) -> bytes:
            return b"{}"
        def __enter__(self) -> "_FakeResp":
            return self
        def __exit__(self, *_) -> None:
            return None

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        captured["body"] = json.loads(req.data.decode())
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    _preload_ollama_mac_model(
        model="qwen3.5-mcp",
        url="http://localhost:11434",
        num_ctx=8192,
        keep_alive="45m",  # non-default to verify the parameter flows through
    )

    assert captured["body"]["keep_alive"] == "45m"
