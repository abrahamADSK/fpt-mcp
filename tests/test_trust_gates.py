"""Bucket E — Structural test: model trust gates for learn_pattern.

The RAG self-learning system has a trust gate: only models in the
WRITE_ALLOWED_MODELS set (or configured via config.json) can write
patterns directly to the docs corpus. Other models stage candidates
in rag/candidates.json for human review.

This test verifies:
  - The trust gate mechanism exists and is correctly wired.
  - WRITE_ALLOWED_MODELS contains the expected models.
  - _model_can_write() respects environment variables and config.
  - learn_pattern_tool routes to "learned" vs "staged" based on trust.

No ShotGrid connection required. Uses mocks for filesystem and config.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from fpt_mcp.server import (
    WRITE_ALLOWED_MODELS,
    _model_can_write,
    _get_current_model,
    learn_pattern_tool,
    LearnPatternInput,
)


def run_async(coro):
    """Run an async coroutine synchronously for pytest."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# TIER 1 — Trust gate infrastructure
# ---------------------------------------------------------------------------


class TestWriteAllowedModels:
    """Verify the WRITE_ALLOWED_MODELS set contains expected models."""

    def test_write_allowed_models_is_set(self):
        """WRITE_ALLOWED_MODELS must be a set (not list/dict)."""
        assert isinstance(WRITE_ALLOWED_MODELS, (set, frozenset)), (
            f"WRITE_ALLOWED_MODELS is {type(WRITE_ALLOWED_MODELS).__name__}, "
            f"expected set or frozenset"
        )

    def test_contains_claude_sonnet(self):
        """Sonnet must be in the write-allowed set."""
        has_sonnet = any("sonnet" in m for m in WRITE_ALLOWED_MODELS)
        assert has_sonnet, (
            f"No sonnet model in WRITE_ALLOWED_MODELS: {WRITE_ALLOWED_MODELS}"
        )

    def test_contains_claude_opus(self):
        """Opus must be in the write-allowed set."""
        has_opus = any("opus" in m for m in WRITE_ALLOWED_MODELS)
        assert has_opus, (
            f"No opus model in WRITE_ALLOWED_MODELS: {WRITE_ALLOWED_MODELS}"
        )

    def test_does_not_contain_qwen(self):
        """Qwen / local models must NOT be in the write-allowed set."""
        has_qwen = any("qwen" in m.lower() for m in WRITE_ALLOWED_MODELS)
        assert not has_qwen, (
            f"Qwen model found in WRITE_ALLOWED_MODELS — local models "
            f"should be read-only: {WRITE_ALLOWED_MODELS}"
        )

    def test_all_entries_are_lowercase_strings(self):
        """All entries must be lowercase strings (matching is case-insensitive)."""
        for model in WRITE_ALLOWED_MODELS:
            assert isinstance(model, str), f"Non-string entry: {model!r}"
            assert model == model.lower() or model.replace("-", "").isalnum(), (
                f"Model name has unexpected casing: {model!r}"
            )


# ---------------------------------------------------------------------------
# TIER 2 — _model_can_write() logic
# ---------------------------------------------------------------------------


class TestModelCanWrite:
    """Verify _model_can_write() correctly gates based on model identity."""

    def test_claude_sonnet_can_write(self):
        """A Claude Sonnet model should be allowed to write."""
        with patch("fpt_mcp.server._get_current_model", return_value="claude-sonnet-4"):
            assert _model_can_write() is True

    def test_claude_opus_can_write(self):
        """A Claude Opus model should be allowed to write."""
        with patch("fpt_mcp.server._get_current_model", return_value="claude-opus-4-6"):
            assert _model_can_write() is True

    def test_qwen_cannot_write(self):
        """A Qwen local model should NOT be allowed to write."""
        with patch("fpt_mcp.server._get_current_model", return_value="qwen3.5-mcp"):
            with patch("fpt_mcp.server._get_config", return_value={}):
                assert _model_can_write() is False

    def test_unknown_model_cannot_write(self):
        """An unknown model should NOT be allowed to write."""
        with patch("fpt_mcp.server._get_current_model", return_value="unknown"):
            with patch("fpt_mcp.server._get_config", return_value={}):
                assert _model_can_write() is False

    def test_empty_model_cannot_write(self):
        """Empty model string should NOT be allowed to write."""
        with patch("fpt_mcp.server._get_current_model", return_value=""):
            with patch("fpt_mcp.server._get_config", return_value={}):
                assert _model_can_write() is False

    def test_env_var_overrides_config(self):
        """FPT_MCP_RUNTIME_MODEL env var should override config.json model."""
        with patch.dict("os.environ", {"FPT_MCP_RUNTIME_MODEL": "claude-sonnet-4"}):
            result = _get_current_model()
            assert "sonnet" in result.lower()

    def test_config_write_allowed_models_override(self):
        """config.json write_allowed_models overrides WRITE_ALLOWED_MODELS."""
        with patch("fpt_mcp.server._get_current_model", return_value="custom-model-v1"):
            # Default set does not include custom-model-v1
            with patch("fpt_mcp.server._get_config", return_value={}):
                assert _model_can_write() is False
            # But config.json can allow it
            with patch("fpt_mcp.server._get_config",
                        return_value={"write_allowed_models": ["custom-model"]}):
                assert _model_can_write() is True


# ---------------------------------------------------------------------------
# TIER 3 — learn_pattern_tool routing
# ---------------------------------------------------------------------------


class TestLearnPatternRouting:
    """Verify learn_pattern_tool routes to 'learned' or 'staged' based on trust."""

    @pytest.fixture
    def pattern_params(self):
        """Return valid LearnPatternInput for testing."""
        return LearnPatternInput(
            description="Filter PublishedFiles by Shot and type",
            code="sg.find('PublishedFile', [['entity', 'is', {'type': 'Shot', 'id': 123}]])",
            api="shotgun_api3",
        )

    def test_trusted_model_writes_directly(self, pattern_params, tmp_path):
        """A trusted model (Sonnet/Opus) writes the pattern to docs directly."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "SG_API.md").write_text("# SG API\n")

        with patch("fpt_mcp.server._model_can_write", return_value=True), \
             patch("fpt_mcp.server._SERVER_DIR", tmp_path):
            result = json.loads(run_async(learn_pattern_tool(pattern_params)))

        assert result["status"] == "learned"
        assert "Pattern appended" in result.get("note", "")

        # Verify the pattern was actually written to the file
        content = (docs_dir / "SG_API.md").read_text()
        assert "Filter PublishedFiles" in content

    def test_untrusted_model_stages_candidate(self, pattern_params, tmp_path):
        """An untrusted model (Qwen, unknown) stages the pattern for review."""
        rag_dir = tmp_path / "rag"
        rag_dir.mkdir()

        with patch("fpt_mcp.server._model_can_write", return_value=False), \
             patch("fpt_mcp.server._get_current_model", return_value="qwen3.5-mcp"), \
             patch("fpt_mcp.server._SERVER_DIR", tmp_path):
            result = json.loads(run_async(learn_pattern_tool(pattern_params)))

        assert result["status"] == "staged"
        assert "read-only" in result.get("note", "").lower()

        # Verify the candidate was written to candidates.json
        candidates_path = rag_dir / "candidates.json"
        assert candidates_path.exists()
        candidates = json.loads(candidates_path.read_text())
        assert len(candidates) == 1
        assert candidates[0]["description"] == "Filter PublishedFiles by Shot and type"
        assert candidates[0]["model"] == "qwen3.5-mcp"

    def test_staged_candidates_accumulate(self, pattern_params, tmp_path):
        """Multiple staged patterns accumulate in candidates.json."""
        rag_dir = tmp_path / "rag"
        rag_dir.mkdir()
        # Pre-seed with one existing candidate
        existing = [{"description": "existing", "code": "x", "api": "shotgun_api3",
                      "model": "qwen3.5-mcp", "timestamp": "2026-01-01T00:00:00"}]
        (rag_dir / "candidates.json").write_text(json.dumps(existing))

        with patch("fpt_mcp.server._model_can_write", return_value=False), \
             patch("fpt_mcp.server._get_current_model", return_value="qwen3.5-mcp"), \
             patch("fpt_mcp.server._SERVER_DIR", tmp_path):
            result = json.loads(run_async(learn_pattern_tool(pattern_params)))

        assert result["status"] == "staged"
        candidates = json.loads((rag_dir / "candidates.json").read_text())
        assert len(candidates) == 2  # existing + new
