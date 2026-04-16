"""
test_filter_recursion.py
========================
Bucket C — Recursion depth and edge-case tests for _validate_filter_triples.

The function handles nested filter groups recursively via
  {"filter_operator": "all"|"any", "filters": [...]}
Currently there is no explicit recursion depth limit, so deeply nested
filters could trigger a RecursionError (Python default limit ~1000).

These tests verify:
  a) Normal nesting (2-3 levels) works fine
  b) Extremely deep nesting (100+ levels) is handled gracefully
  c) Edge cases: empty filter lists, single-element, mixed nesting
"""

import sys

import pytest

from fpt_mcp.server import _validate_filter_triples


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_nested_filter(depth: int, leaf_filter: list | None = None) -> list:
    """Build a filter list with nested logical groups to the given depth.

    The innermost level contains a real filter triple so the whole structure
    is valid if validation doesn't hit a depth limit.

    Args:
        depth: How many nesting levels to create (1 = single group).
        leaf_filter: The filter triple at the bottom.  Defaults to a simple
                     code/is/hero triple.

    Returns:
        A filter list suitable for _validate_filter_triples.
    """
    if leaf_filter is None:
        leaf_filter = ["code", "is", "hero"]

    current = [leaf_filter]
    for _ in range(depth):
        current = [{"filter_operator": "any", "filters": current}]
    return current


# ---------------------------------------------------------------------------
# a) Normal nesting — 2 and 3 levels should work without issues
# ---------------------------------------------------------------------------

class TestNormalNesting:
    """Nesting depths that any production ShotGrid query might use."""

    def test_flat_filters_no_nesting(self):
        """Plain filter list with no grouping at all."""
        filters = [
            ["code", "is", "hero"],
            ["sg_status_list", "is_not", "omt"],
        ]
        result = _validate_filter_triples(filters)
        assert result == filters

    def test_single_level_nesting(self):
        """One logical group wrapping two triples (depth=1)."""
        filters = [
            {
                "filter_operator": "any",
                "filters": [
                    ["code", "is", "hero"],
                    ["code", "is", "villain"],
                ],
            },
        ]
        result = _validate_filter_triples(filters)
        assert result == filters

    def test_two_level_nesting(self):
        """Two levels deep — a group inside a group."""
        filters = _build_nested_filter(2)
        result = _validate_filter_triples(filters)
        assert result == filters

    def test_three_level_nesting(self):
        """Three levels deep — realistic complex query."""
        filters = _build_nested_filter(3)
        result = _validate_filter_triples(filters)
        assert result == filters

    def test_mixed_operators_at_different_levels(self):
        """Alternate 'all' and 'any' at successive levels."""
        filters = [
            {
                "filter_operator": "all",
                "filters": [
                    ["sg_status_list", "is", "ip"],
                    {
                        "filter_operator": "any",
                        "filters": [
                            ["code", "contains", "hero"],
                            ["code", "contains", "villain"],
                        ],
                    },
                ],
            },
        ]
        result = _validate_filter_triples(filters)
        assert result == filters


# ---------------------------------------------------------------------------
# b) Extremely deep nesting — should be handled gracefully
# ---------------------------------------------------------------------------

class TestDeepNesting:
    """Deeply nested filters that might be crafted by an LLM or fuzzer."""

    def test_depth_50_works_or_raises_value_error(self):
        """50 levels — well within Python's default recursion limit.

        Should either succeed or raise ValueError if a depth cap is added.
        Must NOT raise RecursionError at this depth.
        """
        filters = _build_nested_filter(50)
        try:
            result = _validate_filter_triples(filters)
            # If it succeeds, the leaf triple must be preserved
            assert result == filters
        except ValueError:
            # Acceptable: a future depth cap raises ValueError
            pass

    def test_depth_100_graceful(self):
        """100 levels — still under Python's default ~1000 limit.

        Should succeed or raise ValueError.  Must NOT crash with
        RecursionError.
        """
        filters = _build_nested_filter(100)
        try:
            result = _validate_filter_triples(filters)
            assert result == filters
        except ValueError:
            pass

    def test_depth_500_graceful(self):
        """500 levels — approaching Python's recursion limit.

        Should succeed, raise ValueError (depth cap), or raise
        RecursionError.  We document the current behavior: if no depth
        cap exists, Python's own limit kicks in.
        """
        filters = _build_nested_filter(500)
        try:
            result = _validate_filter_triples(filters)
            assert result == filters
        except (ValueError, RecursionError):
            # Both are acceptable outcomes:
            # - ValueError means a depth cap was added (desired)
            # - RecursionError means Python's own limit triggered (current)
            pass

    def test_depth_beyond_recursion_limit(self):
        """Nesting deeper than sys.getrecursionlimit() must not crash the
        process.  It should raise either ValueError or RecursionError.
        """
        depth = sys.getrecursionlimit() + 100
        filters = _build_nested_filter(depth)
        with pytest.raises((ValueError, RecursionError)):
            _validate_filter_triples(filters)


# ---------------------------------------------------------------------------
# c) Edge cases — empty lists, single elements, mixed nesting
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Boundary conditions and unusual-but-valid filter structures."""

    def test_empty_filter_list(self):
        """An empty filter list is valid — means 'no filters'."""
        result = _validate_filter_triples([])
        assert result == []

    def test_empty_nested_group(self):
        """A logical group with an empty filters list should be valid.

        ShotGrid API accepts {"filter_operator": "any", "filters": []}.
        """
        filters = [
            {"filter_operator": "any", "filters": []},
        ]
        result = _validate_filter_triples(filters)
        assert result == filters

    def test_single_element_in_nested_group(self):
        """A logical group with exactly one filter triple."""
        filters = [
            {
                "filter_operator": "all",
                "filters": [
                    ["code", "is", "hero"],
                ],
            },
        ]
        result = _validate_filter_triples(filters)
        assert result == filters

    def test_mixed_triples_and_groups(self):
        """Filter list mixing plain triples and logical groups at top level."""
        filters = [
            ["sg_status_list", "is", "ip"],
            {
                "filter_operator": "any",
                "filters": [
                    ["code", "contains", "hero"],
                    ["code", "contains", "env"],
                ],
            },
            ["sg_asset_type", "is", "Character"],
        ]
        result = _validate_filter_triples(filters)
        assert result == filters

    def test_nested_group_with_entity_link(self):
        """Entity link validation still works inside nested groups."""
        filters = [
            {
                "filter_operator": "all",
                "filters": [
                    ["project", "is", {"type": "Project", "id": 123}],
                    ["entity", "is", {"type": "Asset", "id": 456}],
                ],
            },
        ]
        result = _validate_filter_triples(filters)
        assert result == filters

    def test_nested_group_with_invalid_entity_link_raises(self):
        """Entity link validation catches bare int inside a nested group."""
        filters = [
            {
                "filter_operator": "any",
                "filters": [
                    ["project", "is", 123],  # bare int — must fail
                ],
            },
        ]
        with pytest.raises(ValueError, match="entity link"):
            _validate_filter_triples(filters)

    def test_invalid_filter_operator_in_group(self):
        """filter_operator must be 'all' or 'any'; anything else is rejected."""
        filters = [
            {"filter_operator": "or", "filters": [["code", "is", "x"]]},
        ]
        with pytest.raises(ValueError, match="invalid filter_operator"):
            _validate_filter_triples(filters)

    def test_dict_without_filter_operator_raises(self):
        """A dict filter missing 'filter_operator' key is rejected."""
        filters = [
            {"filters": [["code", "is", "x"]]},  # missing filter_operator
        ]
        with pytest.raises(ValueError, match="filter_operator"):
            _validate_filter_triples(filters)

    def test_filters_key_not_a_list_raises(self):
        """The 'filters' value in a logical group must be a list."""
        filters = [
            {"filter_operator": "any", "filters": "not_a_list"},
        ]
        with pytest.raises(ValueError, match="must be a list"):
            _validate_filter_triples(filters)

    def test_non_triple_element_raises(self):
        """A filter that is neither a 3-element list/tuple nor a dict."""
        filters = [42]
        with pytest.raises(ValueError, match="3-element list"):
            _validate_filter_triples(filters)

    def test_two_element_list_raises(self):
        """A 2-element list is not a valid filter triple."""
        filters = [["code", "is"]]
        with pytest.raises(ValueError, match="3-element list"):
            _validate_filter_triples(filters)

    def test_four_element_list_raises(self):
        """A 4-element list is not a valid filter triple."""
        filters = [["code", "is", "hero", "extra"]]
        with pytest.raises(ValueError, match="3-element list"):
            _validate_filter_triples(filters)

    def test_tuple_triple_accepted(self):
        """Tuples should work just like lists for filter triples."""
        filters = [("code", "is", "hero")]
        result = _validate_filter_triples(filters)
        assert result == filters
