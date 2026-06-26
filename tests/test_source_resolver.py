"""Tests for the source-media resolver (pure ranking + tool-impl wiring)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import fpt_mcp.server as server
from fpt_mcp.models import SgResolveSourceInput
from fpt_mcp.shotgrid import sg_resolve_source_impl
from fpt_mcp.source_resolver import decide, rank_candidates


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Pure: rank_candidates
# ---------------------------------------------------------------------------

def test_rank_orders_versions_then_thumbnail_then_text():
    asset = {
        "id": 1,
        "code": "DJ",
        "description": "a nightclub DJ booth",
        "image": "http://t/asset.jpg",
    }
    versions = [
        {"id": 10, "code": "DJ_v002", "image": "http://t/v2.jpg", "sg_uploaded_movie": None},
        {"id": 9, "code": "DJ_v001", "image": "http://t/v1.jpg", "sg_uploaded_movie": {"x": 1}},
    ]
    cands = rank_candidates(asset, versions)
    # 2 version images + asset thumbnail (all image tier) then 1 text
    assert [c.kind for c in cands] == ["image", "image", "image", "text"]
    # newest version first, asset thumbnail after the version stills
    assert (cands[0].entity_type, cands[0].entity_id) == ("Version", 10)
    assert cands[2].entity_type == "Asset"
    assert cands[-1].kind == "text" and cands[-1].text == "a nightclub DJ booth"


def test_video_tier_reserved_off_by_default():
    asset = {"id": 1, "code": "DJ", "description": "", "image": None}
    versions = [
        {"id": 9, "code": "v1", "image": "http://t/v1.jpg", "sg_uploaded_movie": {"x": 1}},
    ]
    # default: video disabled -> only the image is considered
    assert [c.kind for c in rank_candidates(asset, versions)] == ["image"]
    # when enabled, the movie outranks the image
    enabled = rank_candidates(asset, versions, video_enabled=True)
    assert enabled[0].kind == "video" and enabled[0].priority == 3
    assert enabled[0].field_name == "sg_uploaded_movie"


def test_rank_ignores_empty_fields():
    asset = {"id": 1, "code": "DJ", "description": "   ", "image": None}
    versions = [{"id": 9, "code": "v1", "image": None, "sg_uploaded_movie": None}]
    assert rank_candidates(asset, versions) == []


# ---------------------------------------------------------------------------
# Pure: decide
# ---------------------------------------------------------------------------

def test_decide_single_image_resolved_carries_description():
    asset = {"id": 1, "code": "DJ", "description": "booth", "image": None}
    versions = [{"id": 9, "code": "v1", "image": "http://t/v1.jpg", "sg_uploaded_movie": None}]
    d = decide(rank_candidates(asset, versions))
    assert d["status"] == "resolved"
    assert d["candidate"]["entity_id"] == 9
    assert d["text_prompt"] == "booth"  # description surfaced as companion prompt


def test_decide_multiple_images_requires_choice():
    asset = {"id": 1, "code": "DJ", "description": "", "image": None}
    versions = [
        {"id": 9, "code": "v1", "image": "http://t/v1.jpg", "sg_uploaded_movie": None},
        {"id": 8, "code": "v0", "image": "http://t/v0.jpg", "sg_uploaded_movie": None},
    ]
    d = decide(rank_candidates(asset, versions))
    assert d["status"] == "requires_choice"
    assert [c["entity_id"] for c in d["candidates"]] == [9, 8]


def test_decide_text_only_when_no_media():
    asset = {"id": 1, "code": "DJ", "description": "a neon booth", "image": None}
    d = decide(rank_candidates(asset, []))
    assert d == {"status": "text_only", "text_prompt": "a neon booth"}


def test_decide_no_source_when_empty():
    asset = {"id": 1, "code": "DJ", "description": "  ", "image": None}
    assert decide(rank_candidates(asset, [])) == {"status": "no_source"}


# ---------------------------------------------------------------------------
# Impl wiring (mock the server-level SG functions the lazy import picks up)
# ---------------------------------------------------------------------------

def test_impl_phase1_resolved_no_download(monkeypatch):
    monkeypatch.setattr(
        server, "sg_find_one",
        AsyncMock(return_value={"id": 1, "code": "DJ", "description": "booth", "image": None}),
    )
    monkeypatch.setattr(
        server, "sg_find",
        AsyncMock(return_value=[
            {"id": 9, "code": "v1", "image": "http://t/v1.jpg", "sg_uploaded_movie": None},
        ]),
    )
    out = json.loads(_run(sg_resolve_source_impl(SgResolveSourceInput(asset_id=1))))
    assert out["status"] == "resolved"
    assert out["candidate"]["entity_id"] == 9
    assert out["asset"] == {"id": 1, "code": "DJ"}


def test_impl_phase1_single_image_downloads_inline(monkeypatch, tmp_path):
    dest = str(tmp_path / "src.jpg")
    monkeypatch.setenv("FPT_MCP_ALLOWED_WRITE_ROOTS", str(tmp_path))
    monkeypatch.setattr(
        server, "sg_find_one",
        AsyncMock(side_effect=[
            {"id": 1, "code": "DJ", "description": "booth", "image": None},  # asset
            {"id": 9, "image": "http://t/v1.jpg"},                            # version (download)
        ]),
    )
    monkeypatch.setattr(
        server, "sg_find",
        AsyncMock(return_value=[
            {"id": 9, "code": "v1", "image": "http://t/v1.jpg", "sg_uploaded_movie": None},
        ]),
    )
    monkeypatch.setattr(server, "_get_tk_config", AsyncMock(return_value=None))
    dl = AsyncMock(return_value=dest)
    monkeypatch.setattr(server, "sg_download_attachment", dl)
    out = json.loads(_run(sg_resolve_source_impl(SgResolveSourceInput(asset_id=1, download_path=dest))))
    assert out["status"] == "downloaded"
    assert out["path"] == dest
    assert out["text_prompt"] == "booth"
    dl.assert_awaited_once()


def test_impl_phase2_choice_download(monkeypatch, tmp_path):
    dest = str(tmp_path / "src.jpg")
    monkeypatch.setenv("FPT_MCP_ALLOWED_WRITE_ROOTS", str(tmp_path))
    monkeypatch.setattr(
        server, "sg_find_one",
        AsyncMock(return_value={"id": 9, "image": "http://t/v1.jpg"}),
    )
    monkeypatch.setattr(server, "_get_tk_config", AsyncMock(return_value=None))
    monkeypatch.setattr(server, "sg_download_attachment", AsyncMock(return_value=dest))
    params = SgResolveSourceInput(
        asset_id=1,
        download_path=dest,
        choice={"entity_type": "Version", "entity_id": 9, "field_name": "image"},
    )
    out = json.loads(_run(sg_resolve_source_impl(params)))
    assert out["status"] == "downloaded"
    assert out["candidate"]["entity_id"] == 9


def test_impl_phase2_choice_requires_download_path(monkeypatch):
    params = SgResolveSourceInput(
        asset_id=1,
        choice={"entity_type": "Version", "entity_id": 9, "field_name": "image"},
    )
    out = json.loads(_run(sg_resolve_source_impl(params)))
    assert "error" in out and "download_path" in out["error"]


def test_impl_asset_not_found(monkeypatch):
    monkeypatch.setattr(server, "sg_find_one", AsyncMock(return_value=None))
    out = json.loads(_run(sg_resolve_source_impl(SgResolveSourceInput(asset_id=999))))
    assert "error" in out and "999" in out["error"]
