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


def splice_openclips(
    per_version: list[tuple[str, str]],
    current: str | None = None,
) -> str:
    """Merge single-version canonical Open Clip documents into one clip.

    Args:
        per_version: ordered (ascending) list of ``(uid, xml_text)`` where
            ``uid`` is the publish version identifier (e.g. ``v002``) and
            ``xml_text`` is the ``dl_get_media_info`` output describing that
            version's media directory.
        current: uid of the current version; defaults to the LAST entry
            (highest version) when omitted.

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
            for feed in d_feeds.findall("feed"):
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
