"""Tests for the fpt-mcp:// AMI URL parser.

Lock-in for the Chat 49 selected_ids vs ids regression. ShotGrid AMI URLs
ship two ID fields whose semantics are easy to confuse:

- ``selected_ids`` — the entity (or entities) the user actually clicked.
- ``ids`` — every entity visible in the column / page (often dozens).

The parser MUST prefer ``selected_ids``. Reading ``ids[0]`` gives a
plausible-looking but wrong ID — the user clicked Asset 1480, badge
showed 1479 because the column listed 1479,1480,1481,... and the parser
took ids[0]. These tests pin the contract so the regression cannot
return.
"""

from __future__ import annotations

import pytest

# Skip module if PySide6 is not available (CI runners may lack Qt).
pytest.importorskip("PySide6")

from fpt_mcp.qt.app import parse_protocol_url  # noqa: E402


def test_selected_ids_wins_over_ids():
    """Both fields present: selected_ids wins, ids is ignored."""
    url = (
        "fpt-mcp://chat?entity_type=Asset&project_id=1244"
        "&ids=1479,1480,1481,1482,1511,1512,1545"
        "&selected_ids=1480"
    )
    ctx, event_log_id = parse_protocol_url(url)
    assert ctx["entity_id"] == 1480, (
        "selected_ids must win over ids — Chat 49 regression. "
        f"Got entity_id={ctx['entity_id']}"
    )
    assert ctx["entity_type"] == "Asset"
    assert ctx["project_id"] == 1244
    assert event_log_id is None


def test_selected_ids_only_first_picked():
    """Multiple selected_ids: take the first (the explicitly-clicked one)."""
    url = (
        "fpt-mcp://chat?entity_type=Asset"
        "&selected_ids=1480,1481"
    )
    ctx, _ = parse_protocol_url(url)
    assert ctx["entity_id"] == 1480


def test_falls_back_to_ids_when_selected_ids_missing():
    """No selected_ids: ids becomes the only signal."""
    url = "fpt-mcp://chat?entity_type=Asset&ids=42,99"
    ctx, _ = parse_protocol_url(url)
    assert ctx["entity_id"] == 42


def test_url_encoded_comma_in_ids():
    """URL-encoded commas (%2C) decode correctly before splitting."""
    url = (
        "fpt-mcp://chat?entity_type=Asset"
        "&ids=1479%2C1480%2C1481"
        "&selected_ids=1480"
    )
    ctx, _ = parse_protocol_url(url)
    assert ctx["entity_id"] == 1480


def test_placeholder_brace_skipped():
    """Literal ShotGrid placeholders like ``{selected_ids}`` are ignored."""
    url = "fpt-mcp://chat?entity_type=Asset&selected_ids={selected_ids}&ids=99"
    ctx, _ = parse_protocol_url(url)
    # Falls through to ids when selected_ids is a placeholder.
    assert ctx["entity_id"] == 99


def test_event_log_entry_id_extracted():
    """Light Payload mode: event_log_entry_id is captured separately."""
    url = "fpt-mcp://chat?event_log_entry_id=98765"
    ctx, event_log_id = parse_protocol_url(url)
    assert event_log_id == 98765
    assert "entity_id" not in ctx


def test_real_world_shotgrid_url():
    """Full real URL from /tmp/fpt-console.log (Chat 49 reproduction).

    Asset 1480 was clicked; before the fix, parser returned 1479
    (ids[0]). After the fix, it must return 1480 (selected_ids[0]).
    """
    url = (
        "fpt-mcp://chat?user_id=24&user_login=shotgun_admin&title="
        "&entity_type=Asset&server_hostname=ableviadsk.shotgrid.autodesk.com"
        "&referrer_path=%2Fdetail%2FHumanUser%2F24&page_id=10414"
        "&session_uuid=dcceddd4-4307-11f1-97d5-0a58a9feac02"
        "&project_name=MCP_project_Abraham&project_id=1244"
        "&target_column=sg_published_files"
        "&ids=1479%2C1480%2C1481%2C1482%2C1511%2C1512%2C1545"
        "&selected_ids=1480"
    )
    ctx, event_log_id = parse_protocol_url(url)
    assert ctx["entity_id"] == 1480
    assert ctx["entity_type"] == "Asset"
    assert ctx["project_id"] == 1244
    assert ctx["project_name"] == "MCP_project_Abraham"
    assert ctx["user_login"] == "shotgun_admin"
    assert event_log_id is None
