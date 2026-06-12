"""
test_reporting.py
=================
Coverage for the fpt_reporting dispatcher handlers (reporting.py).

Closes the audit gap: reporting.py shipped with ZERO test coverage on all four
handlers (_do_sg_text_search, _do_sg_summarize, _do_sg_note_thread,
_do_sg_activity) even though fpt_reporting is a daily-use production tool.

Each handler is exercised for:
  * happy path        — mocked ShotGrid returns a payload, JSON contract holds.
  * ValidationError    — malformed params return a structured ``{"error": ...}``.

text_search additionally covers the project-scope warning branch (the
SHOTGRID_PROJECT_ID-not-set guard) which is unique to that handler.

All ShotGrid I/O is mocked at the ``fpt_mcp.server`` boundary (the handlers
import the async wrappers lazily from there), so no live connection is needed.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from fpt_mcp.reporting import (
    _do_sg_activity,
    _do_sg_note_thread,
    _do_sg_summarize,
    _do_sg_text_search,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_async(coro):
    """Run an async coroutine synchronously for pytest."""
    return asyncio.run(coro)


def parse_result(result_str: str):
    """Parse the JSON result string returned by a handler."""
    return json.loads(result_str)


# ---------------------------------------------------------------------------
# text_search
# ---------------------------------------------------------------------------

class TestTextSearch:

    def test_happy_path_returns_matches(self):
        """text_search returns the SG payload verbatim when PROJECT_ID is set."""
        fake = AsyncMock(return_value={"matches": [{"type": "Asset", "id": 1}]})
        params = {"text": "robot", "entity_types": '{"Asset": []}'}
        with patch("fpt_mcp.server.PROJECT_ID", 123), \
             patch("fpt_mcp.server.sg_text_search", new=fake):
            result = parse_result(run_async(_do_sg_text_search(params)))

        assert "matches" in result
        assert result["matches"][0]["id"] == 1
        # PROJECT_ID set → no cross-project warning, and project_ids passed.
        assert "project_scope_warning" not in result
        assert fake.await_args.kwargs["project_ids"] == [123]

    def test_non_dict_results_wrapped(self):
        """A non-dict SG result is wrapped under a 'results' key."""
        fake = AsyncMock(return_value=[{"type": "Asset", "id": 7}])
        params = {"text": "x", "entity_types": '{"Asset": []}'}
        with patch("fpt_mcp.server.PROJECT_ID", 123), \
             patch("fpt_mcp.server.sg_text_search", new=fake):
            result = parse_result(run_async(_do_sg_text_search(params)))
        assert result["results"][0]["id"] == 7

    def test_project_scope_warning_when_project_id_unset(self):
        """PROJECT_ID=0 + a project-scoped entity type → scope warning added."""
        fake = AsyncMock(return_value={"matches": []})
        params = {"text": "robot", "entity_types": '{"Shot": []}'}
        with patch("fpt_mcp.server.PROJECT_ID", 0), \
             patch("fpt_mcp.server.sg_text_search", new=fake):
            result = parse_result(run_async(_do_sg_text_search(params)))

        assert "project_scope_warning" in result
        assert "Shot" in result["project_scope_warning"]
        assert "SHOTGRID_PROJECT_ID" in result["project_scope_warning"]
        # project_ids must be None when unscoped.
        assert fake.await_args.kwargs["project_ids"] is None

    def test_no_scope_warning_for_global_entity(self):
        """PROJECT_ID=0 but only a non-scoped entity → no warning."""
        fake = AsyncMock(return_value={"matches": []})
        params = {"text": "bob", "entity_types": '{"HumanUser": []}'}
        with patch("fpt_mcp.server.PROJECT_ID", 0), \
             patch("fpt_mcp.server.sg_text_search", new=fake):
            result = parse_result(run_async(_do_sg_text_search(params)))
        assert "project_scope_warning" not in result

    def test_validation_error_returns_error_payload(self):
        """Missing the required 'text' field → structured error, no exception."""
        params = {"entity_types": "{}"}  # 'text' missing
        with patch("fpt_mcp.server.sg_text_search", new=AsyncMock()):
            result = parse_result(run_async(_do_sg_text_search(params)))
        assert "error" in result
        assert "text_search" in result["error"]


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------

class TestSummarize:

    def test_happy_path_returns_aggregation(self):
        """summarize returns the SG aggregation payload."""
        fake = AsyncMock(return_value={"summaries": {"id": 5}, "groups": []})
        params = {
            "entity_type": "Task",
            "filters": "[]",
            "summary_fields": '[{"field": "id", "type": "count"}]',
        }
        with patch("fpt_mcp.server.sg_summarize", new=fake):
            result = parse_result(run_async(_do_sg_summarize(params)))

        assert result["summaries"]["id"] == 5
        # grouping omitted → None forwarded.
        assert fake.await_args.kwargs["grouping"] is None

    def test_happy_path_with_grouping(self):
        """summarize forwards a parsed grouping spec when provided."""
        fake = AsyncMock(return_value={"summaries": {"id": 2}, "groups": []})
        params = {
            "entity_type": "Task",
            "filters": '[["sg_status_list", "is", "ip"]]',
            "summary_fields": '[{"field": "id", "type": "count"}]',
            "grouping": '[{"field": "sg_status_list", "type": "exact"}]',
        }
        with patch("fpt_mcp.server.sg_summarize", new=fake):
            run_async(_do_sg_summarize(params))
        assert fake.await_args.kwargs["grouping"] == [
            {"field": "sg_status_list", "type": "exact"}
        ]

    def test_validation_error_returns_error_payload(self):
        """Missing 'summary_fields' → structured error."""
        params = {"entity_type": "Task", "filters": "[]"}
        with patch("fpt_mcp.server.sg_summarize", new=AsyncMock()):
            result = parse_result(run_async(_do_sg_summarize(params)))
        assert "error" in result
        assert "summarize" in result["error"]


# ---------------------------------------------------------------------------
# note_thread
# ---------------------------------------------------------------------------

class TestNoteThread:

    def test_happy_path_returns_thread(self):
        """note_thread returns the full reply thread list."""
        fake = AsyncMock(return_value=[
            {"type": "Note", "id": 42},
            {"type": "Reply", "id": 43},
        ])
        with patch("fpt_mcp.server.sg_note_thread_read", new=fake):
            result = parse_result(run_async(_do_sg_note_thread({"note_id": 42})))

        assert isinstance(result, list)
        assert result[1]["type"] == "Reply"
        assert fake.await_args.args[0] == 42

    def test_validation_error_returns_error_payload(self):
        """Missing 'note_id' → structured error."""
        with patch("fpt_mcp.server.sg_note_thread_read", new=AsyncMock()):
            result = parse_result(run_async(_do_sg_note_thread({})))
        assert "error" in result
        assert "note_thread" in result["error"]


# ---------------------------------------------------------------------------
# activity
# ---------------------------------------------------------------------------

class TestActivity:

    def test_happy_path_returns_stream(self):
        """activity returns the entity's activity stream payload."""
        fake = AsyncMock(return_value={"entity_id": 100, "updates": [{"id": 1}]})
        params = {"entity_type": "Shot", "entity_id": 100}
        with patch("fpt_mcp.server.sg_activity_stream_read", new=fake):
            result = parse_result(run_async(_do_sg_activity(params)))

        assert result["entity_id"] == 100
        assert result["updates"][0]["id"] == 1
        # default limit forwarded.
        assert fake.await_args.kwargs["limit"] == 20

    def test_custom_limit_forwarded(self):
        """A custom limit overrides the default."""
        fake = AsyncMock(return_value={"entity_id": 5, "updates": []})
        params = {"entity_type": "Asset", "entity_id": 5, "limit": 3}
        with patch("fpt_mcp.server.sg_activity_stream_read", new=fake):
            run_async(_do_sg_activity(params))
        assert fake.await_args.kwargs["limit"] == 3

    def test_validation_error_returns_error_payload(self):
        """Missing 'entity_id' → structured error."""
        params = {"entity_type": "Shot"}
        with patch("fpt_mcp.server.sg_activity_stream_read", new=AsyncMock()):
            result = parse_result(run_async(_do_sg_activity(params)))
        assert "error" in result
        assert "activity" in result["error"]


# ---------------------------------------------------------------------------
# Dispatcher wiring — fpt_reporting routes the action to the right handler
# ---------------------------------------------------------------------------

class TestDispatcherWiring:

    def test_fpt_reporting_routes_activity(self):
        """The fpt_reporting @mcp.tool wrapper dispatches 'activity' correctly
        and the telemetry turn-counter increments without error."""
        from fpt_mcp.server import ReportingDispatchInput, fpt_reporting

        fake = AsyncMock(return_value={"entity_id": 100, "updates": []})
        dispatch_input = ReportingDispatchInput(
            action="activity",
            params={"entity_type": "Shot", "entity_id": 100},
        )
        with patch("fpt_mcp.server.sg_activity_stream_read", new=fake):
            result = parse_result(run_async(fpt_reporting(dispatch_input)))
        assert result["entity_id"] == 100

    @pytest.mark.parametrize("action,handler_attr,payload,handler_kwargs", [
        ("text_search", "sg_text_search", {"matches": []},
         {"text": "x", "entity_types": '{"Asset": []}'}),
        ("summarize", "sg_summarize", {"summaries": {}},
         {"entity_type": "Task", "filters": "[]",
          "summary_fields": '[{"field": "id", "type": "count"}]'}),
        ("note_thread", "sg_note_thread_read", [{"type": "Note", "id": 1}],
         {"note_id": 1}),
    ])
    def test_fpt_reporting_routes_all_actions(self, action, handler_attr, payload, handler_kwargs):
        """Every reporting action reaches its handler through the dispatcher."""
        from fpt_mcp.server import ReportingDispatchInput, fpt_reporting

        fake = AsyncMock(return_value=payload)
        dispatch_input = ReportingDispatchInput(action=action, params=handler_kwargs)
        with patch("fpt_mcp.server.PROJECT_ID", 123), \
             patch(f"fpt_mcp.server.{handler_attr}", new=fake):
            out = run_async(fpt_reporting(dispatch_input))
        # Each handler returns valid JSON; the dispatcher must not mangle it.
        assert json.loads(out) is not None
        fake.assert_awaited_once()
