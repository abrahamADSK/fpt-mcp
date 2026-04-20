"""Regression tests for the ollama_mac num_ctx preflight.

Context
-------
Ollama's Anthropic-compatible endpoint (``/v1/messages``) silently ignores
Modelfile ``num_ctx`` and falls back to 4096 tokens. Without an explicit
preflight against ``/api/generate``, every Mac-local inference spawned
from the Qt console would be truncated to 4096 tokens — wrecking multi-
turn 3D-creation workflows whose system prompt alone uses ~1,400 tokens.

These tests pin three behaviours of
``fpt_mcp.qt.claude_worker._preload_ollama_mac_model``:

1. The constant ``OLLAMA_MAC_NUM_CTX`` stays at 8192 (tuned for 4B/9B
   models on Mac 24 GB unified memory).
2. A successful preflight issues a POST to ``<url>/api/generate`` with
   the expected JSON payload (model, num_ctx, keep_alive, stream=False)
   and Content-Type header.
3. The preflight is non-fatal: a transport-level exception from
   ``urlopen`` is swallowed (logged only) so the Qt worker can still
   spawn ``claude -p`` — the main call may succeed, just capped at the
   Ollama default 4096 ceiling.

No Qt, no subprocess, no network. Pure monkeypatching of
``urllib.request.urlopen``.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from fpt_mcp.qt.claude_worker import (
    OLLAMA_MAC_NUM_CTX,
    _preload_ollama_mac_model,
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
    assert body["keep_alive"] == "10m"
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
