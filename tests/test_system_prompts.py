"""Bucket E — Structural regression tests for SYSTEM_PROMPT variants.

Both SYSTEM_PROMPT (Anthropic) and SYSTEM_PROMPT_QWEN (Ollama/Qwen) drive
the Qt console's 3D-creation workflow. They were patched iteratively in
Chat 39 (dispatcher API, vision3d_url_required, adaptive bullets, URL
gate) without a structural safety net. This module enforces invariants
that any future prompt edit must preserve:

- Both variants exist, non-empty, min length.
- Qwen variant is a compressed form (byte ratio <= 65%).
- The AI Quality block is byte-identical between variants.
- The TEXT PROMPT RESOLUTION priority chain is documented in both.
- The Vision3D URL policy (`vision3d_url_required`, never fabricate
  hostname) is documented in both.
- The maya_session / maya_vision3d dispatcher pattern is used, not
  the legacy granular tool names.
- Adaptive bullets / "at most TWICE" rule is present.
- Step 1..6 skeleton of the 3D workflow is present in order.
- The dispatcher-aware `_select_system_prompt(backend)` routes correctly.

No LLM calls, no mocks, no ShotGrid. Pure string/regex analysis on the
standalone prompt files under ``src/fpt_mcp/qt/system_prompts/``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fpt_mcp.qt.claude_worker import (
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_QWEN,
    _select_system_prompt,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def full_prompt() -> str:
    """The Anthropic variant loaded from the standalone text file."""
    return SYSTEM_PROMPT


@pytest.fixture(scope="module")
def qwen_prompt() -> str:
    """The compressed Qwen variant loaded from the standalone text file."""
    return SYSTEM_PROMPT_QWEN


@pytest.fixture(scope="module")
def both_prompts(full_prompt: str, qwen_prompt: str) -> list[tuple[str, str]]:
    return [("full", full_prompt), ("qwen", qwen_prompt)]


def _extract_quality_block(text: str) -> str | None:
    """Return the AI Quality header line plus its four bullet lines."""
    match = re.search(
        r"(AI Quality[^\n]*\n(?:[ \t]*•[^\n]*\n){4})",
        text,
    )
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# TIER 1 — P1 critical structural invariants
# ---------------------------------------------------------------------------


def test_system_prompt_exists_and_minimal_length(full_prompt, qwen_prompt):
    """T1: Both prompts are non-empty and at least 1000 characters."""
    assert len(full_prompt) >= 1000, f"SYSTEM_PROMPT too short: {len(full_prompt)}"
    assert len(qwen_prompt) >= 1000, f"SYSTEM_PROMPT_QWEN too short: {len(qwen_prompt)}"


def test_system_prompt_qwen_is_compressed_variant(full_prompt, qwen_prompt):
    """T2: Qwen variant must be a compressed form (<= 65% of full size)."""
    ratio = len(qwen_prompt) / len(full_prompt)
    assert ratio <= 0.65, (
        f"Qwen variant must be <= 65% of full size; got {ratio*100:.1f}% "
        f"({len(qwen_prompt)} / {len(full_prompt)} chars). If you added "
        f"content to both variants, consider compressing Qwen further."
    )


def test_quality_block_identical_both_prompts(full_prompt, qwen_prompt):
    """T3: The AI Quality block must be byte-identical in both variants."""
    block_full = _extract_quality_block(full_prompt)
    block_qwen = _extract_quality_block(qwen_prompt)
    assert block_full is not None, "Quality block not found in SYSTEM_PROMPT"
    assert block_qwen is not None, "Quality block not found in SYSTEM_PROMPT_QWEN"
    assert block_full == block_qwen, (
        "Quality blocks must be byte-identical.\n"
        f"--- full ---\n{block_full}\n--- qwen ---\n{block_qwen}"
    )


def test_quality_block_contains_required_fields(full_prompt):
    """T4: Quality block must mention model, octree, steps and faces values."""
    block = _extract_quality_block(full_prompt)
    assert block is not None
    required = [
        "turbo model",
        "octree 256",
        "octree 384",
        "octree 512",
        "10 steps",
        "20 steps",
        "30 steps",
        "50 steps",
        "10k faces",
        "50k faces",
        "150k faces",
    ]
    missing = [term for term in required if term not in block]
    assert not missing, f"Quality block missing terms: {missing}"


def test_text_prompt_resolution_priority_chain_present(both_prompts):
    """T5: Both prompts document the 3-priority text resolution chain."""
    for name, prompt in both_prompts:
        # Priority 1: user-typed prompt
        assert re.search(r"user\s+picked\s+['\"]?prompt", prompt, re.IGNORECASE), (
            f"{name}: missing priority 1 (user-typed prompt)"
        )
        # Priority 2 + 3: Asset.description reference
        assert "Asset.description" in prompt, (
            f"{name}: missing Asset.description reference"
        )
        # Priority 3: fallback when no image
        assert re.search(r"fallback|no\s+image", prompt, re.IGNORECASE), (
            f"{name}: missing fallback branch"
        )


def test_vision3d_url_policy_complete(both_prompts):
    """T6: Both prompts document the vision3d_url_required handshake."""
    for name, prompt in both_prompts:
        assert "vision3d_url_required" in prompt, (
            f"{name}: missing vision3d_url_required handling"
        )
        assert "select_server" in prompt, (
            f"{name}: missing select_server step"
        )
        assert re.search(
            r"never\s+fabricate|never\s+auto[- ]select|do\s+not\s+auto",
            prompt,
            re.IGNORECASE,
        ), f"{name}: missing 'never fabricate / never auto-select' rule"


def test_dispatcher_tools_documented(both_prompts):
    """T7: Both prompts reference the maya_session / maya_vision3d dispatchers."""
    for name, prompt in both_prompts:
        assert "maya_session" in prompt, f"{name}: missing maya_session dispatcher"
        assert "maya_vision3d" in prompt, f"{name}: missing maya_vision3d dispatcher"
        # And they should show the action=... params=... call form at least once
        assert re.search(
            r"maya_(?:session|vision3d)\(action=",
            prompt,
        ), f"{name}: dispatchers not shown with action= form"


def test_adaptive_bullets_rule_present(both_prompts):
    """T8: 'at most TWICE' and self-describing-keyword rules present in both."""
    for name, prompt in both_prompts:
        assert "TWICE" in prompt, f"{name}: missing 'at most TWICE' rule"
        assert re.search(
            r"(bare\s+numbers?|self[- ]describing|content[- ]based\s+label)",
            prompt,
            re.IGNORECASE,
        ), f"{name}: missing bare-number / self-describing rule"


# ---------------------------------------------------------------------------
# TIER 2 — P2 workflow semantics
# ---------------------------------------------------------------------------


def test_3d_workflow_step_count(both_prompts):
    """T9: Steps 1..6 are mentioned in ascending order in each prompt."""
    for name, prompt in both_prompts:
        indices = []
        for step in range(1, 7):
            # accept "Step N", "N." at line start, "1. CHECK VISION3D", etc.
            match = re.search(rf"(?m)^\s*{step}[.\)]\s|Step\s+{step}\b", prompt)
            assert match, f"{name}: step {step} not found"
            indices.append(match.start())
        assert indices == sorted(indices), (
            f"{name}: steps not in ascending order (positions={indices})"
        )


def test_system_prompt_select_by_backend():
    """T10: _select_system_prompt routes backends to the correct variant."""
    assert _select_system_prompt("anthropic") is SYSTEM_PROMPT
    assert _select_system_prompt("ollama") is SYSTEM_PROMPT_QWEN
    assert _select_system_prompt("ollama_mac") is SYSTEM_PROMPT_QWEN
    # Case-insensitive per the defensive .lower() in the function
    assert _select_system_prompt("OLLAMA") is SYSTEM_PROMPT_QWEN
    assert _select_system_prompt("Ollama_Mac") is SYSTEM_PROMPT_QWEN
    # None and empty string must fall back to the full prompt
    assert _select_system_prompt(None) is SYSTEM_PROMPT
    assert _select_system_prompt("") is SYSTEM_PROMPT


def test_conversation_history_awareness(both_prompts):
    """T11: Both prompts reference CONVERSATION HISTORY with a skip rule."""
    for name, prompt in both_prompts:
        assert "CONVERSATION HISTORY" in prompt, (
            f"{name}: missing CONVERSATION HISTORY reference"
        )
        assert re.search(
            r"skip|do\s+not\s+ask|already\s+(?:chose|answered|resolved)",
            prompt,
            re.IGNORECASE,
        ), f"{name}: missing skip-when-already-answered rule"


def test_never_repeat_questions_rule(both_prompts):
    """T12: Both prompts explicitly forbid repeating answered questions."""
    for name, prompt in both_prompts:
        assert re.search(
            r"(never\s+repeat|don't\s+repeat|do\s+not\s+repeat)",
            prompt,
            re.IGNORECASE,
        ), f"{name}: missing 'never repeat a question' rule"


def test_image_to_3d_flow_documented(both_prompts):
    """T13: Both prompts document the full image-to-3D pipeline."""
    steps = [
        "sg_download",
        "generate_image",
        "poll",
        "download",
        "execute_python",
    ]
    for name, prompt in both_prompts:
        missing = [s for s in steps if s not in prompt]
        assert not missing, f"{name}: image-to-3D missing steps: {missing}"


def test_text_to_3d_flow_documented(both_prompts):
    """T14: Both prompts document the full text-to-3D pipeline."""
    steps = ["generate_text", "poll", "download", "execute_python"]
    for name, prompt in both_prompts:
        missing = [s for s in steps if s not in prompt]
        assert not missing, f"{name}: text-to-3D missing steps: {missing}"


def test_direct_maya_option_documented(both_prompts):
    """T15: Both prompts document the direct Maya modeling path."""
    tools = ["maya_create_primitive", "maya_transform", "maya_assign_material"]
    for name, prompt in both_prompts:
        missing = [t for t in tools if t not in prompt]
        assert not missing, f"{name}: direct-Maya missing tools: {missing}"


def test_no_granular_tool_names(both_prompts):
    """T16: Legacy granular Vision3D tools must not appear as direct calls.

    The dispatcher refactor consolidated shape_generate_*, vision3d_poll,
    vision3d_download, texture_mesh_remote into maya_vision3d(action=...).
    A regression would re-introduce ``call shape_generate_text(...)`` style
    usage in the prompt.
    """
    legacy = [
        "shape_generate_remote",
        "shape_generate_text",
        "texture_mesh_remote",
        "vision3d_poll",
        "vision3d_download",
    ]
    for name, prompt in both_prompts:
        for tool in legacy:
            # The legacy name must not appear as a direct callable invocation.
            bad = re.search(rf"\b{tool}\s*\(", prompt)
            assert not bad, (
                f"{name}: legacy tool '{tool}' invoked directly; should go "
                f"through maya_vision3d(action='...', params=...)"
            )


# ---------------------------------------------------------------------------
# TIER 3 — P3 UX refinement guards
# ---------------------------------------------------------------------------


def test_vision3d_mentioned_sparingly(both_prompts):
    """T17: 'Vision3D' appears at a reasonable frequency in each prompt.

    This is a soft drift cap — the prompt is a template, not a rendered
    response, so some repetition is expected when documenting multiple
    workflow steps (URL policy, URL gate, workflow intro, image/text flows,
    quality block, diagnostics). The current baseline is ~29 for the full
    variant; anything above 40 suggests drift worth trimming.
    """
    for name, prompt in both_prompts:
        count = len(re.findall(r"Vision3D", prompt))
        assert count <= 40, (
            f"{name}: 'Vision3D' appears {count} times — consider trimming"
        )


def test_no_fabricated_urls_in_examples(both_prompts):
    """T18: Prompts must never hardcode a specific Vision3D host."""
    forbidden = ["glorfindel:8000", "localhost:8000", "127.0.0.1:8000"]
    for name, prompt in both_prompts:
        for url in forbidden:
            assert url not in prompt, (
                f"{name}: hardcoded URL '{url}' — use templated "
                f"<hostname>:<port> instead"
            )


def test_tool_labels_dict_consistency():
    """T19: ClaudeWorker._TOOL_LABELS keys must all be non-empty strings."""
    from fpt_mcp.qt.claude_worker import ClaudeWorker
    labels = ClaudeWorker._TOOL_LABELS
    assert labels, "_TOOL_LABELS dict is empty"
    for key, value in labels.items():
        assert isinstance(key, str) and key, f"bad label key: {key!r}"
        assert isinstance(value, str) and value, f"bad label value: {value!r}"
        # Keys are short tool names or mcp__server__tool format; no spaces
        assert " " not in key, f"label key contains space: {key!r}"


def test_prompt_language_tags(both_prompts):
    """T20: Both prompts instruct Claude to respond concisely in user language."""
    for name, prompt in both_prompts:
        assert re.search(
            r"user'?s\s+language|respond\s+in\s+the\s+user",
            prompt,
            re.IGNORECASE,
        ), f"{name}: missing 'respond in user language' instruction"
        assert re.search(
            r"concise|execute,?\s+don't|don't\s+narrate",
            prompt,
            re.IGNORECASE,
        ), f"{name}: missing conciseness instruction"
