"""
test_session_stats.py
=====================
Unit tests for the per-session stats reset helpers + F0 telemetry
(`fpt_mcp._session_stats`) and their server.py wiring. Ported from flame-mcp
in 3C Wave 2.
"""

from __future__ import annotations

import asyncio
import datetime
import json
from pathlib import Path

from fpt_mcp._session_stats import (
    DEFAULT_IDLE_RESET_SECONDS,
    TELEMETRY_MAX_BYTES,
    apply_idle_reset,
    make_empty_stats,
    persist_timing,
    persist_turn,
    reset_stats,
    should_auto_reset,
)


def _dt(hour: int = 10, minute: int = 0, second: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 5, 21, hour, minute, second)


def _run(coro):
    return asyncio.run(coro)


# ── make_empty_stats ────────────────────────────────────────────────────────

def test_empty_stats_has_all_canonical_keys() -> None:
    """Zero template must carry exactly the keys the server consumes.
    cache_hits is deliberately absent (rag.search owns it)."""
    stats = make_empty_stats()
    expected = {
        "exec_calls", "tokens_in", "tokens_out", "rag_calls", "rag_skipped",
        "tokens_saved", "patterns_learned", "patterns_staged", "safety_blocks",
        # F0: p_fallo counters (3C Wave 2).
        "turns_total", "failed_turns",
        "timings",
    }
    assert set(stats.keys()) == expected


def test_empty_stats_has_no_cache_hits_key() -> None:
    """cache_hits is surfaced from rag.search, not stored in _stats."""
    assert "cache_hits" not in make_empty_stats()


def test_empty_stats_counters_are_zero() -> None:
    stats = make_empty_stats()
    for key, value in stats.items():
        if key == "timings":
            assert value == []
        else:
            assert value == 0, f"counter {key} not zeroed"


def test_empty_stats_matches_server_schema() -> None:
    """The schema invariant in code form: the live _stats dict must carry
    exactly the keys make_empty_stats produces."""
    from fpt_mcp import server
    assert set(server._stats.keys()) == set(make_empty_stats().keys())


# ── persist_timing / persist_turn ──────────────────────────────────────────

def test_persist_timing_writes_one_jsonl_line(tmp_path: Path) -> None:
    log = tmp_path / "timings.jsonl"
    persist_timing(log, {"op": "fpt_bulk", "total_ms": 12})
    contents = log.read_text(encoding="utf-8").splitlines()
    assert len(contents) == 1
    assert json.loads(contents[0]) == {"op": "fpt_bulk", "total_ms": 12}


def test_persist_timing_appends_across_calls(tmp_path: Path) -> None:
    log = tmp_path / "timings.jsonl"
    persist_timing(log, {"op": "fpt_bulk", "n": 1})
    persist_timing(log, {"op": "fpt_reporting", "n": 2})
    lines = log.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["n"] for line in lines] == [1, 2]


def test_persist_timing_creates_parent_directory(tmp_path: Path) -> None:
    log = tmp_path / "nested" / "dir" / "timings.jsonl"
    persist_timing(log, {"op": "fpt_bulk"})
    assert log.exists()


def test_persist_timing_rotates_when_oversized(tmp_path: Path) -> None:
    log = tmp_path / "timings.jsonl"
    rotated = tmp_path / "timings.jsonl.1"
    rotated.write_text("STALE\n", encoding="utf-8")
    log.write_bytes(b"X" * (TELEMETRY_MAX_BYTES + 1))

    persist_timing(log, {"op": "fpt_bulk", "after": "rotation"})

    assert "STALE" not in rotated.read_text(encoding="utf-8")
    new_lines = log.read_text(encoding="utf-8").splitlines()
    assert len(new_lines) == 1
    assert json.loads(new_lines[0])["after"] == "rotation"


def test_persist_timing_swallows_io_errors(tmp_path: Path) -> None:
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    log = blocker / "timings.jsonl"
    persist_timing(log, {"op": "fpt_bulk"})  # must not raise


def test_persist_timing_handles_non_serialisable_values(tmp_path: Path) -> None:
    log = tmp_path / "timings.jsonl"
    persist_timing(log, {"op": "fpt_bulk", "ts": datetime.datetime(2026, 5, 13, 12, 0, 0)})
    parsed = json.loads(log.read_text(encoding="utf-8"))
    assert "2026-05-13" in parsed["ts"]


def test_persist_turn_delegates_to_persist_timing(tmp_path: Path) -> None:
    log = tmp_path / "turns.jsonl"
    persist_turn(log, {"model": "claude-opus", "exit_code": 0})
    parsed = json.loads(log.read_text(encoding="utf-8"))
    assert parsed == {"model": "claude-opus", "exit_code": 0}


# ── should_auto_reset / apply_idle_reset / reset_stats ───────────────────────

def test_should_auto_reset_false_on_first_call() -> None:
    assert should_auto_reset(_dt(), None) is False


def test_should_auto_reset_false_within_idle_window() -> None:
    assert should_auto_reset(_dt(10, 30, 0), _dt(10, 29, 0)) is False


def test_should_auto_reset_true_at_exact_threshold() -> None:
    now = _dt(10, 30, 0)
    last = now - datetime.timedelta(seconds=DEFAULT_IDLE_RESET_SECONDS)
    assert should_auto_reset(now, last) is True


def test_should_auto_reset_custom_threshold() -> None:
    now, last = _dt(10, 0, 10), _dt(10, 0, 0)
    assert should_auto_reset(now, last, idle_reset_seconds=5) is True
    assert should_auto_reset(now, last, idle_reset_seconds=15) is False


def test_apply_idle_reset_does_nothing_within_window() -> None:
    stats = make_empty_stats()
    stats["exec_calls"] = 7
    did, _ = apply_idle_reset(stats, _dt(10, 5, 0), _dt(10, 0, 0))
    assert did is False
    assert stats["exec_calls"] == 7


def test_apply_idle_reset_zeros_counters_past_window() -> None:
    stats = make_empty_stats()
    original_id = id(stats)
    stats["turns_total"] = 10
    stats["failed_turns"] = 3
    stats["timings"].append({"op": "fpt_bulk"})

    did, reset_at = apply_idle_reset(stats, _dt(12, 0, 0), _dt(10, 0, 0))

    assert did is True
    assert reset_at == _dt(12, 0, 0)
    assert id(stats) == original_id, "dict identity must be preserved"
    assert stats["turns_total"] == 0
    assert stats["failed_turns"] == 0
    assert stats["timings"] == []


def test_apply_idle_reset_ignores_first_call() -> None:
    stats = make_empty_stats()
    stats["exec_calls"] = 3
    did, _ = apply_idle_reset(stats, _dt(23, 59, 0), None)
    assert did is False
    assert stats["exec_calls"] == 3


def test_reset_stats_clears_unconditionally() -> None:
    stats = make_empty_stats()
    stats["exec_calls"] = 10
    stats["timings"].append({"op": "fpt_bulk"})
    reset_at = reset_stats(stats, _dt(10, 0, 1))
    assert reset_at == _dt(10, 0, 1)
    assert stats["exec_calls"] == 0
    assert stats["timings"] == []


# ── server.py wiring (F0 turns_total / failed_turns) ─────────────────────────

def test_result_is_error_classification() -> None:
    """_result_is_error flags error / safety_warning payloads only."""
    from fpt_mcp import server
    assert server._result_is_error(json.dumps({"error": "boom"})) is True
    assert server._result_is_error(json.dumps({"safety_warning": "no"})) is True
    assert server._result_is_error(json.dumps({"deleted": True})) is False
    assert server._result_is_error("not json at all") is False
    assert server._result_is_error(json.dumps([1, 2, 3])) is False


def test_fpt_bulk_increments_turns_total_on_success(monkeypatch) -> None:
    """A successful dispatcher call counts one turn and zero failures."""
    from fpt_mcp import server
    from fpt_mcp.models import BulkDispatchInput

    async def ok_handler(params):
        return json.dumps({"deleted": True})

    monkeypatch.setattr(server, "_do_sg_delete", ok_handler)
    server._stats.update(make_empty_stats())

    _run(server.fpt_bulk(BulkDispatchInput(action="delete",
                                           params={"entity_type": "Task", "entity_id": 1})))

    assert server._stats["turns_total"] == 1
    assert server._stats["failed_turns"] == 0
    assert server._stats["timings"][-1]["error"] is False


def test_fpt_bulk_increments_failed_turns_on_error(monkeypatch) -> None:
    """An error payload counts one turn AND one failed turn → p_fallo = 1."""
    from fpt_mcp import server
    from fpt_mcp.models import BulkDispatchInput

    async def err_handler(params):
        return json.dumps({"error": "validation failed"})

    monkeypatch.setattr(server, "_do_sg_delete", err_handler)
    server._stats.update(make_empty_stats())

    _run(server.fpt_bulk(BulkDispatchInput(action="delete",
                                           params={"entity_type": "Task", "entity_id": 1})))

    assert server._stats["turns_total"] == 1
    assert server._stats["failed_turns"] == 1
    assert server._stats["timings"][-1]["error"] is True


def test_reset_session_stats_tool_zeroes_counters() -> None:
    from fpt_mcp import server

    server._stats.update(make_empty_stats())
    server._stats["exec_calls"] = 12
    server._stats["turns_total"] = 5

    out = _run(server.reset_session_stats_tool())

    assert json.loads(out)["status"] == "reset"
    assert server._stats["exec_calls"] == 0
    assert server._stats["turns_total"] == 0


def test_session_stats_reports_p_fallo() -> None:
    from fpt_mcp import server

    server._stats.update(make_empty_stats())
    server._stats["turns_total"] = 4
    server._stats["failed_turns"] = 1

    parsed = json.loads(_run(server.session_stats_tool()))

    assert parsed["dispatcher_turns"] == 4
    assert parsed["failed_turns"] == 1
    assert parsed["p_fallo"] == "25%"
