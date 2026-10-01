"""fpt_bulk(action="link_task") — link a native tk-flame delivery to its Task.

tk-flame resolves its context from the .batch path and tk-core only yields a
Task from a Task-typed schema folder, so the Version and its publishes arrive
Task-less (Chat 108). All ShotGrid I/O is mocked.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from fpt_mcp.shotgrid import _do_sg_link_task

SHOT = {"type": "Shot", "id": 2663, "name": "SEQ003_SH001"}
TASK_CMP = {"type": "Task", "id": 6775, "content": "Comp", "step": {"type": "Step", "id": 8, "name": "CMP"}}
TASK_LGT = {"type": "Task", "id": 6774, "content": "Light", "step": {"type": "Step", "id": 7, "name": "LGT"}}


def _pf(pid, code, ptype, task=None, number=1):
    return {
        "type": "PublishedFile", "id": pid, "code": code, "version_number": number,
        "task": task, "path": {"local_path": f"/proj/{code}"},
        "published_file_type": {"type": "PublishedFileType", "name": ptype},
    }


RENDER = _pf(10, "SEQ003_SH001_CMP_v001.%04d.exr", "Flame Render")
BATCH = _pf(11, "SEQ003_SH001.v001.batch", "Flame Batch File")
MOVIE = _pf(12, "SEQ003_SH001_CMP_v001.mov", "Flame Quicktime")


def _version(sg_task=None, published_files=(RENDER,), code="SEQ003_SH001_CMP_v001"):
    return {
        "type": "Version", "id": 456, "code": code, "entity": SHOT, "sg_task": sg_task,
        "published_files": [{"type": "PublishedFile", "id": p["id"]} for p in published_files],
    }


def _sg(version, tasks, publishes, all_tasks=None):
    """Patch server-level SG calls with a small in-memory model."""

    async def _find_one(entity_type, filters, fields):
        return version if entity_type == "Version" else None

    async def _find(entity_type, filters, fields, *a, **k):
        if entity_type == "Task":
            # the step-filtered query carries a dict filter; the candidates one does not
            return tasks if any(isinstance(f, dict) for f in filters) else (all_tasks or tasks)
        if entity_type == "PublishedFile":
            ids = next((f[2] for f in filters if f[0] == "id"), None)
            if ids is not None:
                return [p for p in publishes if p["id"] in ids]
            return [p for p in publishes if p.get("task") is None and p["version_number"] == 1]
        return []

    batch = AsyncMock(return_value=[])
    return (
        patch("fpt_mcp.server.sg_find_one", AsyncMock(side_effect=_find_one)),
        patch("fpt_mcp.server.sg_find", AsyncMock(side_effect=_find)),
        patch("fpt_mcp.server.sg_batch", batch),
        batch,
    )


def _run(version, tasks, publishes, params=None, all_tasks=None):
    p1, p2, p3, batch = _sg(version, tasks, publishes, all_tasks)
    with p1, p2, p3:
        out = asyncio.run(_do_sg_link_task(params or {"version_id": 456, "step": "CMP"}))
    return json.loads(out), batch


class TestLinkTask:

    def test_links_version_and_all_three_publishes_in_one_batch(self):
        """The .batch and quicktime are found although only the render hangs from the Version."""
        result, batch = _run(_version(), [TASK_CMP], [RENDER, BATCH, MOVIE])

        assert "error" not in result, result
        assert result["task"]["id"] == 6775
        assert result["written"] == 4
        assert {(r["type"], r["id"]) for r in result["linked"]} == {
            ("Version", 456), ("PublishedFile", 10), ("PublishedFile", 11), ("PublishedFile", 12)}
        batch.assert_awaited_once()
        reqs = batch.await_args[0][0]
        version_req = next(r for r in reqs if r["entity_type"] == "Version")
        assert version_req["data"] == {"sg_task": {"type": "Task", "id": 6775}}
        assert all(r["data"] == {"task": {"type": "Task", "id": 6775}}
                   for r in reqs if r["entity_type"] == "PublishedFile")

    def test_rerun_on_a_linked_delivery_writes_nothing(self):
        linked = [dict(p, task=TASK_CMP) for p in (RENDER, BATCH, MOVIE)]
        result, batch = _run(_version(sg_task=TASK_CMP, published_files=(linked[0],)),
                             [TASK_CMP], linked)

        assert result["written"] == 0
        assert len(result["already_linked"]) == 2  # Version + its render (siblings query sees no Task-less)
        batch.assert_not_awaited()

    def test_no_matching_task_returns_candidates_and_writes_nothing(self):
        result, batch = _run(_version(), [], [RENDER, BATCH], all_tasks=[TASK_LGT])

        assert "error" in result and "0 Tasks" in result["error"]
        assert result["candidates"] == [{"id": 6774, "content": "Light", "step": "LGT"}]
        batch.assert_not_awaited()

    def test_ambiguous_tasks_write_nothing(self):
        twin = dict(TASK_CMP, id=6776, content="Comp 2")
        result, batch = _run(_version(), [TASK_CMP, twin], [RENDER], all_tasks=[TASK_CMP, twin])

        assert "2 Tasks" in result["error"]
        batch.assert_not_awaited()

    def test_record_on_another_task_aborts_the_whole_write(self):
        render_on_light = dict(RENDER, task=TASK_LGT)
        result, batch = _run(_version(published_files=(render_on_light,)),
                             [TASK_CMP], [render_on_light, BATCH])

        assert "DIFFERENT Task" in result["error"]
        assert result["conflicts"][0]["id"] == 10
        assert result["linked"] == []
        assert {r["id"] for r in result["would_link"]} == {456, 11}
        batch.assert_not_awaited()

    def test_version_number_falls_back_to_the_version_code(self):
        """A Version with no published_files still finds its publishes via '_v001'."""
        result, batch = _run(_version(published_files=()), [TASK_CMP], [BATCH, MOVIE])

        assert result["version_number"] == 1
        assert result["written"] == 3

    def test_unknown_version(self):
        p1, p2, p3, batch = _sg(None, [], [])
        with p1, p2, p3:
            result = json.loads(asyncio.run(_do_sg_link_task({"version_id": 1, "step": "CMP"})))
        assert "not found" in result["error"]

    @pytest.mark.parametrize("bad", [{"version_id": 456}, {"step": "CMP"}, {"version_id": 456, "step": ""}])
    def test_invalid_params(self, bad):
        result = json.loads(asyncio.run(_do_sg_link_task(bad)))
        assert "Invalid params for link_task" in result["error"]
