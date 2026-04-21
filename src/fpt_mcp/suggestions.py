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
import os
from typing import Any, Callable, TypedDict


class Suggestion(TypedDict, total=False):
    """One chaining hint. Keys match the schema in the design doc §3.1."""

    tool: str
    reason: str
    params_hint: dict[str, Any]


_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".exr")


def _suggest_after_sg_find(response: dict[str, Any]) -> list[Suggestion]:
    """Rule 1 + 2 from design doc §5.

    - Asset rows with non-empty ``image`` → sg_download + maya_vision3d(generate_image).
    - Task/Version rows → fpt_reporting(activity) for the first match.
    """
    rows = response.get("entities") or []
    if not rows:
        return []

    suggestions: list[Suggestion] = []
    first = rows[0] or {}
    entity_type = first.get("type", "")

    assets_with_image = [
        r for r in rows if (r or {}).get("type") == "Asset" and (r or {}).get("image")
    ]
    if assets_with_image:
        first_asset = assets_with_image[0]
        suggestions.append({
            "tool": "sg_download",
            "reason": "Download the reference image for use in 3D generation.",
            "params_hint": {
                "entity_type": "Asset",
                "entity_id": first_asset.get("id"),
                "field_name": "image",
            },
        })
        suggestions.append({
            "tool": "maya_vision3d",
            "reason": "Generate a 3D mesh from this asset image.",
            "params_hint": {"action": "generate_image"},
        })

    if entity_type in ("Task", "Version"):
        suggestions.append({
            "tool": "fpt_reporting",
            "reason": f"Read the activity stream for this {entity_type}.",
            "params_hint": {
                "action": "activity",
                "entity_type": entity_type,
                "entity_id": first.get("id"),
            },
        })
    return suggestions


def _suggest_after_sg_download(response: dict[str, Any]) -> list[Suggestion]:
    """Rule 3 — downloaded image file → maya_vision3d(generate_image).

    Trigger: ``path`` key present (success shape) AND the file has an
    image extension. Explicit ``error`` responses short-circuit.
    """
    if "error" in response:
        return []
    path = response.get("path")
    if not isinstance(path, str):
        return []
    if not path.lower().endswith(_IMAGE_EXTS):
        return []
    return [{
        "tool": "maya_vision3d",
        "reason": "Feed the downloaded image into 3D generation.",
        "params_hint": {
            "action": "generate_image",
            "image_path": path,
        },
    }]


def _suggest_after_tk_publish(response: dict[str, Any]) -> list[Suggestion]:
    """Rule 4 — publish success → note_thread + sequence scope.

    Trigger: ``id`` + ``entity`` keys present (success shape).
    """
    if "error" in response or "id" not in response:
        return []
    entity = response.get("entity") or {}
    entity_type = entity.get("type")
    entity_id = entity.get("id")
    if not entity_type or entity_id is None:
        return []
    suggestions: list[Suggestion] = [{
        "tool": "fpt_reporting",
        "reason": "Check for open notes on this entity to coordinate handoff.",
        "params_hint": {
            "action": "activity",
            "entity_type": entity_type,
            "entity_id": entity_id,
        },
    }]
    if entity_type == "Shot":
        suggestions.append({
            "tool": "sg_find",
            "reason": "List sibling Shots in the same sequence for batch follow-up.",
            "params_hint": {"entity_type": "Shot"},
        })
    return suggestions


def _suggest_after_fpt_bulk(response: dict[str, Any]) -> list[Suggestion]:
    """Rule 5 — successful soft-delete → offer revive as safety net.

    Trigger: ``deleted: true`` with entity_type + entity_id (fpt_bulk
    delete response shape). Create/batch responses are ignored.
    """
    if not response.get("deleted"):
        return []
    entity_type = response.get("entity_type")
    entity_id = response.get("entity_id")
    if not entity_type or entity_id is None:
        return []
    return [{
        "tool": "fpt_bulk",
        "reason": "Restore this entity if the delete was unintended (soft-delete is reversible).",
        "params_hint": {
            "action": "revive",
            "params": {"entity_type": entity_type, "entity_id": entity_id},
        },
    }]


# tool_name → callable(parsed_response_dict) -> list[Suggestion]
SUGGESTION_RULES: dict[str, Callable[[dict[str, Any]], list[Suggestion]]] = {
    "sg_find": _suggest_after_sg_find,
    "sg_download": _suggest_after_sg_download,
    "tk_publish": _suggest_after_tk_publish,
    "fpt_bulk": _suggest_after_fpt_bulk,
}


def _suggestions_disabled() -> bool:
    """Kill switch for the whole feature. Set FPT_MCP_DISABLE_SUGGESTIONS=1
    to emit no suggestions regardless of what the rules return. Useful if
    a rule misbehaves in production before a fix can be deployed."""
    return os.environ.get("FPT_MCP_DISABLE_SUGGESTIONS", "").strip() in ("1", "true", "yes")


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
    if _suggestions_disabled():
        return response
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
