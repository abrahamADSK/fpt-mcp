"""Tests for the Chat 91 conform-support pure functions:
editorial.frames_to_timecode / build_edl and openclip.build_openclip.

The EDL fixtures mirror the REAL "Master v1" Cut (id 897): 6 CutItems,
25 fps, base timecode 01:00:00:00, every shot full-range 1001+100.
"""
import xml.etree.ElementTree as ET

import pytest

from fpt_mcp.editorial import build_edl, frames_to_timecode, timecode_to_frames
from fpt_mcp.openclip import splice_openclips

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


class TestSpliceOpenclips:
    """splice_openclips merges single-version canonical documents (the
    dl_get_media_info output tags everything ``v0``) into one versioned clip.

    The fixtures are trimmed canonical documents: same element structure
    Flame 2027 accepts (schema v8), minus the bulky handler blocks that the
    splice never touches.
    """

    def _doc(self, path, tracks=("BEAUTY:MasterBeauty", "Z:Z")):
        track_xml = "".join(
            f'<track uid="{uid}"><trackType>video</trackType>'
            f'<feeds currentVersion="v0"><feed vuid="v0"><spans><span>'
            f'<path encoding="pattern">{path}</path>'
            f"</span></spans></feed></feeds></track>"
            for uid in tracks
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<clip type="clip" version="8"><name type="string">S_light</name>'
            f"<tracks>{track_xml}</tracks>"
            '<versions currentVersion="v0"><version uid="v0"/></versions>'
            "</clip>"
        )

    def _two(self):
        return [
            ("v001", self._doc("/renders/light/v001/S_light_v001.[1001-1100].exr")),
            ("v002", self._doc("/renders/light/v002/S_light_v002.[1001-1100].exr")),
        ]

    # ---- Chat 99: the timecode anchor ------------------------------------
    # In-vivo, a comp version with the SAME frame numbering (1001-1100), the
    # same duration and correct paths still flipped to 'no media' on the
    # conformed timeline: the segment lines versions up by TIMECODE, and the
    # two writers disagree. Maya EXRs carry no timeCode attribute so
    # dl_get_media_info falls back to the frame number (nbTicks 1001);
    # Flame's Write File embeds a real one and stamped 00:00:00:00
    # (nbTicks 0).

    def _tc_doc(self, path, ticks, rate="", drop="NDF"):
        rate_el = f"<rate>{rate}</rate>" if rate else "<rate />"
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<clip type="clip" version="8"><name type="string">S</name>'
            '<tracks><track uid="BEAUTY:MasterBeauty">'
            '<feeds currentVersion="v0"><feed vuid="v0">'
            f"<startTimecode>{rate_el}<nbTicks>{ticks}</nbTicks>"
            f"<dropMode>{drop}</dropMode></startTimecode>"
            "<startFrame>1001</startFrame>"
            f'<spans><span><duration>100</duration>'
            f'<path encoding="pattern">{path}</path></span></spans>'
            "</feed></feeds></track></tracks>"
            '<versions currentVersion="v0"><version uid="v0"/></versions>'
            "</clip>"
        )

    def _light_and_comp(self):
        return [
            ("LIGHT_v003", self._tc_doc("/LGT/v003/S_LGT_v003.[1001-1100].exr", 1001)),
            ("COMP_v001", self._tc_doc(
                "/finishing/comp/S_CMP_v001/S_CMP_v001.[1001-1100].exr", 0, rate="25")),
        ]

    def _ticks(self, root):
        return [
            (f.get("vuid"), (f.find("startTimecode/nbTicks").text or "").strip())
            for f in root.iter("feed")
        ]

    def test_comp_feed_inherits_the_source_timecode_anchor(self):
        realigned = []
        root = ET.fromstring(splice_openclips(self._light_and_comp(), realigned=realigned))
        assert self._ticks(root) == [("LIGHT_v003", "1001"), ("COMP_v001", "1001")]
        assert realigned == ["BEAUTY:MasterBeauty COMP_v001: 0 -> 1001"]

    def test_start_frame_is_never_normalised(self):
        """It must keep matching the real filenames in <path>; numbering
        parity is enforced at RENDER time, not by rewriting the clip."""
        docs = self._light_and_comp()
        docs[1] = ("COMP_v001", docs[1][1]
                   .replace("<startFrame>1001</startFrame>", "<startFrame>1</startFrame>")
                   .replace("[1001-1100]", "[0001-0100]"))
        root = ET.fromstring(splice_openclips(docs))
        frames = [(f.get("vuid"), f.findtext("startFrame")) for f in root.iter("feed")]
        assert frames == [("LIGHT_v003", "1001"), ("COMP_v001", "1")]

    def test_matching_anchor_is_left_alone_and_not_reported(self):
        realigned = []
        docs = [("LIGHT_v003", self._tc_doc("/a/S.[1001-1100].exr", 1001)),
                ("COMP_v001", self._tc_doc("/b/S.[1001-1100].exr", 1001))]
        root = ET.fromstring(splice_openclips(docs, realigned=realigned))
        assert realigned == []
        assert self._ticks(root) == [("LIGHT_v003", "1001"), ("COMP_v001", "1001")]

    def test_dropmode_follows_the_anchor(self):
        docs = [("LIGHT_v003", self._tc_doc("/a/S.[1001-1100].exr", 1001, drop="NDF")),
                ("COMP_v001", self._tc_doc("/b/S.[1001-1100].exr", 0, rate="25", drop="DF"))]
        root = ET.fromstring(splice_openclips(docs))
        drops = [f.findtext("startTimecode/dropMode") for f in root.iter("feed")]
        assert drops == ["NDF", "NDF"]

    def test_feeds_without_a_timecode_block_are_untouched(self):
        """The legacy fixtures carry no <startTimecode> — splicing them must
        not crash or invent one."""
        realigned = []
        root = ET.fromstring(splice_openclips(self._two(), realigned=realigned))
        assert realigned == []
        assert root.find(".//startTimecode") is None

    def test_realigned_is_optional(self):
        splice_openclips(self._light_and_comp())  # no list passed — must not raise

    def test_single_version_retagged_from_v0(self):
        xml = splice_openclips([self._two()[0]])
        assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        root = ET.fromstring(xml)
        for feeds in root.iter("feeds"):
            assert feeds.get("currentVersion") == "v001"
            assert [f.get("vuid") for f in feeds.findall("feed")] == ["v001"]
        versions = root.find("versions")
        assert versions.get("currentVersion") == "v001"
        assert [v.get("uid") for v in versions] == ["v001"]

    def test_two_versions_feeds_merged_per_track(self):
        root = ET.fromstring(splice_openclips(self._two()))
        tracks = root.find("tracks").findall("track")
        assert len(tracks) == 2
        for track in tracks:
            feeds = track.find("feeds")
            assert feeds.get("currentVersion") == "v002"
            assert [f.get("vuid") for f in feeds.findall("feed")] == ["v001", "v002"]
            paths = [f.find("spans/span/path") for f in feeds.findall("feed")]
            assert all(p.get("encoding") == "pattern" for p in paths)
            assert "v001" in paths[0].text and "v002" in paths[1].text
        versions = root.find("versions")
        assert versions.get("currentVersion") == "v002"
        assert [v.get("uid") for v in versions] == ["v001", "v002"]

    def test_track_missing_from_master_is_ignored(self):
        v1 = ("v001", self._doc("/r/v001/a.[1-2].exr", tracks=("BEAUTY:MasterBeauty",)))
        v2 = ("v002", self._doc("/r/v002/a.[1-2].exr", tracks=("BEAUTY:MasterBeauty", "N:N")))
        root = ET.fromstring(splice_openclips([v1, v2]))
        tracks = root.find("tracks").findall("track")
        assert [t.get("uid") for t in tracks] == ["BEAUTY:MasterBeauty"]
        feeds = tracks[0].find("feeds")
        assert [f.get("vuid") for f in feeds.findall("feed")] == ["v001", "v002"]

    def test_current_override_and_validation(self):
        xml = splice_openclips(self._two(), current="v001")
        assert 'currentVersion="v001"' in xml
        with pytest.raises(ValueError):
            splice_openclips(self._two(), current="v999")
        with pytest.raises(ValueError):
            splice_openclips([])
        with pytest.raises(ValueError):
            splice_openclips([self._two()[0], self._two()[0]])


class TestKeepSourceCurrent:
    """Which version is current is NOT cosmetic (Chat 99, measured in-vivo).

    Flame read the very same .clip as ``start_frame=1001`` when the light
    version was current and as ``start_frame=0`` (spanning 1101 frames) when
    the comp version was. With the comp current, every 'Update Sources'
    replace anchored the conformed segment at 00:00:00:00 and lost its cut —
    and no mode of update_sources restores it afterwards, so prevention is
    the only cure. Aligning the feeds' timecode, rate, sampleRate and
    TimecodeSource did NOT change the behaviour; keeping the SOURCE current
    does, and it writes nothing to the timeline.
    """

    def _docs(self):
        return [
            ("v001", '<?xml version="1.0"?><clip type="clip" version="8"><tracks>'
                     '<track uid="T"><feeds currentVersion="v0"><feed vuid="v0">'
                     '<spans><span><path>/a/L.[1001-1100].exr</path></span></spans>'
                     '</feed></feeds></track></tracks>'
                     '<versions currentVersion="v0"><version uid="v0"/></versions></clip>'),
            ("v002", '<?xml version="1.0"?><clip type="clip" version="8"><tracks>'
                     '<track uid="T"><feeds currentVersion="v0"><feed vuid="v0">'
                     '<spans><span><path>/b/C.[1001-1100].exr</path></span></spans>'
                     '</feed></feeds></track></tracks>'
                     '<versions currentVersion="v0"><version uid="v0"/></versions></clip>'),
        ]

    def test_default_keeps_the_newest_current(self):
        root = ET.fromstring(splice_openclips(self._docs()))
        assert root.find("versions").get("currentVersion") == "v002"

    def test_explicit_current_wins_everywhere(self):
        """Both the per-track feeds and the clip-level versions block must
        name it — Flame reads the start frame from the current version."""
        root = ET.fromstring(splice_openclips(self._docs(), current="v001"))
        assert root.find("versions").get("currentVersion") == "v001"
        for feeds in root.iter("feeds"):
            assert feeds.get("currentVersion") == "v001"

    def test_the_field_exists_and_defaults_off(self):
        from fpt_mcp.models import OpenclipCreateInput
        p = OpenclipCreateInput(shot_id=1, output_path="/x/S.clip")
        assert p.keep_source_current is False

    def test_impl_marks_the_first_round_current_when_asked(self):
        import inspect
        from fpt_mcp import shotgrid
        src = inspect.getsource(shotgrid.openclip_create_impl)
        assert "per_version[0][0] if params.keep_source_current else None" in src
        assert "current=current_uid" in src


class TestAnchorMetadataAlignment:
    """The anchor DESCRIPTION is copied from the source feed too — always,
    even when the tick counts already agree (they did, in-vivo, and the clip
    still resolved to start_frame 0)."""

    def _pair(self, comp_extra=""):
        def doc(path, src, rate):
            return ('<?xml version="1.0"?><clip type="clip" version="8"><tracks>'
                    '<track uid="T"><feeds currentVersion="v0"><feed vuid="v0">'
                    f'<startTimecode><rate>{rate}</rate><nbTicks>1001</nbTicks>'
                    '<dropMode>NDF</dropMode></startTimecode>'
                    f'{comp_extra if src == "Header" else ""}'
                    f'<userData><TimecodeSource>{src}</TimecodeSource>'
                    '<RateSource>None / Project</RateSource></userData>'
                    f'<spans><span><path>{path}</path></span></spans>'
                    '</feed></feeds></track></tracks>'
                    '<versions currentVersion="v0"><version uid="v0"/></versions></clip>')
        return [("LIGHT_v003", doc("/a/L.[1001-1100].exr", "Filename", "")),
                ("CMP_v001", doc("/b/C.[1001-1100].exr", "Header", "25"))]

    def test_timecode_source_and_rate_follow_the_source_feed(self):
        root = ET.fromstring(splice_openclips(self._pair()))
        feeds = list(root.iter("feed"))
        rates = [f.find("startTimecode/rate").text or "" for f in feeds]
        srcs = [f.find("userData/TimecodeSource").text for f in feeds]
        assert rates[0] == rates[1] == ""
        assert srcs == ["Filename", "Filename"]

    def test_sample_rate_is_dropped_when_the_source_has_none(self):
        root = ET.fromstring(splice_openclips(self._pair("<sampleRate>25</sampleRate>")))
        assert [f.find("sampleRate") for f in root.iter("feed")] == [None, None]

    def test_alignment_runs_even_when_ticks_already_match(self):
        """The early return on equal nbTicks must not skip it."""
        realigned = []
        root = ET.fromstring(splice_openclips(self._pair(), realigned=realigned))
        assert realigned == []          # nothing to report: ticks agreed
        assert root.find(".//feed[@vuid='CMP_v001']/userData/TimecodeSource").text == "Filename"
