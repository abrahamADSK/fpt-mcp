"""Tests for the Chat 91 conform-support pure functions:
editorial.frames_to_timecode / build_edl and openclip.build_openclip.

The EDL fixtures mirror the REAL "Master v1" Cut (id 897): 6 CutItems,
25 fps, base timecode 01:00:00:00, every shot full-range 1001+100.
"""
import xml.etree.ElementTree as ET

import pytest

from fpt_mcp.editorial import build_edl, frames_to_timecode, timecode_to_frames
from fpt_mcp.openclip import build_openclip

SHOTS = ["SEQ001_SH001", "SEQ001_SH002", "SEQ002_SH001",
         "SEQ002_SH002", "SEQ003_SH001", "SEQ003_SH002"]


def _master_v1_events():
    return [
        {"tape": s, "clip_name": f"{s}_light_v001",
         "src_in_frame": 1001, "duration": 100, "rec_in_frame": i * 100}
        for i, s in enumerate(SHOTS)
    ]


class TestTimecode:
    def test_known_values(self):
        assert frames_to_timecode(0, 25) == "00:00:00:00"
        assert frames_to_timecode(1001, 25) == "00:00:40:01"
        assert frames_to_timecode(90000, 25) == "01:00:00:00"
        assert frames_to_timecode(90100, 25) == "01:00:04:00"

    def test_roundtrip(self):
        for f in (0, 1, 24, 25, 1001, 90000, 123456):
            assert timecode_to_frames(frames_to_timecode(f, 25), 25) == f

    def test_other_fps(self):
        assert frames_to_timecode(24, 24) == "00:00:01:00"


class TestBuildEdl:
    def test_master_v1_structure(self):
        edl = build_edl("Master v1", 25, "01:00:00:00", _master_v1_events())
        lines = edl.splitlines()
        assert lines[0] == "TITLE: Master v1"
        assert lines[1] == "FCM: NON-DROP FRAME"
        events = [ln for ln in lines if ln[:3].isdigit()]
        assert len(events) == 6

    def test_source_and_record_timecodes(self):
        edl = build_edl("Master v1", 25, "01:00:00:00", _master_v1_events())
        events = [ln for ln in edl.splitlines() if ln[:3].isdigit()]
        # event 1: source 1001..1101 (exclusive out), record from base
        assert "00:00:40:01 00:00:44:01" in events[0]
        assert "01:00:00:00 01:00:04:00" in events[0]
        # event 3 (SEQ002_SH001): record edit_in 200 -> base + 8s
        assert "01:00:08:00 01:00:12:00" in events[2]
        # last event ends exactly at base + 600 frames = +24s
        assert "01:00:20:00 01:00:24:00" in events[5]

    def test_events_are_contiguous(self):
        edl = build_edl("Master v1", 25, "01:00:00:00", _master_v1_events())
        events = [ln for ln in edl.splitlines() if ln[:3].isdigit()]
        rec_pairs = [ln.split()[-2:] for ln in events]
        for (prev, cur) in zip(rec_pairs, rec_pairs[1:]):
            assert prev[1] == cur[0]  # rec_out(k) == rec_in(k+1)

    def test_from_clip_name_comments(self):
        edl = build_edl("Master v1", 25, "01:00:00:00", _master_v1_events())
        for s in SHOTS:
            assert f"* FROM CLIP NAME: {s}_light_v001" in edl

    def test_tape_is_shot_code(self):
        edl = build_edl("Master v1", 25, "01:00:00:00", _master_v1_events())
        events = [ln for ln in edl.splitlines() if ln[:3].isdigit()]
        for ev, shot in zip(events, SHOTS):
            assert ev.split()[1] == shot

    def test_duration_authoritative_not_cut_item_out(self):
        # stored cut_item_out may be inclusive (real Master v1 data: 1100
        # with duration 100) — the EDL must use duration, giving out 1101.
        edl = build_edl("X", 25, "01:00:00:00", [
            {"tape": "S", "clip_name": None, "src_in_frame": 1001,
             "duration": 100, "rec_in_frame": 0}])
        assert "00:00:44:01" in edl  # 1101 @ 25fps, not 1100


class TestBuildOpenclip:
    def _two_versions(self):
        return [
            {"uid": "v001", "path": "/renders/light/v001/S_light_v001.[1001-1100].exr"},
            {"uid": "v002", "path": "/renders/light/v002/S_light_v002.[1001-1100].exr"},
        ]

    def test_valid_xml_with_versions_block(self):
        xml = build_openclip("S_light", self._two_versions(), fps=25)
        root = ET.fromstring(xml)
        assert root.tag == "clip"
        versions = root.find("versions")
        assert versions.get("nbVersions") == "2"
        assert versions.get("currentVersion") == "v002"
        assert [v.get("uid") for v in versions] == ["v001", "v002"]

    def test_feeds_carry_pattern_paths_and_fps(self):
        xml = build_openclip("S_light", self._two_versions(), fps=25)
        root = ET.fromstring(xml)
        feeds = root.find("tracks/track/feeds")
        assert feeds.get("currentVersion") == "v002"
        paths = [f.find("spans/span/path") for f in feeds]
        assert all(p.get("encoding") == "pattern" for p in paths)
        assert "[1001-1100]" in paths[0].text
        rates = [f.find("sampleRate").text for f in feeds]
        assert rates == ["25", "25"]

    def test_current_override_and_validation(self):
        xml = build_openclip("S", self._two_versions(), current="v001")
        assert 'currentVersion="v001"' in xml
        with pytest.raises(ValueError):
            build_openclip("S", self._two_versions(), current="v999")
        with pytest.raises(ValueError):
            build_openclip("S", [])
