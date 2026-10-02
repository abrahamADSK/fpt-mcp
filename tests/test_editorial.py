"""
test_editorial.py
=================
Unit tests for the deterministic editorial Cut / CutItem auto-calc.

Two layers, mirroring tests/test_float_coercion.py (pure helper) and
tests/test_sg_operations.py (mock-ShotGrid creation layer):

  1. The PURE math — fpt_mcp.editorial.compute_editorial_cut. No mocks, no I/O.
     Asserts exact cumulative edit ranges, per-shot source ranges, handles,
     source_start_frame, fps float-coercion, sg_cut_duration, revision_number.

  2. The Pydantic input model — fpt_mcp.models.SgEditorialInput and its
     sub-models — strict shape validation (extra='forbid', entity refs,
     positive durations, non-empty shot list).

  3. The creation layer — fpt_mcp.shotgrid._do_sg_editorial — Cut via
     sg_create, CutItems via one sg_batch transaction, project auto-link,
     Cut link injection. ShotGrid is mocked (conftest patch_sg_client).

Frame-range convention under test (see editorial.py docstring) — Autodesk's,
read from tk-multi-importcut (Chat 109):
  - edit_in/edit_out: 1-based, inclusive, contiguous/cumulative.
  - cut_item_in/cut_item_out: source_start_frame-anchored, inclusive;
    cut_item_duration == out - in + 1. Handles never touch the CutItem — they
    go on the Shot (compute_shot_handle_updates).
"""

import asyncio
import json

import pytest
from pydantic import ValidationError

from fpt_mcp.editorial import (
    DEFAULT_SOURCE_START_FRAME,
    compute_editorial_cut,
    compute_shot_handle_updates,
)
from fpt_mcp.models import EditorialCutSpec, EditorialShot, SgEditorialInput
from fpt_mcp.shotgrid import _do_sg_editorial


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_async(coro):
    """Run an async coroutine synchronously for pytest."""
    return asyncio.run(coro)


def parse_result(result_str: str):
    """Parse JSON result string from a tool call."""
    return json.loads(result_str)


SEQ = {"type": "Sequence", "id": 42}


def _shot(shot_id: int, duration: int) -> dict:
    return {"shot": {"type": "Shot", "id": shot_id}, "duration": duration}


# ===========================================================================
# 1. PURE MATH — compute_editorial_cut
# ===========================================================================

class TestPureMathSingleShot:

    def test_single_shot_default_anchor(self):
        """One 100-frame shot: cut + a single CutItem with the documented math."""
        cut, items = compute_editorial_cut(
            entity=SEQ, code="SEQ01_v1", fps=24.0, shots=[_shot(1, 100)],
        )

        # Cut-level
        assert cut == {
            "code": "SEQ01_v1",
            "entity": SEQ,
            "fps": 24.0,
            "sg_cut_duration": 100,
        }
        assert "revision_number" not in cut  # omitted when not provided

        # CutItem-level
        assert len(items) == 1
        item = items[0]
        assert item["cut_order"] == 1
        assert item["edit_in"] == 1          # 1-based timeline start
        assert item["edit_out"] == 100       # inclusive: in + duration - 1
        assert item["cut_item_in"] == 1001   # default source_start_frame
        assert item["cut_item_out"] == 1100  # inclusive: 1001 + 100 - 1
        assert item["cut_item_duration"] == 100
        assert item["shot"] == {"type": "Shot", "id": 1}
        # The cut link is injected by the creation layer, not the pure math.
        assert "cut" not in item

    def test_default_source_start_frame_constant(self):
        assert DEFAULT_SOURCE_START_FRAME == 1001


class TestPureMathMultiShotCumulation:

    def test_three_shots_cumulative_edit_ranges(self):
        """edit_in/edit_out cumulate contiguously; source ranges restart per shot."""
        durations = [100, 50, 75]
        shots = [_shot(i + 1, d) for i, d in enumerate(durations)]
        cut, items = compute_editorial_cut(
            entity=SEQ, code="cumul", fps=25.0, shots=shots,
        )

        assert cut["sg_cut_duration"] == 225  # sum of all durations

        # Exact cumulative timeline, 1-based inclusive: [1,100] [101,150] [151,225]
        expected_edit = [(1, 100), (101, 150), (151, 225)]
        assert [(it["edit_in"], it["edit_out"]) for it in items] == expected_edit

        # cut_order is 1-based and matches list order
        assert [it["cut_order"] for it in items] == [1, 2, 3]

        # Each item's source range restarts at source_start_frame (1001),
        # inclusive out = 1001 + duration - 1; it does NOT cumulate.
        assert [(it["cut_item_in"], it["cut_item_out"]) for it in items] == [
            (1001, 1100), (1001, 1050), (1001, 1075),
        ]
        assert [it["cut_item_duration"] for it in items] == durations

    def test_next_edit_in_follows_edit_out(self):
        """The cumulation is gap-free: edit_in(k+1) == edit_out(k) + 1."""
        shots = [_shot(1, 12), _shot(2, 33), _shot(3, 7), _shot(4, 200)]
        _, items = compute_editorial_cut(entity=SEQ, code="c", fps=24.0, shots=shots)
        for prev, nxt in zip(items, items[1:]):
            assert nxt["edit_in"] == prev["edit_out"] + 1
        # First starts at frame 1, last ends on the total duration (inclusive).
        assert items[0]["edit_in"] == 1
        assert items[-1]["edit_out"] == sum(s["duration"] for s in shots)
        for it, sh in zip(items, shots):
            assert it["edit_out"] - it["edit_in"] + 1 == sh["duration"]
            assert it["cut_item_out"] - it["cut_item_in"] + 1 == it["cut_item_duration"]

    def test_shot_links_preserved_in_order(self):
        shots = [_shot(10, 5), _shot(20, 5), _shot(30, 5)]
        _, items = compute_editorial_cut(entity=SEQ, code="c", fps=24.0, shots=shots)
        assert [it["shot"]["id"] for it in items] == [10, 20, 30]


class TestPureMathFpsFloat:

    def test_float_fps_passthrough(self):
        cut, _ = compute_editorial_cut(
            entity=SEQ, code="c", fps=23.976, shots=[_shot(1, 10)],
        )
        assert cut["fps"] == 23.976
        assert isinstance(cut["fps"], float)

    def test_int_fps_coerced_to_float(self):
        """A bare int fps is coerced to float so SG's Float Cut.fps never gets an int."""
        cut, _ = compute_editorial_cut(
            entity=SEQ, code="c", fps=24, shots=[_shot(1, 10)],
        )
        assert cut["fps"] == 24.0
        assert isinstance(cut["fps"], float)


class TestPureMathHandles:
    """Handles live on the Shot (Autodesk importer), never on the CutItem."""

    def test_handles_not_accepted_by_cut_math(self):
        """compute_editorial_cut has no handles parameter any more."""
        with pytest.raises(TypeError):
            compute_editorial_cut(
                entity=SEQ, code="c", fps=24.0, shots=[_shot(1, 100)], handles=8,
            )

    def test_shot_update_fields(self):
        """Inclusive cut range widened by handles into head/tail."""
        (upd,) = compute_shot_handle_updates(shots=[_shot(1, 100)], handles=8)
        assert upd["shot"] == {"type": "Shot", "id": 1}
        assert upd["data"] == {
            "sg_head_in": 993,           # 1001 - 8
            "sg_cut_in": 1001,
            "sg_cut_out": 1100,          # inclusive
            "sg_tail_out": 1108,         # 1100 + 8
            "sg_cut_duration": 100,
            "sg_working_duration": 116,  # 100 + 2*8
        }

    def test_shot_update_custom_start(self):
        (upd,) = compute_shot_handle_updates(
            shots=[_shot(1, 48)], source_start_frame=900, handles=10,
        )
        d = upd["data"]
        assert (d["sg_head_in"], d["sg_cut_in"], d["sg_cut_out"], d["sg_tail_out"]) == (
            890, 900, 947, 957,
        )

    def test_repeated_shot_gets_one_update_with_longest_use(self):
        shots = [_shot(1, 40), _shot(2, 10), _shot(1, 60)]
        updates = compute_shot_handle_updates(shots=shots, handles=5)
        assert [u["shot"]["id"] for u in updates] == [1, 2]
        assert updates[0]["data"]["sg_cut_out"] == 1001 + 60 - 1


class TestPureMathSourceStartFrame:

    def test_custom_source_start_frame(self):
        cut, items = compute_editorial_cut(
            entity=SEQ, code="c", fps=24.0, shots=[_shot(1, 100)],
            source_start_frame=0,
        )
        item = items[0]
        assert item["cut_item_in"] == 0
        assert item["cut_item_out"] == 99  # inclusive: 0 + duration - 1

class TestPureMathRevisionNumber:

    def test_revision_number_present(self):
        cut, _ = compute_editorial_cut(
            entity=SEQ, code="c", fps=24.0, shots=[_shot(1, 10)], revision_number=3,
        )
        assert cut["revision_number"] == 3

    def test_revision_number_absent_when_none(self):
        cut, _ = compute_editorial_cut(
            entity=SEQ, code="c", fps=24.0, shots=[_shot(1, 10)], revision_number=None,
        )
        assert "revision_number" not in cut


# ===========================================================================
# 2. PYDANTIC INPUT VALIDATION — SgEditorialInput + sub-models
# ===========================================================================

class TestEditorialInputValidation:

    def test_minimal_valid_input(self):
        params = SgEditorialInput(
            cut={"entity": SEQ, "code": "c", "fps": 24.0},
            shots=[{"shot": {"type": "Shot", "id": 1}, "duration": 100}],
        )
        assert params.cut.source_start_frame == 1001  # default
        assert params.cut.handles == 0                 # default
        assert params.cut.revision_number is None
        assert params.shots[0].duration == 100

    def test_fps_int_coerced(self):
        params = SgEditorialInput(
            cut={"entity": SEQ, "code": "c", "fps": 24},
            shots=[{"shot": {"type": "Shot", "id": 1}, "duration": 100}],
        )
        assert isinstance(params.cut.fps, float)
        assert params.cut.fps == 24.0

    def test_extra_field_rejected_top_level(self):
        with pytest.raises(ValidationError) as exc:
            SgEditorialInput(
                cut={"entity": SEQ, "code": "c", "fps": 24.0},
                shots=[{"shot": {"type": "Shot", "id": 1}, "duration": 1}],
                hallucinated_extra="nope",
            )
        assert "hallucinated_extra" in str(exc.value) or "extra" in str(exc.value).lower()

    def test_extra_field_rejected_in_cut_spec(self):
        with pytest.raises(ValidationError):
            EditorialCutSpec(entity=SEQ, code="c", fps=24.0, bogus=1)

    def test_empty_shots_rejected(self):
        with pytest.raises(ValidationError):
            SgEditorialInput(cut={"entity": SEQ, "code": "c", "fps": 24.0}, shots=[])

    def test_zero_duration_rejected(self):
        with pytest.raises(ValidationError):
            EditorialShot(shot={"type": "Shot", "id": 1}, duration=0)

    def test_negative_duration_rejected(self):
        with pytest.raises(ValidationError):
            EditorialShot(shot={"type": "Shot", "id": 1}, duration=-5)

    def test_non_positive_fps_rejected(self):
        with pytest.raises(ValidationError):
            EditorialCutSpec(entity=SEQ, code="c", fps=0)

    def test_shot_must_be_shot_type(self):
        """A non-Shot entity link for `shot` is rejected."""
        with pytest.raises(ValidationError) as exc:
            EditorialShot(shot={"type": "Asset", "id": 1}, duration=10)
        assert "Shot" in str(exc.value)

    def test_cut_entity_type_whitelist(self):
        """Cut entity must be Project / Sequence / Shot."""
        with pytest.raises(ValidationError):
            EditorialCutSpec(entity={"type": "Version", "id": 1}, code="c", fps=24.0)

    def test_bare_int_entity_ref_rejected(self):
        with pytest.raises(ValidationError):
            EditorialShot(shot=123, duration=10)

    def test_entity_ref_missing_id_rejected(self):
        with pytest.raises(ValidationError):
            EditorialCutSpec(entity={"type": "Sequence"}, code="c", fps=24.0)

    def test_bool_id_rejected(self):
        """A bool id (True is an int in Python) is not a valid SG id."""
        with pytest.raises(ValidationError):
            EditorialShot(shot={"type": "Shot", "id": True}, duration=10)


# ===========================================================================
# 3. CREATION LAYER — _do_sg_editorial (mocked ShotGrid)
# ===========================================================================

def _editorial_params(**overrides):
    cut = {"entity": SEQ, "code": "SEQ01_v3", "fps": 24.0}
    cut.update(overrides.pop("cut", {}))
    params = {
        "cut": cut,
        "shots": [_shot(101, 100), _shot(102, 50)],
    }
    params.update(overrides)
    return params


class TestEditorialCreationLayer:

    def test_creates_cut_and_cut_items(self, patch_sg_client):
        """_do_sg_editorial creates one Cut (sg_create) and the CutItems (sg_batch)."""
        mock_sg = patch_sg_client
        result = parse_result(run_async(_do_sg_editorial(_editorial_params())))

        assert "cut" in result
        assert result["cut"]["type"] == "Cut"
        assert result["cut_item_count"] == 2
        assert result["sg_cut_duration"] == 150
        assert len(result["cut_items"]) == 2

        # Cut created exactly once, as entity_type "Cut".
        mock_sg.create.assert_called_once()
        assert mock_sg.create.call_args[0][0] == "Cut"
        # CutItems created in a single transactional batch.
        mock_sg.batch.assert_called_once()

    def test_cut_fields_carry_computed_duration_and_float_fps(self, patch_sg_client):
        mock_sg = patch_sg_client
        run_async(_do_sg_editorial(_editorial_params()))
        cut_data = mock_sg.create.call_args[0][1]
        assert cut_data["sg_cut_duration"] == 150
        assert cut_data["fps"] == 24.0
        assert isinstance(cut_data["fps"], float)
        assert cut_data["entity"] == SEQ
        assert cut_data["code"] == "SEQ01_v3"

    def test_cut_items_linked_to_created_cut(self, patch_sg_client):
        """Every CutItem request carries the freshly created Cut's link."""
        mock_sg = patch_sg_client
        run_async(_do_sg_editorial(_editorial_params()))

        # conftest's create side_effect returns the new entity with id 9999.
        requests = mock_sg.batch.call_args[0][0]
        assert len(requests) == 2
        for req in requests:
            assert req["request_type"] == "create"
            assert req["entity_type"] == "CutItem"
            assert req["data"]["cut"] == {"type": "Cut", "id": 9999}

    def test_cut_item_requests_have_cumulative_ranges(self, patch_sg_client):
        """The batch requests carry the exact computed editorial math."""
        mock_sg = patch_sg_client
        run_async(_do_sg_editorial(_editorial_params()))
        requests = mock_sg.batch.call_args[0][0]
        data = [r["data"] for r in requests]

        assert [(d["edit_in"], d["edit_out"]) for d in data] == [(1, 100), (101, 150)]
        assert [d["cut_order"] for d in data] == [1, 2]
        assert [(d["cut_item_in"], d["cut_item_out"]) for d in data] == [
            (1001, 1100), (1001, 1050),
        ]
        assert [d["cut_item_duration"] for d in data] == [100, 50]

    def test_project_autolinked(self, patch_sg_client):
        """PROJECT_ID (123 in the fixture) is auto-linked on Cut and CutItems."""
        mock_sg = patch_sg_client
        run_async(_do_sg_editorial(_editorial_params()))

        cut_data = mock_sg.create.call_args[0][1]
        assert cut_data["project"] == {"type": "Project", "id": 123}

        for req in mock_sg.batch.call_args[0][0]:
            assert req["data"]["project"] == {"type": "Project", "id": 123}

    def test_handles_go_to_shots_in_same_batch(self, patch_sg_client):
        """handles > 0 → Shot updates appended to the CutItem transaction."""
        mock_sg = patch_sg_client
        params = _editorial_params(
            cut={"source_start_frame": 900, "handles": 5},
            shots=[_shot(101, 40)],
        )
        result = parse_result(run_async(_do_sg_editorial(params)))
        mock_sg.batch.assert_called_once()
        requests = mock_sg.batch.call_args[0][0]
        item, shot_upd = requests
        assert item["entity_type"] == "CutItem"
        assert (item["data"]["cut_item_in"], item["data"]["cut_item_out"]) == (900, 939)
        assert item["data"]["cut_item_duration"] == 40
        assert shot_upd["request_type"] == "update"
        assert shot_upd["entity_type"] == "Shot"
        assert shot_upd["entity_id"] == 101
        assert shot_upd["data"]["sg_head_in"] == 895
        assert shot_upd["data"]["sg_tail_out"] == 944
        assert result["cut_item_count"] == 1
        assert result["shots_updated_with_handles"] == 1

    def test_zero_handles_leave_shots_untouched(self, patch_sg_client):
        mock_sg = patch_sg_client
        result = parse_result(run_async(_do_sg_editorial(_editorial_params())))
        requests = mock_sg.batch.call_args[0][0]
        assert all(r["entity_type"] == "CutItem" for r in requests)
        assert result["shots_updated_with_handles"] == 0

    def test_revision_number_propagated(self, patch_sg_client):
        mock_sg = patch_sg_client
        params = _editorial_params(cut={"revision_number": 7})
        run_async(_do_sg_editorial(params))
        assert mock_sg.create.call_args[0][1]["revision_number"] == 7

    def test_invalid_params_return_error_without_touching_sg(self, patch_sg_client):
        """Bad input is rejected by validation; ShotGrid is never called."""
        mock_sg = patch_sg_client
        result = parse_result(run_async(_do_sg_editorial({"cut": {"entity": SEQ}, "shots": []})))
        assert "error" in result
        mock_sg.create.assert_not_called()
        mock_sg.batch.assert_not_called()

    def test_fps_int_coerced_before_create(self, patch_sg_client):
        """An int fps reaches sg_create as a float (pydantic + pure-math coercion)."""
        mock_sg = patch_sg_client
        params = _editorial_params(cut={"fps": 24})
        run_async(_do_sg_editorial(params))
        assert isinstance(mock_sg.create.call_args[0][1]["fps"], float)


# ===========================================================================
# 4. cut_to_edl READER — record offsets relative to the first item
# ===========================================================================

def _cut_find(edit_ins):
    """sg.find side_effect: one Cut, CutItems with the given edit_in values."""
    def find(entity_type, filters, fields=None, **kwargs):
        if entity_type == "Cut":
            return [{"type": "Cut", "id": 897, "code": "Master v1", "fps": 25.0,
                     "timecode_start_text": "01:00:00:00", "revision_number": 1}]
        if entity_type == "CutItem":
            return [
                {"type": "CutItem", "id": 1200 + i, "code": f"CUT{i:04d}",
                 "shot": {"type": "Shot", "id": 10 + i, "name": f"SH{i:03d}"},
                 "cut_order": i + 1, "cut_item_in": 1001,
                 "cut_item_duration": 100, "edit_in": e}
                for i, e in enumerate(edit_ins)
            ]
        return []
    return find


class TestCutToEdlRecordOffsets:
    """Legacy 0-based Cuts and Autodesk 1-based Cuts give the SAME EDL."""

    def _edl(self, patch_sg_client, tmp_path, edit_ins):
        from fpt_mcp.models import CutToEdlInput
        from fpt_mcp.shotgrid import cut_to_edl_impl

        patch_sg_client.find.side_effect = _cut_find(edit_ins)
        out = tmp_path / f"cut_{edit_ins[0]}.edl"
        run_async(cut_to_edl_impl(CutToEdlInput(cut_id=897, output_path=str(out))))
        return out.read_text()

    def test_zero_and_one_based_cuts_match(self, patch_sg_client, tmp_path):
        legacy = self._edl(patch_sg_client, tmp_path, [0, 100, 200])      # Cut 897
        autodesk = self._edl(patch_sg_client, tmp_path, [1, 101, 201])    # importer
        assert legacy == autodesk
        # First event starts exactly on the Cut's base timecode.
        assert "01:00:00:00 01:00:04:00" in autodesk
