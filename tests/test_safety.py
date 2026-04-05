"""
test_safety.py
==============
Pytest suite for fpt_mcp/safety.py — verifies that each of the 12 dangerous
patterns is detected, and that normal safe inputs pass through without warnings.

No ShotGrid connection or external dependencies required.
Run with:
    pytest tests/test_safety.py -v
"""

import pytest
from fpt_mcp.safety import check_dangerous


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def assert_blocked(input_str: str) -> str:
    """Assert that check_dangerous returns a warning (not None)."""
    result = check_dangerous(input_str)
    assert result is not None, (
        f"Expected a safety warning but got None for input:\n  {input_str!r}"
    )
    return result


def assert_safe(input_str: str) -> None:
    """Assert that check_dangerous returns None (no warning)."""
    result = check_dangerous(input_str)
    assert result is None, (
        f"Expected no safety warning but got:\n  {result}\n"
        f"for input:\n  {input_str!r}"
    )


# ---------------------------------------------------------------------------
# Pattern 1 — Bulk delete (sg_delete all / limit 0 / retire_all)
# ---------------------------------------------------------------------------

class TestBulkDelete:
    def test_safety_bulk_delete_all(self):
        """Pattern 1a: sg_delete ... all triggers warning."""
        assert_blocked('sg_delete all entities of type Asset')

    def test_safety_bulk_delete_limit_zero(self):
        """Pattern 1b: sg_delete with limit 0 triggers warning."""
        assert_blocked('sg_delete limit 0')

    def test_safety_bulk_delete_retire_all(self):
        """Pattern 1c: 'retire_all' keyword triggers warning."""
        assert_blocked('"retire_all": true')


# ---------------------------------------------------------------------------
# Pattern 2 — Unfiltered search (sg_find filters [] limit 0)
# ---------------------------------------------------------------------------

class TestUnfilteredSearch:
    def test_safety_unfiltered_search(self):
        """Pattern 2: sg_find with empty filters and limit 0 triggers warning."""
        assert_blocked('sg_find filters [] limit 0')

    def test_safety_unfiltered_search_spaced(self):
        """Pattern 2: whitespace variations still trigger warning."""
        assert_blocked('sg_find filters [  ] limit 0')


# ---------------------------------------------------------------------------
# Pattern 3 — Entity reference as integer (not dict)
# ---------------------------------------------------------------------------

class TestEntityAsInteger:
    def test_safety_entity_as_integer(self):
        """Pattern 3a: entity field as bare integer triggers warning."""
        assert_blocked('"entity": 123')

    def test_safety_project_as_integer(self):
        """Pattern 3b: project field as bare integer triggers warning."""
        assert_blocked('"project": 456')

    def test_safety_task_as_integer(self):
        """Pattern 3c: task field as bare integer triggers warning."""
        assert_blocked('"task": 789')


# ---------------------------------------------------------------------------
# Pattern 4 — Path traversal
# ---------------------------------------------------------------------------

class TestPathTraversal:
    def test_safety_path_traversal_unix(self):
        """Pattern 4a: Unix path traversal ../ triggers warning."""
        assert_blocked('file_path: "../../etc/passwd"')

    def test_safety_path_traversal_windows(self):
        """Pattern 4b: Windows path traversal ..\\ triggers warning."""
        assert_blocked('file_path: "..\\\\secret\\\\file.txt"')


# ---------------------------------------------------------------------------
# Pattern 5 — Schema modification
# ---------------------------------------------------------------------------

class TestSchemaModification:
    def test_safety_schema_field_create(self):
        """Pattern 5a: schema_field_create triggers warning."""
        assert_blocked('schema_field_create("Asset", "sg_custom_field", "text")')

    def test_safety_schema_field_delete(self):
        """Pattern 5b: schema_field_delete triggers warning."""
        assert_blocked('schema_field_delete("Asset", "sg_old_field")')

    def test_safety_schema_entity_create(self):
        """Pattern 5c: schema_entity_create triggers warning."""
        assert_blocked('schema_entity_create("CustomEntity")')


# ---------------------------------------------------------------------------
# Pattern 6 — Setting project to null
# ---------------------------------------------------------------------------

class TestSetProjectNull:
    def test_safety_set_project_null(self):
        """Pattern 6a: sg_update setting project to null triggers warning."""
        assert_blocked('sg_update "project": null')

    def test_safety_set_project_none(self):
        """Pattern 6b: sg_update setting project to None triggers warning."""
        assert_blocked('sg_update "project": None')


# ---------------------------------------------------------------------------
# Pattern 7 — Setting status to 'omt' (soft-delete)
# ---------------------------------------------------------------------------

class TestSetStatusOmt:
    def test_safety_set_status_omt(self):
        """Pattern 7: sg_update setting sg_status_list to 'omt' triggers warning."""
        assert_blocked('sg_update {"sg_status_list": "omt"}')

    def test_safety_set_status_omt_case_insensitive(self):
        """Pattern 7: check is case-insensitive for the surrounding keys."""
        assert_blocked('SG_UPDATE "sg_status_list": "omt"')


# ---------------------------------------------------------------------------
# Pattern 8 — Deleting a PublishedFile
# ---------------------------------------------------------------------------

class TestDeletePublishedFile:
    def test_safety_delete_published_file(self):
        """Pattern 8: sg_delete targeting PublishedFile triggers warning."""
        assert_blocked('sg_delete PublishedFile id=42')

    def test_safety_delete_published_file_dict(self):
        """Pattern 8: sg_delete with PublishedFile in JSON triggers warning."""
        assert_blocked('sg_delete {"type": "PublishedFile", "id": 7}')


# ---------------------------------------------------------------------------
# Pattern 9 — Modifying path of an existing PublishedFile
# ---------------------------------------------------------------------------

class TestUpdatePublishedFilePath:
    def test_safety_update_published_file_path(self):
        """Pattern 9: sg_update on PublishedFile changing 'path' triggers warning."""
        assert_blocked('sg_update PublishedFile {"path": "/new/location/file.ma"}')


# ---------------------------------------------------------------------------
# Pattern 10 — Invalid filter operators (LLM hallucinations)
# ---------------------------------------------------------------------------

class TestInvalidFilterOperator:
    def test_safety_operator_is_exactly(self):
        """Pattern 10a: 'is_exactly' operator triggers warning."""
        assert_blocked('filters: [["code", "is_exactly", "hero_char"]]')

    def test_safety_operator_exact(self):
        """Pattern 10b: 'exact' operator triggers warning."""
        assert_blocked('filters: [["name", "exact", "MainHero"]]')

    def test_safety_operator_matches(self):
        """Pattern 10c: 'matches' operator triggers warning."""
        assert_blocked('filters: [["sg_description", "matches", "vfx"]]')

    def test_safety_operator_regex(self):
        """Pattern 10d: 'regex' operator triggers warning."""
        assert_blocked('filters: [["code", "regex", "^hero.*"]]')

    def test_safety_operator_like(self):
        """Pattern 10e: 'like' operator triggers warning."""
        assert_blocked('filters: [["code", "like", "%hero%"]]')


# ---------------------------------------------------------------------------
# Pattern 11 — Large batch operations
# ---------------------------------------------------------------------------

class TestLargeBatch:
    def test_safety_large_batch_create(self):
        """Pattern 11a: batch create with len > 50 triggers warning."""
        assert_blocked('batch create len > 50')

    def test_safety_large_batch_delete_100(self):
        """Pattern 11b: batch delete with len > 100 triggers warning."""
        assert_blocked('batch delete len > 100')

    def test_safety_large_batch_update_1000(self):
        """Pattern 11c: batch update with len > 1000 triggers warning."""
        assert_blocked('batch update len > 1000')


# ---------------------------------------------------------------------------
# Pattern 12 — Invalid Toolkit template tokens (snake_case)
# ---------------------------------------------------------------------------

class TestInvalidTemplateToken:
    def test_safety_invalid_token_shot_name(self):
        """Pattern 12a: {shot_name} instead of {Shot} triggers warning."""
        assert_blocked('template: "/jobs/{project_name}/shots/{shot_name}/work"')

    def test_safety_invalid_token_asset_name(self):
        """Pattern 12b: {asset_name} instead of {Asset} triggers warning."""
        assert_blocked('template: "/jobs/{project_name}/{asset_name}/publish"')

    def test_safety_invalid_token_project_name(self):
        """Pattern 12c: {project_name} alone triggers warning."""
        assert_blocked('path contains {project_name}')


# ---------------------------------------------------------------------------
# Safe input — normal operations must NOT be blocked
# ---------------------------------------------------------------------------

class TestSafeInputPasses:
    def test_safety_safe_input_passes(self):
        """Normal sg_find with filters and limit must pass through safely."""
        safe_input = (
            '{"entity_type": "Asset", '
            '"filters": [["project", "is", {"type": "Project", "id": 123}], '
            '["sg_status_list", "is", "ip"]], '
            '"fields": ["id", "code", "sg_asset_type"], '
            '"limit": 50}'
        )
        assert_safe(safe_input)

    def test_safety_sg_create_passes(self):
        """Normal sg_create payload must pass through safely."""
        safe_input = (
            '{"entity_type": "Task", '
            '"data": {"content": "Rigging", '
            '"project": {"type": "Project", "id": 456}, '
            '"entity": {"type": "Asset", "id": 789}, '
            '"sg_status_list": "wtg"}}'
        )
        assert_safe(safe_input)

    def test_safety_sg_update_status_in_progress_passes(self):
        """Updating status to 'ip' (in progress) must pass through safely."""
        safe_input = 'sg_update {"sg_status_list": "ip", "id": 42}'
        assert_safe(safe_input)

    def test_safety_valid_filter_operators_pass(self):
        """Valid ShotGrid filter operators must not trigger any warning."""
        safe_input = (
            'filters: [["code", "contains", "hero"], '
            '["created_at", "greater_than", "2025-01-01"], '
            '["sg_asset_type", "in", ["Character", "Prop"]]]'
        )
        assert_safe(safe_input)

    def test_safety_correct_template_tokens_pass(self):
        """PascalCase Toolkit template tokens must pass through safely."""
        safe_input = (
            'template: "/jobs/{Project}/{Sequence}/{Shot}/work/{Step}/{name}.v{version}.{maya_extension}"'
        )
        assert_safe(safe_input)

    def test_safety_publish_absolute_path_passes(self):
        """Absolute publish path without traversal must pass through safely."""
        safe_input = '/Users/Shared/FPT_MCP/mcp_project_abraham/assets/hero/publish/model/hero.v001.ma'
        assert_safe(safe_input)


# ---------------------------------------------------------------------------
# Comprehensive — all 12 patterns trigger exactly as designed
# ---------------------------------------------------------------------------

class TestAll12Patterns:
    """
    Each tuple contains: (pattern_number, description, triggering_input).
    The test iterates all 12 and verifies each one produces a warning.
    """

    PATTERN_INPUTS = [
        (1,  "bulk delete / retire_all",
         'sg_delete all'),
        (2,  "unfiltered search no limit",
         'sg_find filters [] limit 0'),
        (3,  "entity reference as integer",
         '"project": 999'),
        (4,  "path traversal",
         '../secret/file'),
        (5,  "schema modification",
         'schema_field_create'),
        (6,  "set project to null",
         'sg_update "project": null'),
        (7,  "set status to omt",
         'sg_update "sg_status_list": "omt"'),
        (8,  "delete PublishedFile",
         'sg_delete PublishedFile'),
        (9,  "update PublishedFile path",
         'sg_update PublishedFile "path"'),
        (10, "invalid filter operator",
         '"is_exactly"'),
        (11, "large batch operation",
         'batch create len > 50'),
        (12, "invalid template token",
         '{shot_name}'),
    ]

    @pytest.mark.parametrize("pattern_num,description,trigger", PATTERN_INPUTS,
                             ids=[f"pattern_{p}" for p, _, _ in PATTERN_INPUTS])
    def test_safety_all_12_patterns(self, pattern_num, description, trigger):
        """Each of the 12 patterns triggers a warning on its designed input."""
        result = check_dangerous(trigger)
        assert result is not None, (
            f"Pattern {pattern_num} ({description}) did NOT trigger "
            f"for input: {trigger!r}"
        )
        assert "Safety check" in result or "⚠️" in result, (
            f"Pattern {pattern_num} warning message has unexpected format:\n{result}"
        )
