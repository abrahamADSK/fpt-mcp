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
    def test_registry_has_phase2_rules(self):
        """Phase 2 wires rules for the four user-facing mutation/query tools.

        Additions beyond these four are a Phase 3 change and should land
        with an explicit doc + concept-invariant update.
        """
        assert set(s.SUGGESTION_RULES) == {
            "sg_find", "sg_download", "tk_publish", "fpt_bulk",
        }


class TestSgFindRule:
    def test_empty_entities_returns_no_suggestions(self):
        assert s._suggest_after_sg_find({"total": 0, "entities": []}) == []

    def test_asset_with_image_suggests_download_and_vision3d(self):
        resp = {"total": 1, "entities": [
            {"type": "Asset", "id": 42, "image": "https://…/ref.png"}
        ]}
        out = s._suggest_after_sg_find(resp)
        tools = [x["tool"] for x in out]
        assert "sg_download" in tools and "maya_vision3d" in tools
        dl = next(x for x in out if x["tool"] == "sg_download")
        assert dl["params_hint"]["entity_id"] == 42

    def test_asset_without_image_no_suggestions(self):
        resp = {"total": 1, "entities": [{"type": "Asset", "id": 7}]}
        assert s._suggest_after_sg_find(resp) == []

    def test_task_row_suggests_activity_stream(self):
        resp = {"total": 1, "entities": [{"type": "Task", "id": 100}]}
        out = s._suggest_after_sg_find(resp)
        assert len(out) == 1
        assert out[0]["tool"] == "fpt_reporting"
        assert out[0]["params_hint"] == {
            "action": "activity", "entity_type": "Task", "entity_id": 100
        }

    def test_version_row_suggests_activity_stream(self):
        resp = {"total": 1, "entities": [{"type": "Version", "id": 200}]}
        out = s._suggest_after_sg_find(resp)
        assert out and out[0]["tool"] == "fpt_reporting"
        assert out[0]["params_hint"]["entity_type"] == "Version"

    def test_shot_row_no_activity_suggestion(self):
        resp = {"total": 1, "entities": [{"type": "Shot", "id": 10}]}
        assert s._suggest_after_sg_find(resp) == []


class TestSgDownloadRule:
    def test_image_path_suggests_vision3d(self):
        resp = {"path": "/tmp/ref.png", "entity_type": "Asset", "entity_id": 42}
        out = s._suggest_after_sg_download(resp)
        assert len(out) == 1
        assert out[0]["tool"] == "maya_vision3d"
        assert out[0]["params_hint"]["image_path"] == "/tmp/ref.png"

    def test_uppercase_extension_is_recognised(self):
        resp = {"path": "/tmp/REF.JPEG", "entity_type": "Asset", "entity_id": 1}
        assert s._suggest_after_sg_download(resp) != []

    def test_non_image_no_suggestion(self):
        resp = {"path": "/tmp/notes.pdf", "entity_type": "Asset", "entity_id": 1}
        assert s._suggest_after_sg_download(resp) == []

    def test_error_response_no_suggestion(self):
        assert s._suggest_after_sg_download({"error": "nope"}) == []


class TestTkPublishRule:
    def test_successful_publish_emits_activity(self):
        resp = {
            "id": 5, "code": "asset_model_v001",
            "entity": {"type": "Asset", "id": 42, "code": "hero"},
        }
        out = s._suggest_after_tk_publish(resp)
        tools = [x["tool"] for x in out]
        assert "fpt_reporting" in tools
        # Asset publish does NOT trigger the Shot-sibling sub-suggestion.
        assert "sg_find" not in tools

    def test_shot_publish_also_emits_sg_find(self):
        resp = {
            "id": 9, "code": "sh010_comp_v001",
            "entity": {"type": "Shot", "id": 555, "code": "SH010"},
        }
        out = s._suggest_after_tk_publish(resp)
        assert {x["tool"] for x in out} == {"fpt_reporting", "sg_find"}

    def test_error_response_no_suggestion(self):
        assert s._suggest_after_tk_publish({"error": "not found"}) == []

    def test_missing_entity_no_suggestion(self):
        assert s._suggest_after_tk_publish({"id": 1}) == []


class TestFptBulkRule:
    def test_delete_success_suggests_revive(self):
        resp = {"deleted": True, "entity_type": "Shot", "entity_id": 99}
        out = s._suggest_after_fpt_bulk(resp)
        assert len(out) == 1
        assert out[0]["tool"] == "fpt_bulk"
        assert out[0]["params_hint"]["action"] == "revive"
        assert out[0]["params_hint"]["params"] == {
            "entity_type": "Shot", "entity_id": 99,
        }

    def test_delete_false_no_suggestion(self):
        resp = {"deleted": False, "entity_type": "Shot", "entity_id": 99}
        assert s._suggest_after_fpt_bulk(resp) == []

    def test_batch_response_no_suggestion(self):
        # Batch responses don't carry the `deleted: true` shape.
        assert s._suggest_after_fpt_bulk({"batch_results": [1, 2, 3]}) == []


class TestKillSwitch:
    def test_env_var_disables_annotation(self, monkeypatch, restore_rules):
        restore_rules["sg_find"] = lambda _resp: [
            {"tool": "x", "reason": "y", "params_hint": {}}
        ]
        monkeypatch.setenv("FPT_MCP_DISABLE_SUGGESTIONS", "1")
        payload = json.dumps({"total": 1, "entities": [{"type": "Asset", "id": 1}]})
        assert s.maybe_annotate_with_suggestions("sg_find", payload) == payload

    def test_env_var_unset_normal_behaviour(self, restore_rules):
        restore_rules["sg_find"] = lambda _resp: [
            {"tool": "x", "reason": "y", "params_hint": {}}
        ]
        payload = json.dumps({"total": 1, "entities": [{"type": "Asset", "id": 1}]})
        out = s.maybe_annotate_with_suggestions("sg_find", payload)
        assert "next_suggested_actions" in json.loads(out)
