"""filters.py — ShotGrid filter validation and safety constants.

Extracted from server.py in Bucket F Phase 2a to keep the orchestrator slim
and to make this logic testable in isolation. server.py re-exports the
symbols so existing imports (and the .concepts.yml invariants that grep
for them by file path) keep working.

Contents:
    - _PROJECT_SCOPED_ENTITIES  — project-auto-filter guard (used by sg_find
      and fpt_reporting.text_search to warn when SHOTGRID_PROJECT_ID is 0)
    - _VALID_FILTER_OPERATORS   — whitelist of operators accepted by
      shotgun_api3. Hallucinated ones (is_exactly, matches, like, ...)
      are rejected at the MCP layer.
    - _MAX_FILTER_DEPTH         — recursion cap for nested filter groups
    - _validate_filter_triples  — structural validator for filter lists;
      wired into SgFindInput via @field_validator.
"""

from __future__ import annotations

from typing import Any


# Entity types that live inside a project.  Queries for these without a
# project filter return results from ALL projects on the ShotGrid site,
# which is almost never what the user wants.  The guard in sg_find_tool
# injects a warning when PROJECT_ID is unset or add_project_filter is False.
_PROJECT_SCOPED_ENTITIES: frozenset[str] = frozenset({
    "Asset", "Shot", "Sequence", "Task", "Version", "Note",
    "PublishedFile", "Playlist", "TimeLog", "Milestone",
    "CustomEntity01", "CustomEntity02", "CustomEntity03",
})

# Canonical list of valid ShotGrid filter operators. Sourced from the
# shotgun_api3 documentation (docs/SG_API.md). Used by the SgFindInput
# filter validator (C.3) to reject hallucinated operators at the MCP layer
# instead of letting them fail at the ShotGrid API layer with a confusing
# error.
#
# We use a frozenset of strings rather than an Enum so the validator can
# accept the operator either as a literal string in the filter triple
# (the natural shape from JSON) without forcing the LLM to use enum
# member syntax.
_VALID_FILTER_OPERATORS: frozenset[str] = frozenset({
    # Equality / containment
    "is", "is_not",
    "in", "not_in",
    # String matching
    "contains", "not_contains",
    "starts_with", "ends_with",
    # Numeric / date comparison
    "less_than", "greater_than", "between", "not_between",
    "in_last", "not_in_last", "in_next", "not_in_next",
    "in_calendar_day", "in_calendar_week",
    "in_calendar_month", "in_calendar_year",
    # Type-aware
    "type_is", "type_is_not",
    # Name matching (multi-entity fields)
    "name_contains", "name_not_contains", "name_starts_with", "name_ends_with",
    "name_is",
})


_MAX_FILTER_DEPTH = 20  # cap recursion to prevent stack overflow on malformed input


def _validate_filter_triples(filters: list, _depth: int = 0) -> list:
    """C.3 — structural validation for ShotGrid filter lists.

    Walks every filter and rejects:
      - Filters that are not a 3-element list/tuple [field, operator, value]
      - Operators that are not in _VALID_FILTER_OPERATORS (catches the
        hallucinated 'is_exactly', 'matches', 'like', etc. that the LLM
        invents when it skips search_sg_docs)
      - Entity references passed as bare integers/strings instead of the
        canonical {"type": "...", "id": N} dict (the single most common
        ShotGrid hallucination, per the audit)
      - Nesting deeper than _MAX_FILTER_DEPTH levels (prevents RecursionError
        on adversarial or hallucinated deeply-nested filters)

    Allows logical groupings of the form
      {"filter_operator": "any"|"all", "filters": [...]}
    by recursing into the nested filters list. This is the canonical
    ShotGrid syntax for OR/AND blocks.

    Note: this complements safety.py's regex check, which only catches
    JSON-key-value entity refs ("entity": 123) and misses the array form
    ([["entity", "is", 123]]) that filter lists actually use.
    """
    if _depth > _MAX_FILTER_DEPTH:
        raise ValueError(
            f"Filter nesting exceeds maximum depth of {_MAX_FILTER_DEPTH}. "
            "Simplify your filter structure."
        )

    # Field names that are typed as entity links and therefore require
    # a {"type": ..., "id": ...} dict on the value side. This is a
    # conservative subset — there are more entity-link fields in custom
    # schemas, but these are the universal ones.
    _entity_link_fields = {
        "entity", "project", "task", "user", "asset", "shot",
        "sequence", "version", "playlist", "step", "published_file",
        "parent", "children", "linked_versions",
    }

    def _is_entity_dict(value: Any) -> bool:
        return (
            isinstance(value, dict)
            and "type" in value
            and "id" in value
            and isinstance(value["id"], int)
            and isinstance(value["type"], str)
        )

    for idx, f in enumerate(filters):
        # Logical grouping: {"filter_operator": "all"|"any", "filters": [...]}
        if isinstance(f, dict):
            if "filter_operator" in f and "filters" in f:
                op = f.get("filter_operator")
                if op not in ("all", "any"):
                    raise ValueError(
                        f"filter[{idx}]: invalid filter_operator '{op}' "
                        "(must be 'all' or 'any')"
                    )
                if not isinstance(f["filters"], list):
                    raise ValueError(
                        f"filter[{idx}]: 'filters' inside a logical group must be a list"
                    )
                _validate_filter_triples(f["filters"], _depth + 1)
                continue
            raise ValueError(
                f"filter[{idx}]: dict filters must have 'filter_operator' "
                "and 'filters' keys (logical grouping)"
            )

        # Triple: [field, operator, value]
        if not isinstance(f, (list, tuple)) or len(f) != 3:
            raise ValueError(
                f"filter[{idx}]: each filter must be a 3-element list "
                f"[field, operator, value], got {type(f).__name__} of length "
                f"{len(f) if hasattr(f, '__len__') else 'n/a'}"
            )
        field, op, value = f[0], f[1], f[2]

        if not isinstance(field, str):
            raise ValueError(
                f"filter[{idx}]: field name must be a string, got {type(field).__name__}"
            )
        if not isinstance(op, str):
            raise ValueError(
                f"filter[{idx}]: operator must be a string, got {type(op).__name__}"
            )
        if op not in _VALID_FILTER_OPERATORS:
            raise ValueError(
                f"filter[{idx}]: invalid operator '{op}'. "
                f"Valid operators: {sorted(_VALID_FILTER_OPERATORS)}. "
                "Common hallucinations: 'is_exactly', 'matches', 'like', "
                "'before_date' — none of these exist in the ShotGrid API."
            )

        # Entity-link field validation: if the field looks like an entity
        # link, the value must be a dict (or a list of dicts for 'in').
        if field in _entity_link_fields and op in ("is", "is_not"):
            if not _is_entity_dict(value):
                raise ValueError(
                    f"filter[{idx}]: field '{field}' is an entity link, "
                    f"value must be {{'type': '...', 'id': N}}, got {value!r}. "
                    "Bare integers and strings are not accepted by ShotGrid."
                )
        if field in _entity_link_fields and op in ("in", "not_in"):
            if not isinstance(value, list) or not all(_is_entity_dict(v) for v in value):
                raise ValueError(
                    f"filter[{idx}]: field '{field}' with operator '{op}' "
                    "requires a list of entity dicts, got "
                    f"{value!r}."
                )

    return filters
