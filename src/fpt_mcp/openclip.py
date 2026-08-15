"""openclip.py — Flame Open Clip (.clip) version splicing for published renders.

PURE functions only: no ShotGrid I/O, no filesystem writes, no subprocess.
The I/O layer lives in ``shotgrid.py::openclip_create_impl``.

Why canonical documents instead of hand-rolled XML (Chat 92 in-vivo)
====================================================================
The first implementation emitted a minimal static XML (schema ``version="4"``,
one bare ``<feed>`` per version) modeled on the Open Clip Reference example.
Flame 2027 REJECTS that document SILENTLY: ``flame.import_clips`` returns an
empty list, the app log shows a clean ``Entering/Exiting importClips`` with no
error, and nothing lands in the reel. The canonical generator that ships with
Flame — ``dl_get_media_info`` (``/opt/Autodesk/mio/<version>/``) — produces a
schema ``version="8"`` document (per-track OpenEXR ``<handler>`` blocks, typed
attributes, one track per EXR channel group) that imports correctly; validated
in-vivo on Flame 2027 (2026-08-05, 6/6 clips).

So the division of labor is now:
- ``dl_get_media_info <media_dir>`` describes ONE version's media (it tags it
  ``vuid="v0"``) — run once per publish version by the I/O layer.
- ``splice_openclips`` (here) merges those single-version documents into one
  versioned Open Clip: feeds are appended per matching track ``uid``, version
  uids are renamed from ``v0`` to the real publish version (``v002``…), and
  ``currentVersion`` points at the chosen current (default: last/highest).

Format notes:
- A "version" = feeds sharing a ``vuid`` across tracks; declared per-track
  (``<feeds currentVersion=...>``) AND clip-level (``<versions>`` block).
- ``<path encoding="pattern">`` interprets ``[1001-1100]`` frame brackets.
- Tracks are matched across versions by their ``uid`` attribute (channel
  identity, e.g. ``BEAUTY:MasterBeauty``). A track present in a later version
  but absent from the first (master) document has no home and is ignored; a
  master track missing from a later version simply carries no feed for that
  vuid.
- Do NOT place the .clip next to the media files (conform ambiguity).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET


def _retag_version(root: ET.Element, uid: str) -> None:
    """Rename the single-version tag (``v0``) of a canonical document to a
    real publish-version uid, in every place the schema declares it."""
    for feeds in root.iter("feeds"):
        if feeds.get("currentVersion") is not None:
            feeds.set("currentVersion", uid)
        for feed in feeds.findall("feed"):
            if feed.get("vuid") is not None:
                feed.set("vuid", uid)
    versions = root.find("versions")
    if versions is not None:
        versions.set("currentVersion", uid)
        for v in versions.findall("version"):
            v.set("uid", uid)


def _align_timecode(master_feed: ET.Element, feed: ET.Element) -> str | None:
    """Give ``feed`` the master (source) feed's start-timecode ANCHOR.

    Why (Chat 99, in-vivo 'no media' on the timeline flip)
    -----------------------------------------------------
    A conformed segment lines its versions up by TIMECODE, not by frame
    number. The two writers disagree about what they stamp:

    - Maya/Arnold EXRs carry NO ``timeCode`` attribute, so
      ``dl_get_media_info`` falls back to the frame number and the LIGHT feed
      reads ``<startTimecode><nbTicks>1001``.
    - Flame's Write File DOES embed a real ``timeCode``, and a comp batch
      whose source timecode was never set stamps ``00:00:00:00`` — the COMP
      feed reads ``<nbTicks>0``.

    Same frame numbering (both ``1001-1100`` after the flame-mcp fix), same
    duration, correct paths — and the flip still showed 'no media', because
    the segment asked for TC 1001-1100 and that feed spanned TC 0-99.

    The pipeline owns the clip, so the pipeline normalises the anchor: every
    spliced version inherits the SOURCE version's ``nbTicks`` (and
    ``dropMode``). This is a metadata correction, not a lie — frame N of a
    comp version IS the comp of the source's frame N; the ``00:00:00:00``
    stamp is what is wrong.

    ``startFrame`` is deliberately NOT normalised: it must keep matching the
    real filenames in ``<path>`` (``[0001-0100]``), or Flame looks for files
    that do not exist. Numbering parity is enforced upstream, at render time.

    Returns a short 'uid: 0 -> 1001' description when it changed something,
    else ``None``.
    """
    m_tc = master_feed.find("startTimecode")
    f_tc = feed.find("startTimecode")
    if m_tc is None or f_tc is None:
        return None
    m_ticks = m_tc.find("nbTicks")
    f_ticks = f_tc.find("nbTicks")
    if m_ticks is None or f_ticks is None:
        return None
    before = (f_ticks.text or "").strip()
    after = (m_ticks.text or "").strip()
    if before == after:
        return None
    f_ticks.text = m_ticks.text
    m_drop, f_drop = m_tc.find("dropMode"), f_tc.find("dropMode")
    if m_drop is not None and f_drop is not None:
        f_drop.text = m_drop.text
    return f"{feed.get('vuid')}: {before or 'none'} -> {after or 'none'}"


def splice_openclips(
    per_version: list[tuple[str, str]],
    current: str | None = None,
    realigned: list[str] | None = None,
) -> str:
    """Merge single-version canonical Open Clip documents into one clip.

    Args:
        per_version: ordered (ascending) list of ``(uid, xml_text)`` where
            ``uid`` is the publish version identifier (e.g. ``v002``) and
            ``xml_text`` is the ``dl_get_media_info`` output describing that
            version's media directory.
        current: uid of the current version; defaults to the LAST entry
            (highest version) when omitted.
        realigned: optional list the function APPENDS to, one entry per feed
            whose start-timecode anchor was pulled onto the source version's
            (see ``_align_timecode``) — so the caller can report it instead
            of silently rewriting metadata.

    Returns:
        The merged .clip XML document as a string (UTF-8 declaration).
    """
    if not per_version:
        raise ValueError("splice_openclips: per_version list is empty")
    uids = [u for u, _ in per_version]
    if len(set(uids)) != len(uids):
        raise ValueError(f"splice_openclips: duplicate uids in {uids}")
    cur = current or uids[-1]
    if cur not in uids:
        raise ValueError(f"splice_openclips: current={cur!r} not in {uids}")

    master_uid, master_xml = per_version[0]
    master = ET.fromstring(master_xml)
    _retag_version(master, master_uid)

    tracks_el = master.find("tracks")
    master_tracks = {
        t.get("uid"): t
        for t in (tracks_el.findall("track") if tracks_el is not None else [])
    }

    for uid, xml_text in per_version[1:]:
        doc = ET.fromstring(xml_text)
        _retag_version(doc, uid)
        doc_tracks = doc.find("tracks")
        for t in (doc_tracks.findall("track") if doc_tracks is not None else []):
            m_track = master_tracks.get(t.get("uid"))
            if m_track is None:
                continue  # channel not in the master document — no home for it
            m_feeds = m_track.find("feeds")
            d_feeds = t.find("feeds")
            if m_feeds is None or d_feeds is None:
                continue
            # The SOURCE version's feed on this track is the anchor every
            # later version must line up with (Chat 99 — see _align_timecode).
            anchor = next(iter(m_feeds.findall("feed")), None)
            for feed in d_feeds.findall("feed"):
                if anchor is not None:
                    note = _align_timecode(anchor, feed)
                    if note is not None and realigned is not None:
                        realigned.append(f"{t.get('uid')} {note}")
                m_feeds.append(feed)
        m_versions = master.find("versions")
        d_versions = doc.find("versions")
        if m_versions is not None and d_versions is not None:
            for v in d_versions.findall("version"):
                m_versions.append(v)

    for feeds in master.iter("feeds"):
        if feeds.get("currentVersion") is not None:
            feeds.set("currentVersion", cur)
    m_versions = master.find("versions")
    if m_versions is not None:
        m_versions.set("currentVersion", cur)

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(master, encoding="unicode")
    )
