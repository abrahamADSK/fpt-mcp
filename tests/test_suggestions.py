"""Tests for fpt_mcp.suggestions — Phase 1 plumbing.

The registry is empty in Phase 1, so most real-code paths assert
"response returned verbatim". Each test monkeypatches a temporary rule
into SUGGESTION_RULES to exercise the helper's branches.
"""

from __future__ import annotations

import json

import pytest

from fpt_mcp import suggestions as s


@pytest.fixture
def restore_rules():
    """Snapshot SUGGESTION_RULES around each test — yields a mutable view."""
    original = dict(s.SUGGESTION_RULES)
    yield s.SUGGESTION_RULES
    s.SUGGESTION_RULES.clear()
    s.SUGGESTION_RULES.update(original)


class TestMaybeAnnotate:
    def test_unknown_tool_returns_verbatim(self):
        payload = json.dumps({"total": 2, "entities": []})
        assert s.maybe_annotate_with_suggestions("not_a_tool", payload) == payload

    def test_invalid_json_returns_verbatim(self):
        assert s.maybe_annotate_with_suggestions("sg_find", "not json at all") == "not json at all"

    def test_non_object_json_returns_verbatim(self):
        # sg_find would never return a bare array, but the guard matters
        # for future tools that might.
        payload = "[1, 2, 3]"
        assert s.maybe_annotate_with_suggestions("sg_find", payload) == payload

    def test_rule_returning_empty_list_is_noop(self, restore_rules):
        restore_rules["sg_find"] = lambda _resp: []
        payload = json.dumps({"total": 0, "entities": []})
        assert s.maybe_annotate_with_suggestions("sg_find", payload) == payload

    def test_rule_returning_suggestions_annotates_response(self, restore_rules):
        restore_rules["sg_find"] = lambda _resp: [
            {"tool": "sg_download", "reason": "download", "params_hint": {}}
        ]
        payload = json.dumps({"total": 1, "entities": [{"type": "Asset", "id": 1}]})
        out = s.maybe_annotate_with_suggestions("sg_find", payload)
        parsed = json.loads(out)
        assert parsed["entities"] == [{"type": "Asset", "id": 1}]
        assert parsed["next_suggested_actions"] == [
            {"tool": "sg_download", "reason": "download", "params_hint": {}}
        ]

    def test_already_annotated_is_idempotent(self, restore_rules):
        restore_rules["sg_find"] = lambda _resp: [
            {"tool": "other", "reason": "should not appear", "params_hint": {}}
        ]
        payload = json.dumps({
            "total": 1,
            "entities": [],
            "next_suggested_actions": [
                {"tool": "pre-existing", "reason": "kept", "params_hint": {}}
            ],
        })
        out = s.maybe_annotate_with_suggestions("sg_find", payload)
        parsed = json.loads(out)
        assert parsed["next_suggested_actions"][0]["tool"] == "pre-existing"

    def test_rule_raising_returns_verbatim(self, restore_rules):
        def boom(_resp):
            raise RuntimeError("rule bug must not reach the caller")
        restore_rules["sg_find"] = boom
        payload = json.dumps({"total": 0, "entities": []})
        assert s.maybe_annotate_with_suggestions("sg_find", payload) == payload

    def test_suggestions_capped_at_three(self, restore_rules):
        restore_rules["sg_find"] = lambda _resp: [
            {"tool": f"t{i}", "reason": "r", "params_hint": {}} for i in range(7)
        ]
        payload = json.dumps({"total": 1, "entities": [{"type": "Asset", "id": 1}]})
        parsed = json.loads(s.maybe_annotate_with_suggestions("sg_find", payload))
        assert len(parsed["next_suggested_actions"]) == 3


class TestRegistryContract:
    def test_registry_starts_empty(self):
        """Phase 1 ships no active rules — any addition is a Phase 2 change."""
        assert s.SUGGESTION_RULES == {}
