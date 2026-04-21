"""Per-tool chaining hints emitted alongside normal tool responses.

Design doc: ``docs/O3_NEXT_SUGGESTED_ACTIONS.md``. Phase 1 ships the
plumbing (helper + empty rule registry + sg_find wire-up) with zero
active rules — rules land in Phase 2 once the contract is exercised in
real runs.

The feature is strictly additive: tools whose name is not a key in
``SUGGESTION_RULES`` see no change, rule execution errors are swallowed,
and responses already carrying ``next_suggested_actions`` are returned
untouched. A tool response must never fail because a hint rule raised.
"""

from __future__ import annotations

import json
from typing import Any, Callable, TypedDict


class Suggestion(TypedDict, total=False):
    """One chaining hint. Keys match the schema in the design doc §3.1."""

    tool: str
    reason: str
    params_hint: dict[str, Any]


# tool_name → callable(parsed_response_dict) -> list[Suggestion]
#
# Phase 1 registry is intentionally empty. Phase 2 populates it with the
# five v1 rules documented in O3_NEXT_SUGGESTED_ACTIONS.md §5.
SUGGESTION_RULES: dict[str, Callable[[dict[str, Any]], list[Suggestion]]] = {}


def maybe_annotate_with_suggestions(tool_name: str, response: str) -> str:
    """Return ``response`` possibly enriched with ``next_suggested_actions``.

    The MCP tools in fpt-mcp currently serialize to JSON strings, so this
    helper takes a string, parses it, and re-serializes. When a tool is
    migrated to return a dict directly, a dict-in / dict-out sibling can
    be added without breaking callers.

    Guarantees:
    - If the response is not valid JSON or not a JSON object, returned
      verbatim. Rules only apply to object-shaped responses.
    - If ``tool_name`` is not in ``SUGGESTION_RULES``, returned verbatim.
    - If the response already contains ``next_suggested_actions`` (e.g.
      double-wrapping), returned verbatim (idempotent).
    - If the rule callable raises, the original response is returned —
      hints must never break the tool.
    """
    rule = SUGGESTION_RULES.get(tool_name)
    if rule is None:
        return response

    try:
        parsed = json.loads(response)
    except (ValueError, TypeError):
        return response
    if not isinstance(parsed, dict):
        return response
    if "next_suggested_actions" in parsed:
        return response

    try:
        suggestions = rule(parsed) or []
    except Exception:
        return response

    if not suggestions:
        return response

    parsed["next_suggested_actions"] = suggestions[:3]  # cap per design doc
    return json.dumps(parsed, default=str)
