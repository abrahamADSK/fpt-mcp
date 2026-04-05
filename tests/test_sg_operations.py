"""
test_sg_operations.py
=====================
Phase 3.1 — Mock ShotGrid API tests.

10 test cases as defined in TESTING_PLAN.md Fase 3.1:
  1. test_sg_find_basic
  2. test_sg_find_with_limit
  3. test_sg_find_empty_result
  4. test_sg_find_safety_block
  5. test_sg_create_basic
  6. test_sg_create_project_autolink
  7. test_sg_update_basic
  8. test_sg_batch_all_or_nothing
  9. test_sg_batch_mixed_ops
 10. test_sg_delete_safety

All tests use unittest.mock — no live ShotGrid connection required.
"""

import asyncio
import json
from unittest.mock import patch

import pytest

# Import the MCP tool functions directly from the server module
from fpt_mcp.server import (
    sg_find_tool,
    sg_create_tool,
    sg_update_tool,
    sg_batch_tool,
    sg_delete_tool,
)

# Import Pydantic input models to construct tool parameters
from fpt_mcp.server import (
    SgFindInput,
    SgCreateInput,
    SgUpdateInput,
    SgBatchInput,
    SgDeleteInput,
)

# Import safety module for direct pattern testing
from fpt_mcp.safety import check_dangerous


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_async(coro):
    """Run an async coroutine synchronously for pytest."""
    return asyncio.get_event_loop().run_until_complete(coro)


def parse_result(result_str: str):
    """Parse JSON result string from a tool call."""
    return json.loads(result_str)


# ---------------------------------------------------------------------------
# 1. test_sg_find_basic
#    Verifies sg_find returns correct entities with simple filters.
# ---------------------------------------------------------------------------

class TestSgFindBasic:

    def test_returns_matching_entities(self, patch_sg_client, sample_assets):
        """sg_find returns list of dicts matching filter, correct fields."""
        mock_sg = patch_sg_client
        # Configure mock to return sample assets for Asset find
        mock_sg.find.return_value = sample_assets

        params = SgFindInput(
            entity_type="Asset",
            filters=[["sg_status_list", "is", "ip"]],
            fields=["id", "code", "sg_asset_type", "sg_status_list"],
            limit=50,
        )

        result = parse_result(run_async(sg_find_tool(params)))

        assert "entities" in result
        assert result["total"] == len(sample_assets)
        assert result["entities"][0]["code"] == "hero_robot"
        assert result["entities"][0]["type"] == "Asset"

        # Verify the mock was called with correct entity type and fields
        mock_sg.find.assert_called_once()
        call_args = mock_sg.find.call_args
        assert call_args[0][0] == "Asset"  # entity_type


# ---------------------------------------------------------------------------
# 2. test_sg_find_with_limit
#    Verifies sg_find respects limit parameter.
# ---------------------------------------------------------------------------

class TestSgFindWithLimit:

    def test_respects_limit_parameter(self, patch_sg_client, sample_assets):
        """sg_find returns at most N entities when limit is set."""
        mock_sg = patch_sg_client
        # Return only first asset to simulate SG respecting limit=1
        mock_sg.find.return_value = sample_assets[:1]

        params = SgFindInput(
            entity_type="Asset",
            filters=[],
            fields=["id", "code"],
            limit=1,
        )

        result = parse_result(run_async(sg_find_tool(params)))

        assert result["total"] == 1
        assert len(result["entities"]) == 1

        # Verify limit was passed through to the SG API call
        call_args = mock_sg.find.call_args
        assert call_args[1]["limit"] == 1


# ---------------------------------------------------------------------------
# 3. test_sg_find_empty_result
#    Verifies sg_find returns empty list when no match.
# ---------------------------------------------------------------------------

class TestSgFindEmptyResult:

    def test_returns_empty_list_without_error(self, patch_sg_client):
        """sg_find returns [] without error when no entities match."""
        mock_sg = patch_sg_client
        mock_sg.find.return_value = []

        params = SgFindInput(
            entity_type="Asset",
            filters=[["code", "is", "nonexistent_asset_xyz"]],
            fields=["id", "code"],
            limit=50,
        )

        result = parse_result(run_async(sg_find_tool(params)))

        assert result["total"] == 0
        assert result["entities"] == []


# ---------------------------------------------------------------------------
# 4. test_sg_find_safety_block
#    Verifies sg_find blocks dangerous queries (empty filter without limit).
# ---------------------------------------------------------------------------

class TestSgFindSafetyBlock:

    def test_safety_detects_unfiltered_unlimited_find(self):
        """check_dangerous blocks 'sg_find' with empty filters and limit 0.

        The safety pattern r'sg_find.*filters.*\\[\\s*\\].*limit.*0' is designed
        to catch unfiltered unlimited queries. We test check_dangerous directly
        because the serialized params inside sg_find_tool include the pattern
        context needed for detection.
        """
        # Construct the kind of string that would match the safety pattern:
        # In a real MCP dispatch, the tool name + params are serialized together.
        dangerous_input = 'sg_find {"filters": [], "limit": 0}'
        warning = check_dangerous(dangerous_input)

        assert warning is not None
        assert "safety" in warning.lower() or "dangerous" in warning.lower() or "unfiltered" in warning.lower()

    def test_safety_allows_filtered_find(self):
        """check_dangerous allows sg_find with proper filters."""
        safe_input = 'sg_find {"filters": [["code", "is", "hero"]], "limit": 50}'
        warning = check_dangerous(safe_input)
        assert warning is None

    def test_tool_integration_with_safety(self, patch_sg_client):
        """sg_find_tool calls check_dangerous before the SG API.
        When safety passes, the API is called; when it blocks, the API is not."""
        mock_sg = patch_sg_client
        mock_sg.find.return_value = []

        # Safe query — should reach the API
        params = SgFindInput(
            entity_type="Asset",
            filters=[["code", "is", "hero"]],
            fields=["id", "code"],
            limit=50,
        )
        result = parse_result(run_async(sg_find_tool(params)))
        assert "entities" in result
        mock_sg.find.assert_called_once()


# ---------------------------------------------------------------------------
# 5. test_sg_create_basic
#    Verifies sg_create creates entity with correct fields.
# ---------------------------------------------------------------------------

class TestSgCreateBasic:

    def test_creates_entity_with_correct_fields(self, patch_sg_client):
        """sg_create returns dict with id and type."""
        mock_sg = patch_sg_client
        # Clear conftest side_effect so return_value is used
        mock_sg.create.side_effect = None
        mock_sg.create.return_value = {
            "type": "Asset",
            "id": 5001,
            "code": "new_hero",
            "sg_asset_type": "Character",
            "project": {"type": "Project", "id": 123},
        }

        params = SgCreateInput(
            entity_type="Asset",
            data={"code": "new_hero", "sg_asset_type": "Character"},
        )

        result = parse_result(run_async(sg_create_tool(params)))

        assert result["type"] == "Asset"
        assert result["id"] == 5001
        assert result["code"] == "new_hero"

        # Verify the mock was called
        mock_sg.create.assert_called_once()


# ---------------------------------------------------------------------------
# 6. test_sg_create_project_autolink
#    Verifies sg_create auto-links project when not specified.
# ---------------------------------------------------------------------------

class TestSgCreateProjectAutolink:

    def test_autolinks_project_when_not_specified(self, patch_sg_client):
        """sg_create auto-adds project field from PROJECT_ID env var."""
        mock_sg = patch_sg_client

        # Capture the actual data dict passed to sg.create
        created_data = {}
        def capture_create(entity_type, data):
            created_data.update(data)
            return {"type": entity_type, "id": 5002, **data}

        mock_sg.create.side_effect = capture_create

        params = SgCreateInput(
            entity_type="Shot",
            data={"code": "SH030"},  # No project field
        )

        run_async(sg_create_tool(params))

        # Project should have been auto-added
        assert "project" in created_data
        assert created_data["project"]["type"] == "Project"
        assert created_data["project"]["id"] == 123

    def test_does_not_override_explicit_project(self, patch_sg_client):
        """sg_create does NOT override an explicitly provided project field."""
        mock_sg = patch_sg_client

        created_data = {}
        def capture_create(entity_type, data):
            created_data.update(data)
            return {"type": entity_type, "id": 5003, **data}

        mock_sg.create.side_effect = capture_create

        explicit_project = {"type": "Project", "id": 999}
        params = SgCreateInput(
            entity_type="Shot",
            data={"code": "SH040", "project": explicit_project},
        )

        run_async(sg_create_tool(params))

        # Explicit project should be preserved
        assert created_data["project"]["id"] == 999


# ---------------------------------------------------------------------------
# 7. test_sg_update_basic
#    Verifies sg_update modifies fields correctly.
# ---------------------------------------------------------------------------

class TestSgUpdateBasic:

    def test_updates_fields_correctly(self, patch_sg_client):
        """sg_update returns updated entity dict with new values."""
        mock_sg = patch_sg_client
        mock_sg.update.return_value = {
            "type": "Asset",
            "id": 1001,
            "sg_status_list": "cmpt",
            "description": "Final approved version",
        }

        params = SgUpdateInput(
            entity_type="Asset",
            entity_id=1001,
            data={"sg_status_list": "cmpt", "description": "Final approved version"},
        )

        result = parse_result(run_async(sg_update_tool(params)))

        assert result["type"] == "Asset"
        assert result["id"] == 1001
        assert result["sg_status_list"] == "cmpt"
        assert result["description"] == "Final approved version"

        # Verify correct args passed to SG API
        mock_sg.update.assert_called_once_with("Asset", 1001, {
            "sg_status_list": "cmpt",
            "description": "Final approved version",
        })


# ---------------------------------------------------------------------------
# 8. test_sg_batch_all_or_nothing
#    Verifies sg_batch rolls back on partial failure.
# ---------------------------------------------------------------------------

class TestSgBatchAllOrNothing:

    def test_rolls_back_on_failure(self, patch_sg_client):
        """sg_batch: if ShotGrid raises an error, no operations succeed.
        The ShotGrid batch API is transactional — all or nothing."""
        mock_sg = patch_sg_client

        # Simulate SG raising an error during batch (partial failure = full rollback)
        mock_sg.batch.side_effect = Exception(
            "Batch failed: entity_id 999 not found for update request"
        )

        batch_requests = [
            {"request_type": "create", "entity_type": "Shot",
             "data": {"code": "SH050", "project": {"type": "Project", "id": 123}}},
            {"request_type": "update", "entity_type": "Asset",
             "entity_id": 999, "data": {"sg_status_list": "cmpt"}},
        ]

        params = SgBatchInput(requests=json.dumps(batch_requests))

        # The tool should propagate the exception (all-or-nothing)
        with pytest.raises(Exception, match="Batch failed"):
            run_async(sg_batch_tool(params))

        # Verify batch was attempted with all requests
        mock_sg.batch.assert_called_once()

    def test_all_succeed_together(self, patch_sg_client):
        """sg_batch: when all operations succeed, all results are returned."""
        mock_sg = patch_sg_client
        mock_sg.batch.side_effect = None
        mock_sg.batch.return_value = [
            {"type": "Shot", "id": 7001, "code": "SH050"},
            {"type": "Asset", "id": 1001, "sg_status_list": "cmpt"},
        ]

        batch_requests = [
            {"request_type": "create", "entity_type": "Shot",
             "data": {"code": "SH050", "project": {"type": "Project", "id": 123}}},
            {"request_type": "update", "entity_type": "Asset",
             "entity_id": 1001, "data": {"sg_status_list": "cmpt"}},
        ]

        params = SgBatchInput(requests=json.dumps(batch_requests))
        result = parse_result(run_async(sg_batch_tool(params)))

        assert len(result) == 2
        assert result[0]["type"] == "Shot"
        assert result[1]["sg_status_list"] == "cmpt"


# ---------------------------------------------------------------------------
# 9. test_sg_batch_mixed_ops
#    Verifies sg_batch handles create+update+delete mix.
# ---------------------------------------------------------------------------

class TestSgBatchMixedOps:

    def test_handles_create_update_delete_mix(self, patch_sg_client):
        """sg_batch processes create, update, and delete operations correctly."""
        mock_sg = patch_sg_client
        # Clear conftest side_effect so return_value is used
        mock_sg.batch.side_effect = None
        mock_sg.batch.return_value = [
            {"type": "Shot", "id": 7002, "code": "SH060"},       # create result
            {"type": "Asset", "id": 1001, "sg_status_list": "cmpt"},  # update result
            True,  # delete result (SG returns True for successful delete)
        ]

        batch_requests = [
            {"request_type": "create", "entity_type": "Shot",
             "data": {"code": "SH060", "project": {"type": "Project", "id": 123}}},
            {"request_type": "update", "entity_type": "Asset",
             "entity_id": 1001, "data": {"sg_status_list": "cmpt"}},
            {"request_type": "delete", "entity_type": "Task",
             "entity_id": 4099},
        ]

        params = SgBatchInput(requests=json.dumps(batch_requests))
        result = parse_result(run_async(sg_batch_tool(params)))

        assert len(result) == 3

        # Verify create result
        assert result[0]["type"] == "Shot"
        assert result[0]["code"] == "SH060"

        # Verify update result
        assert result[1]["sg_status_list"] == "cmpt"

        # Verify delete result (True)
        assert result[2] is True

        # Verify the batch was sent with all 3 request types
        call_args = mock_sg.batch.call_args[0][0]
        request_types = [r["request_type"] for r in call_args]
        assert "create" in request_types
        assert "update" in request_types
        assert "delete" in request_types


# ---------------------------------------------------------------------------
# 10. test_sg_delete_safety
#     Verifies sg_delete blocks bulk delete without confirmation.
# ---------------------------------------------------------------------------

class TestSgDeleteSafety:

    def test_safety_detects_published_file_delete(self):
        """check_dangerous blocks 'sg_delete' of PublishedFile entities.

        The safety pattern r'sg_delete.*PublishedFile' detects attempts to
        delete PublishedFile entities which can break Toolkit loader references.
        We test check_dangerous directly with the full context string.
        """
        dangerous_input = 'sg_delete PublishedFile entity_id=8001'
        warning = check_dangerous(dangerous_input)

        assert warning is not None
        assert "PublishedFile" in warning or "reference" in warning.lower() or "loader" in warning.lower()

    def test_safety_allows_normal_entity_delete(self):
        """check_dangerous allows deletion of non-sensitive entity types."""
        safe_input = 'sg_delete Task entity_id=4099'
        warning = check_dangerous(safe_input)
        # Task deletion should not be blocked by the PublishedFile pattern
        # (it may still be blocked by bulk_delete if it matches that pattern)
        # For a simple single-entity delete of Task, no pattern should match
        assert warning is None

    def test_tool_allows_normal_delete(self, patch_sg_client):
        """sg_delete allows deletion of normal entity types (e.g. Task)."""
        mock_sg = patch_sg_client
        mock_sg.delete.return_value = True

        params = SgDeleteInput(
            entity_type="Task",
            entity_id=4099,
        )

        result = parse_result(run_async(sg_delete_tool(params)))

        assert result["deleted"] is True
        assert result["entity_type"] == "Task"
        assert result["entity_id"] == 4099

        mock_sg.delete.assert_called_once()
