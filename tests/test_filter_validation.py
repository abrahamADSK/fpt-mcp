"""
test_filter_validation.py
=========================
Bucket C — Entity link field whitelist and operator validation tests
for _validate_filter_triples.

Tests cover:
  a) All fields in _entity_link_fields are common ShotGrid entity link fields
  b) Entity link validation: bare integer rejected, proper dict accepted
  c) The "in" operator with list of entity dicts
  d) Non-entity-link fields skip entity dict validation
"""

import pytest

from fpt_mcp.server import _validate_filter_triples


# ---------------------------------------------------------------------------
# a) Verify the whitelist contains real ShotGrid entity link fields
# ---------------------------------------------------------------------------

# Canonical set of ShotGrid entity link fields that appear in virtually
# every ShotGrid site.  This is the ground truth from the ShotGrid Python
# API documentation and standard entity schemas.
_KNOWN_SG_ENTITY_LINK_FIELDS = {
    "entity",           # polymorphic link on Task, Note, Version, etc.
    "project",          # present on nearly every entity
    "task",             # link to Task entity
    "user",             # link to HumanUser / ApiUser
    "asset",            # link to Asset
    "shot",             # link to Shot
    "sequence",         # link to Sequence
    "version",          # link to Version
    "playlist",         # link to Playlist
    "step",             # link to Step (pipeline step)
    "published_file",   # link to PublishedFile
    "parent",           # hierarchical parent (Asset, Task, etc.)
    "children",         # hierarchical children
    "linked_versions",  # multi-entity link on Playlist/Version
}


class TestEntityLinkWhitelist:
    """Verify the internal whitelist matches known ShotGrid fields."""

    def _get_whitelist(self) -> set[str]:
        """Extract _entity_link_fields from _validate_filter_triples.

        The whitelist is defined inside the function body as a local
        variable.  We probe it by testing which fields trigger entity
        dict validation.
        """
        # Fields that cause a ValueError when given a bare int with "is"
        # are in the whitelist.
        detected = set()
        candidates = _KNOWN_SG_ENTITY_LINK_FIELDS | {
            # Add some non-link fields to confirm they are NOT in the set
            "code", "sg_status_list", "description", "created_at",
            "updated_at", "sg_asset_type", "content",
        }
        for field in candidates:
            try:
                _validate_filter_triples([[field, "is", 999]])
                # No error → field is NOT in the whitelist
            except ValueError as e:
                if "entity link" in str(e):
                    detected.add(field)
                # Other ValueErrors (e.g. bad operator) don't count
        return detected

    def test_whitelist_covers_all_known_fields(self):
        """Every field in _KNOWN_SG_ENTITY_LINK_FIELDS should be
        in the function's internal whitelist.
        """
        whitelist = self._get_whitelist()
        missing = _KNOWN_SG_ENTITY_LINK_FIELDS - whitelist
        assert not missing, (
            f"These known SG entity link fields are missing from "
            f"_entity_link_fields: {sorted(missing)}"
        )

    def test_whitelist_has_no_non_entity_fields(self):
        """Non-entity fields (code, sg_status_list, etc.) must NOT be
        in the whitelist.
        """
        non_entity = {"code", "sg_status_list", "description", "created_at"}
        whitelist = self._get_whitelist()
        overlap = non_entity & whitelist
        assert not overlap, (
            f"Non-entity fields incorrectly in whitelist: {sorted(overlap)}"
        )


# ---------------------------------------------------------------------------
# b) Entity link validation: bare int rejected, proper dict accepted
# ---------------------------------------------------------------------------

class TestEntityLinkValidation:
    """Validation of entity reference format for entity link fields."""

    @pytest.mark.parametrize("field", [
        "entity", "project", "task", "user", "asset", "shot",
        "sequence", "version", "step", "parent",
    ])
    def test_bare_integer_rejected(self, field):
        """A bare integer for an entity link field with 'is' must fail."""
        with pytest.raises(ValueError, match="entity link"):
            _validate_filter_triples([[field, "is", 123]])

    @pytest.mark.parametrize("field", [
        "entity", "project", "task", "user", "asset", "shot",
        "sequence", "version", "step", "parent",
    ])
    def test_bare_string_rejected(self, field):
        """A bare string for an entity link field with 'is' must fail."""
        with pytest.raises(ValueError, match="entity link"):
            _validate_filter_triples([[field, "is", "Asset_123"]])

    @pytest.mark.parametrize("field", [
        "entity", "project", "task", "user", "asset", "shot",
    ])
    def test_proper_entity_dict_accepted(self, field):
        """A proper {"type": ..., "id": N} dict should pass."""
        entity_map = {
            "entity": "Asset",
            "project": "Project",
            "task": "Task",
            "user": "HumanUser",
            "asset": "Asset",
            "shot": "Shot",
        }
        value = {"type": entity_map.get(field, "Asset"), "id": 456}
        result = _validate_filter_triples([[field, "is", value]])
        assert result[0][2] == value

    def test_entity_dict_missing_type_rejected(self):
        """Dict without 'type' key is not a valid entity reference."""
        with pytest.raises(ValueError, match="entity link"):
            _validate_filter_triples([["project", "is", {"id": 123}]])

    def test_entity_dict_missing_id_rejected(self):
        """Dict without 'id' key is not a valid entity reference."""
        with pytest.raises(ValueError, match="entity link"):
            _validate_filter_triples([["project", "is", {"type": "Project"}]])

    def test_entity_dict_string_id_rejected(self):
        """id must be an int, not a string."""
        with pytest.raises(ValueError, match="entity link"):
            _validate_filter_triples([
                ["project", "is", {"type": "Project", "id": "123"}]
            ])

    def test_entity_dict_int_type_rejected(self):
        """type must be a string, not an int."""
        with pytest.raises(ValueError, match="entity link"):
            _validate_filter_triples([
                ["project", "is", {"type": 1, "id": 123}]
            ])

    def test_is_not_operator_also_validates(self):
        """is_not should also enforce entity dict format."""
        with pytest.raises(ValueError, match="entity link"):
            _validate_filter_triples([["project", "is_not", 123]])

    def test_is_not_with_proper_dict_passes(self):
        """is_not with a proper entity dict should succeed."""
        result = _validate_filter_triples([
            ["project", "is_not", {"type": "Project", "id": 999}]
        ])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# c) "in" operator with list of entity dicts
# ---------------------------------------------------------------------------

class TestInOperatorEntityDicts:
    """The 'in' operator requires a list of entity dicts for entity link fields."""

    def test_in_with_list_of_dicts_accepted(self):
        """A list of proper entity dicts with 'in' should pass."""
        filters = [[
            "project", "in", [
                {"type": "Project", "id": 100},
                {"type": "Project", "id": 200},
            ]
        ]]
        result = _validate_filter_triples(filters)
        assert len(result[0][2]) == 2

    def test_in_with_bare_int_list_rejected(self):
        """A list of bare integers with 'in' on an entity field must fail."""
        with pytest.raises(ValueError, match="list of entity dicts"):
            _validate_filter_triples([["project", "in", [100, 200]]])

    def test_in_with_mixed_list_rejected(self):
        """A mix of dicts and ints with 'in' must fail."""
        with pytest.raises(ValueError, match="list of entity dicts"):
            _validate_filter_triples([
                ["project", "in", [
                    {"type": "Project", "id": 100},
                    200,  # bare int
                ]]
            ])

    def test_not_in_with_list_of_dicts_accepted(self):
        """not_in with proper entity dicts should pass."""
        filters = [[
            "entity", "not_in", [
                {"type": "Asset", "id": 1},
                {"type": "Shot", "id": 2},
            ]
        ]]
        result = _validate_filter_triples(filters)
        assert result == filters

    def test_not_in_with_bare_ints_rejected(self):
        """not_in with bare integers on an entity field must fail."""
        with pytest.raises(ValueError, match="list of entity dicts"):
            _validate_filter_triples([["entity", "not_in", [1, 2, 3]]])

    def test_in_with_empty_list_accepted(self):
        """An empty list for 'in' on an entity field should pass.

        ShotGrid API accepts empty lists (returns no results).
        """
        result = _validate_filter_triples([["project", "in", []]])
        assert result[0][2] == []

    def test_in_with_non_list_rejected(self):
        """'in' operator with a non-list value on an entity field must fail."""
        with pytest.raises(ValueError, match="list of entity dicts"):
            _validate_filter_triples([
                ["project", "in", {"type": "Project", "id": 100}]
            ])


# ---------------------------------------------------------------------------
# d) Non-entity-link fields skip entity dict validation
# ---------------------------------------------------------------------------

class TestNonEntityFieldsPassThrough:
    """Fields NOT in _entity_link_fields should accept any value type."""

    def test_code_with_string(self):
        """code is a text field — string value should pass."""
        result = _validate_filter_triples([["code", "is", "hero"]])
        assert result[0][2] == "hero"

    def test_code_with_integer(self):
        """code with an integer — unusual but not entity-link-validated."""
        result = _validate_filter_triples([["code", "is", 42]])
        assert result[0][2] == 42

    def test_sg_status_list_with_string(self):
        """sg_status_list is a status field — string is normal."""
        result = _validate_filter_triples([["sg_status_list", "is", "ip"]])
        assert result[0][2] == "ip"

    def test_id_with_integer(self):
        """id is an int field — bare integer is correct."""
        result = _validate_filter_triples([["id", "is", 1234]])
        assert result[0][2] == 1234

    def test_description_with_string(self):
        """description is a text field."""
        result = _validate_filter_triples([
            ["description", "contains", "hero character"]
        ])
        assert result[0][2] == "hero character"

    def test_created_at_with_string(self):
        """created_at is a date field — string value is accepted."""
        result = _validate_filter_triples([
            ["created_at", "greater_than", "2024-01-01"]
        ])
        assert result[0][2] == "2024-01-01"

    def test_sg_asset_type_in_with_list_of_strings(self):
        """'in' on a non-entity field with a list of strings should pass."""
        result = _validate_filter_triples([
            ["sg_asset_type", "in", ["Character", "Prop", "Environment"]]
        ])
        assert len(result[0][2]) == 3

    def test_non_entity_field_with_dict_passes(self):
        """A dict value on a non-entity field is allowed (SG won't use it
        as entity validation is skipped).
        """
        result = _validate_filter_triples([
            ["sg_custom_field", "is", {"key": "value"}]
        ])
        assert result[0][2] == {"key": "value"}


# ---------------------------------------------------------------------------
# Operator validation (bonus — complements the whitelist tests)
# ---------------------------------------------------------------------------

class TestOperatorValidation:
    """Verify that invalid/hallucinated operators are caught."""

    @pytest.mark.parametrize("op", [
        "is_exactly", "matches", "like", "before_date", "after_date",
        "equals", "not_equal", "regex", "glob",
    ])
    def test_hallucinated_operators_rejected(self, op):
        """Common LLM hallucinations must be caught."""
        with pytest.raises(ValueError, match="invalid operator"):
            _validate_filter_triples([["code", op, "hero"]])

    @pytest.mark.parametrize("op", [
        "is", "is_not", "contains", "not_contains", "starts_with",
        "ends_with", "in", "not_in", "greater_than", "less_than",
        "between", "type_is", "name_contains",
    ])
    def test_valid_operators_accepted(self, op):
        """All standard ShotGrid operators should pass."""
        # Use appropriate values for range operators
        if op == "between":
            value = [1, 100]
        elif op in ("in", "not_in"):
            value = ["a", "b"]
        elif op in ("type_is", "type_is_not"):
            value = "Asset"
        else:
            value = "test"
        result = _validate_filter_triples([["code", op, value]])
        assert result[0][1] == op
