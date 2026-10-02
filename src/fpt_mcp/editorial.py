"""editorial.py — deterministic Cut / CutItem timecode auto-calc.

PURE functions only: NO ShotGrid I/O, no pydantic, no ``fpt_mcp.server``
imports. The thin creation layer that turns these field dicts into real
ShotGrid entities lives in ``shotgrid.py::_do_sg_editorial`` (the
``fpt_bulk(action="editorial")`` handler), which uses the existing
``sg_create`` / ``sg_batch`` transaction path.

Why this exists
===============
Today Cut/CutItem are not modelled in code, so the LLM computes the cumulative
timeline / source-range arithmetic by hand in the console. That hand math is
error-prone (off-by-one on the inclusive/exclusive boundary, broken
cumulation across shots). This module moves the math into a single
deterministic, unit-tested function.

Frame-range convention (READ THIS BEFORE CHANGING THE MATH)
===========================================================
The convention is Autodesk's, read from its own Cut importer
(``tk-multi-importcut``, ``edl_cut.py`` / ``cut_diff.py``, verified Chat 109)
so that Create, RV and the importer read our Cuts frame-exact:

1. ``edit_in`` / ``edit_out`` — the RECORD (timeline) position of each item.
   * 1-based: the first item starts at ``edit_in == 1``.
   * INCLUSIVE end: ``edit_out == edit_in + duration - 1``.
   * Contiguous & cumulative: item *k+1* starts at ``edit_out(k) + 1``, so the
     timeline has no gaps and no overlaps.

2. ``cut_item_in`` / ``cut_item_out`` — the SOURCE (media) range of the cut.
   * Anchored at ``source_start_frame`` (default 1001) — it does NOT cumulate,
     because every shot has its own source media.
   * INCLUSIVE end: ``cut_item_out == cut_item_in + duration - 1``, and
     ``cut_item_duration == cut_item_out - cut_item_in + 1 == duration``.
   * Handles are NOT part of the CutItem. As in Autodesk's importer they live
     on the Shot (``sg_head_in`` / ``sg_tail_out``) — see
     :func:`compute_shot_handle_updates`.

This matches the Shot convention (``sg_cut_in`` / ``sg_cut_out`` inclusive).
Cuts written before Chat 109 used a 0-based, exclusive ``edit_*``;
``cut_to_edl`` reads record positions relative to the first item's
``edit_in``, so it handles both.
"""

from __future__ import annotations

from typing import Any

#: Default first source frame for every CutItem's ``cut_item_in`` (the VFX
#: industry's conventional 1001 plate start). Mirrors the value documented in
#: SG_API.md's create-Shot example.
DEFAULT_SOURCE_START_FRAME = 1001


def compute_editorial_cut(
    *,
    entity: dict[str, Any],
    code: str,
    fps: float,
    shots: list[dict[str, Any]],
    source_start_frame: int = DEFAULT_SOURCE_START_FRAME,
    revision_number: int | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Compute the field dicts for one Cut and its ordered CutItems.

    This is a PURE function: it performs the deterministic editorial timecode
    math and returns plain dicts — it does NOT touch ShotGrid. The caller
    (``_do_sg_editorial``) creates the Cut, then injects the resulting Cut link
    into each CutItem dict before the batch create.

    See the module docstring for the full frame-range convention. In short:
    ``edit_*`` is the 1-based, inclusive, contiguous timeline position;
    ``cut_item_*`` is the ``source_start_frame``-anchored, inclusive source
    range of the cut itself (handles go on the Shot, not here).

    Args:
        entity: Entity link the Cut belongs to, e.g.
            ``{"type": "Sequence", "id": 42}``. Passed through verbatim.
        code: Cut ``code`` / name.
        fps: Frames per second. Coerced to ``float`` so ShotGrid's Float-typed
            ``Cut.fps`` field never receives a bare ``int``.
        shots: Ordered list (cut order = list order). Each entry is a mapping
            with ``"shot"`` (a ``{"type": "Shot", "id": N}`` link) and
            ``"duration"`` (cut duration in frames, ``int``).
        source_start_frame: First source frame for every shot's
            ``cut_item_in`` (default 1001). Does not cumulate across shots.
        revision_number: Optional Cut ``revision_number``; omitted from the
            Cut dict when ``None``.

    Returns:
        A 2-tuple ``(cut_fields, cut_item_fields)`` where:
          * ``cut_fields`` has ``code``, ``entity``, ``fps`` (float),
            ``sg_cut_duration`` (sum of all shot durations) and, when given,
            ``revision_number``.
          * ``cut_item_fields`` is a list (one dict per shot, in order) of
            ``shot``, ``cut_order`` (1-based), ``edit_in``, ``edit_out``,
            ``cut_item_in``, ``cut_item_out``, ``cut_item_duration``. The
            ``cut`` link is intentionally absent — it is unknown until the Cut
            is created and is injected by the creation layer.
    """
    cut_fields: dict[str, Any] = {
        "code": code,
        "entity": entity,
        # Float-coerce up front: Cut.fps is a Float field and ShotGrid rejects
        # a bare int with a type Fault (see shotgrid._coerce_float_fields).
        "fps": float(fps),
        "sg_cut_duration": sum(int(entry["duration"]) for entry in shots),
    }
    if revision_number is not None:
        cut_fields["revision_number"] = int(revision_number)

    cut_item_fields: list[dict[str, Any]] = []
    edit_in = 1  # 1-based timeline cursor (Autodesk importer convention).
    for cut_order, entry in enumerate(shots, start=1):
        duration = int(entry["duration"])
        edit_out = edit_in + duration - 1  # inclusive end
        cut_item_fields.append(
            {
                "shot": entry["shot"],
                "cut_order": cut_order,
                # Record (timeline) range — cumulative, 1-based, inclusive.
                "edit_in": edit_in,
                "edit_out": edit_out,
                # Source (media) range of the cut — per-shot, inclusive.
                "cut_item_in": source_start_frame,
                "cut_item_out": source_start_frame + duration - 1,
                "cut_item_duration": duration,
            }
        )
        edit_in = edit_out + 1  # next item starts on the following frame

    return cut_fields, cut_item_fields


def compute_shot_handle_updates(
    *,
    shots: list[dict[str, Any]],
    source_start_frame: int = DEFAULT_SOURCE_START_FRAME,
    handles: int,
) -> list[dict[str, Any]]:
    """Compute the Shot in/out fields that carry the handles.

    PURE function. Mirrors Autodesk's importer
    (``edl_cut.py::_get_shot_in_out_sg_data``, non-smart fields): the cut
    range is inclusive and the handles widen it into the head/tail range.

    A shot used more than once in the cut gets ONE update covering its
    longest use, since every use pulls from the same ``source_start_frame``.

    Args:
        shots: The same ordered entries passed to :func:`compute_editorial_cut`.
        source_start_frame: First source frame of the cut (default 1001).
        handles: Frames added before ``sg_cut_in`` and after ``sg_cut_out``.

    Returns:
        One dict per distinct shot, in first-appearance order, with ``shot``
        (the link) and ``data`` (``sg_head_in``, ``sg_cut_in``, ``sg_cut_out``,
        ``sg_tail_out``, ``sg_cut_duration``, ``sg_working_duration``).
    """
    longest: dict[int, tuple[dict[str, Any], int]] = {}
    for entry in shots:
        link, duration = entry["shot"], int(entry["duration"])
        prev = longest.get(link["id"])
        if prev is None or duration > prev[1]:
            longest[link["id"]] = (link, duration)

    updates = []
    for link, duration in longest.values():
        cut_in = source_start_frame
        cut_out = source_start_frame + duration - 1
        head_in, tail_out = cut_in - handles, cut_out + handles
        updates.append({
            "shot": link,
            "data": {
                "sg_head_in": head_in,
                "sg_cut_in": cut_in,
                "sg_cut_out": cut_out,
                "sg_tail_out": tail_out,
                "sg_cut_duration": cut_out - cut_in + 1,
                "sg_working_duration": tail_out - head_in + 1,
            },
        })
    return updates


# ---------------------------------------------------------------------------
# EDL generation (CMX 3600) — Chat 91 conform workflow
# ---------------------------------------------------------------------------

def frames_to_timecode(frame: int, fps: int) -> str:
    """Convert an absolute frame count to a non-drop SMPTE timecode string.

    Args:
        frame: absolute frame number (0-based within the timecode space).
        fps:   integer frames per second (25 for PAL — this pipeline's rate).

    Returns:
        ``HH:MM:SS:FF`` non-drop timecode.
    """
    ff = frame % fps
    total_seconds = frame // fps
    ss = total_seconds % 60
    mm = (total_seconds // 60) % 60
    hh = total_seconds // 3600
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def timecode_to_frames(tc: str, fps: int) -> int:
    """Inverse of :func:`frames_to_timecode` for ``HH:MM:SS:FF`` strings."""
    hh, mm, ss, ff = (int(p) for p in tc.split(":"))
    return ((hh * 3600 + mm * 60 + ss) * fps) + ff


def build_edl(
    title: str,
    fps: int,
    record_base_tc: str,
    events: list[dict],
) -> str:
    """Build a CMX 3600 EDL from Cut/CutItem-derived event dicts.

    PURE function: no ShotGrid I/O. The SG data conventions handled here
    (see the module docstring): ``rec_in_frame`` is each item's record offset
    from the cut's first frame (0 for the first item) — the caller derives it
    as ``edit_in - first edit_in`` so 1-based (Autodesk) and legacy 0-based
    Cuts both work; the SOURCE range is anchored at ``src_in_frame`` and its
    length is ``duration`` (``cut_item_duration`` is authoritative — stored
    ``cut_item_out`` may be inclusive OR exclusive depending on who created
    the Cut, so it is deliberately not used here). EDL out-points are
    exclusive per the CMX convention.

    Args:
        title:          EDL title line (the Cut code).
        fps:            integer frame rate (25 for this project).
        record_base_tc: timeline start timecode (``Cut.timecode_start_text``).
        events: ordered list (by cut_order) of dicts with keys:
            ``tape``         — tape/reel name (the Shot code),
            ``clip_name``    — source clip display name (published file base),
            ``src_in_frame`` — first source frame (``cut_item_in``),
            ``duration``     — event length in frames (``cut_item_duration``),
            ``rec_in_frame`` — record offset from the cut's first frame.

    Returns:
        The EDL file content as a string (trailing newline included).
    """
    base = timecode_to_frames(record_base_tc, fps)
    lines = [f"TITLE: {title}", "FCM: NON-DROP FRAME", ""]
    for idx, ev in enumerate(events, start=1):
        src_in = int(ev["src_in_frame"])
        dur = int(ev["duration"])
        rec_in = base + int(ev["rec_in_frame"])
        lines.append(
            "{:03d}  {:<8s} V     C        {} {} {} {}".format(
                idx,
                str(ev["tape"])[:32],
                frames_to_timecode(src_in, fps),
                frames_to_timecode(src_in + dur, fps),
                frames_to_timecode(rec_in, fps),
                frames_to_timecode(rec_in + dur, fps),
            )
        )
        clip = ev.get("clip_name")
        if clip:
            lines.append(f"* FROM CLIP NAME: {clip}")
        lines.append("")
    return "\n".join(lines) + "\n"
