"""openclip.py — Flame Open Clip (.clip) XML generation for published renders.

PURE functions only: no ShotGrid I/O, no filesystem writes. The I/O layer
lives in ``shotgrid.py::openclip_create_impl``.

Why this exists (Chat 91 conform workflow)
==========================================
A Flame timeline conformed against per-shot ``.clip`` files gains version
awareness: each publish version of a shot's render appears as a Flame
"Source Version" the operator can flip to (timeline right-click → Source
Versions → Update Source). The ``.clip`` format is Flame's official,
documented XML API (Autodesk Flame-API book → Open Clip Reference).

Two documented mechanisms exist; this module implements the STATIC one —
one ``<feed>`` per version with bracket-pattern paths — because the official
docs provide a complete verbatim example of it ("One Track Two Versions"),
whereas a full ScanPattern/handler document is undocumented (fragment only).
A new publish version therefore requires REGENERATING the .clip (the
generation is deterministic, so regeneration is idempotent and cheap).

Format notes (all from the official Open Clip Reference):
- version = feeds sharing a ``vuid`` across tracks; declared per-track
  (``<feeds currentVersion=...>``) AND clip-level (``<versions>`` block).
- ``<path encoding="pattern">`` interprets ``[1001-1100]`` frame brackets;
  padding is expressed by how the digits are written.
- Relative paths resolve against the .clip location; absolute paths as-is.
- Do NOT place the .clip next to the media files (conform ambiguity).
"""

from __future__ import annotations

from xml.sax.saxutils import escape


def build_openclip(
    clip_name: str,
    versions: list[dict],
    fps: int = 25,
    current: str | None = None,
) -> str:
    """Build a versioned Open Clip XML document (schema per official examples).

    Args:
        clip_name: display name of the clip (informational; Flame derives the
            clip identity from the .clip filename).
        versions: ordered list (ascending) of dicts with keys:
            ``uid``        — version identifier (e.g. ``v001``),
            ``path``       — absolute path to the EXR sequence with the frame
                             field replaced by a ``[start-end]`` bracket
                             (padding = digits as written),
        fps: integer frame rate written as the feed ``sampleRate``.
        current: uid of the current version; defaults to the LAST entry
            (highest version) when omitted.

    Returns:
        The .clip XML document as a string.
    """
    if not versions:
        raise ValueError("build_openclip: versions list is empty")
    cur = current or versions[-1]["uid"]
    uids = [v["uid"] for v in versions]
    if cur not in uids:
        raise ValueError(f"build_openclip: current={cur!r} not in {uids}")

    feeds = []
    for i, v in enumerate(versions):
        feeds.append(
            '                <feed vuid="{vuid}" uid="f{i}">\n'
            "                    <sampleRate>{fps}</sampleRate>\n"
            "                    <spans>\n"
            "                        <span>\n"
            '                            <path encoding="pattern">{path}</path>\n'
            "                        </span>\n"
            "                    </spans>\n"
            "                </feed>".format(
                vuid=escape(v["uid"]), i=i, fps=fps, path=escape(v["path"])
            )
        )
    version_rows = "\n".join(
        f'        <version uid="{escape(u)}"/>' for u in uids
    )
    return (
        '<?xml version="1.0"?>\n'
        '<clip type="clip" version="4">\n'
        f"    <name>{escape(clip_name)}</name>\n"
        "    <tracks>\n"
        '        <track uid="t0">\n'
        "            <trackType>video</trackType>\n"
        f'            <feeds currentVersion="{escape(cur)}">\n'
        + "\n".join(feeds) + "\n"
        "            </feeds>\n"
        "        </track>\n"
        "    </tracks>\n"
        f'    <versions nbVersions="{len(uids)}" currentVersion="{escape(cur)}">\n'
        + version_rows + "\n"
        "    </versions>\n"
        "</clip>\n"
    )
