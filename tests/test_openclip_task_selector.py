"""Tests for the openclip_create Task/Step selection contract (Chat 93).

The tool must NEVER guess which pipeline step feeds the conform:

  * no selector  -> choice_required with candidates grouped by Task and a
                    dependency-based suggestion (Task.upstream_tasks) when
                    the production fills dependencies — never builds.
  * task_id      -> exact Task filter appended to the publish query.
  * step         -> filter_operator "any" block matching the Step's code
                    OR short_name.
  * zero matches -> error that lists the real candidates (no dead ends).

LGT and comp renders share publish_type ("Rendered Image") by design —
these tests pin the behaviour that keeps them from mixing in one clip.
"""

import asyncio
import json

import pytest

from fpt_mcp.models import OpenclipCreateInput
from fpt_mcp.shotgrid import openclip_create_impl


def _run(coro):
    return asyncio.run(coro)


PUB_LGT_2 = {
    "code": "SEQ001_010_LGT.v002",
    "version_number": 2,
    "task": {"type": "Task", "id": 11, "name": "Light"},
    "task.Task.step": {"type": "Step", "id": 5, "name": "Light"},
}
PUB_LGT_3 = {
    "code": "SEQ001_010_LGT.v003",
    "version_number": 3,
    "task": {"type": "Task", "id": 11, "name": "Light"},
    "task.Task.step": {"type": "Step", "id": 5, "name": "Light"},
}
PUB_CMP_1 = {
    "code": "SEQ001_010_CMP.v001",
    "version_number": 1,
    "task": {"type": "Task", "id": 22, "name": "Comp"},
    "task.Task.step": {"type": "Step", "id": 8, "name": "Comp"},
}
PUB_UNTASKED = {
    "code": "SEQ001_010_stray.v001",
    "version_number": 1,
    "task": None,
    "task.Task.step": None,
}

TASK_COMP_DEPENDS_ON_LIGHT = {
    "type": "Task", "id": 22, "content": "Comp",
    "step": {"type": "Step", "id": 8, "name": "Comp"},
    "upstream_tasks": [{"type": "Task", "id": 11, "name": "Light"}],
}
TASK_LIGHT_NO_DEPS = {
    "type": "Task", "id": 11, "content": "Light",
    "step": {"type": "Step", "id": 5, "name": "Light"},
    "upstream_tasks": [],
}


class FakeSgFind:
    """Queue-driven async sg_find stand-in that records every call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, entity_type, filters, fields, order=None,
                       limit=0):
        self.calls.append({"entity_type": entity_type, "filters": filters,
                           "fields": fields})
        if not self.responses:
            raise AssertionError("unexpected extra sg_find call")
        return self.responses.pop(0)


@pytest.fixture()
def patch_sg(monkeypatch):
    def _install(responses, binary="/opt/Autodesk/mio/2027/dl_get_media_info"):
        fake = FakeSgFind(responses)
        import fpt_mcp.server as server_mod
        import fpt_mcp.shotgrid as sg_mod
        monkeypatch.setattr(server_mod, "sg_find", fake)
        monkeypatch.setattr(sg_mod, "_find_dl_media_binary", lambda: binary)
        return fake
    return _install


def _create(**kwargs):
    return openclip_create_impl(
        OpenclipCreateInput(shot_id=101, output_path="/tmp/x.clip", **kwargs))


# ── discovery mode (no selector) ─────────────────────────────────────────────


def test_no_selector_returns_choice_required(patch_sg):
    patch_sg([
        [PUB_LGT_2, PUB_LGT_3, PUB_CMP_1],                  # candidates query
        [TASK_LIGHT_NO_DEPS, TASK_COMP_DEPENDS_ON_LIGHT],   # task graph
    ])
    out = json.loads(_run(_create()))
    assert out["choice_required"] is True
    by_id = {c["task_id"]: c for c in out["candidates"]}
    assert by_id[11]["publishes"] == 2
    assert by_id[11]["latest_version"] == 3
    assert by_id[11]["step"] == "Light"
    assert by_id[22]["publishes"] == 1
    assert out["untasked_publishes"] == 0


def test_dependency_singles_out_suggestion(patch_sg):
    patch_sg([
        [PUB_LGT_2, PUB_LGT_3, PUB_CMP_1],
        [TASK_LIGHT_NO_DEPS, TASK_COMP_DEPENDS_ON_LIGHT],
    ])
    out = json.loads(_run(_create()))
    # Light (11) is upstream of Comp only; Comp (22) is upstream of nothing.
    assert out["suggested"]["task_id"] == 11
    assert "Comp" in out["suggested"]["reason"]


def test_no_dependency_data_means_no_suggestion(patch_sg):
    no_deps = {**TASK_COMP_DEPENDS_ON_LIGHT, "upstream_tasks": []}
    patch_sg([
        [PUB_LGT_2, PUB_CMP_1],
        [TASK_LIGHT_NO_DEPS, no_deps],
    ])
    out = json.loads(_run(_create()))
    assert out["choice_required"] is True
    assert out["suggested"] is None


def test_untasked_publishes_are_counted_not_dropped(patch_sg):
    patch_sg([
        [PUB_LGT_2, PUB_UNTASKED],
        [TASK_LIGHT_NO_DEPS],
    ])
    out = json.loads(_run(_create()))
    assert out["untasked_publishes"] == 1
    assert len(out["candidates"]) == 1


def test_discovery_works_without_mio_install(patch_sg):
    """Enumerating options is pure ShotGrid I/O — must not require Flame."""
    patch_sg([
        [PUB_LGT_2],
        [TASK_LIGHT_NO_DEPS],
    ], binary=None)
    out = json.loads(_run(_create()))
    assert out["choice_required"] is True


def test_discovery_empty_shot_is_plain_error(patch_sg):
    patch_sg([[]])
    out = json.loads(_run(_create()))
    assert "error" in out and "choice_required" not in out


# ── explicit selectors ───────────────────────────────────────────────────────


def test_task_id_appends_exact_task_filter(patch_sg):
    fake = patch_sg([
        [],                      # filtered publish query -> no matches
        [PUB_LGT_2, PUB_CMP_1],  # candidates listing for the error payload
    ])
    out = json.loads(_run(_create(task_id=99)))
    filters = fake.calls[0]["filters"]
    assert ["task", "is", {"type": "Task", "id": 99}] in filters
    assert "task_id=99" in out["error"]
    assert {c["task_id"] for c in out["candidates"]} == {11, 22}


def test_step_matches_code_or_short_name(patch_sg):
    fake = patch_sg([
        [],
        [PUB_LGT_2],
    ])
    _run(_create(step="LGT"))
    complex_blocks = [f for f in fake.calls[0]["filters"]
                      if isinstance(f, dict)]
    assert len(complex_blocks) == 1
    block = complex_blocks[0]
    assert block["filter_operator"] == "any"
    assert ["task.Task.step.Step.code", "is", "LGT"] in block["filters"]
    assert ["task.Task.step.Step.short_name", "is", "LGT"] in block["filters"]


def test_task_id_takes_precedence_over_step(patch_sg):
    fake = patch_sg([
        [],
        [PUB_LGT_2],
    ])
    out = json.loads(_run(_create(task_id=11, step="Comp")))
    filters = fake.calls[0]["filters"]
    assert ["task", "is", {"type": "Task", "id": 11}] in filters
    assert not any(isinstance(f, dict) for f in filters)
    assert "task_id=11" in out["error"]


def test_explicit_selector_still_requires_mio(patch_sg):
    """The build path keeps the canonical-generator requirement."""
    fake = patch_sg([], binary=None)
    out = json.loads(_run(_create(task_id=11)))
    assert "dl_get_media_info not found" in out["error"]
    assert fake.calls == []
