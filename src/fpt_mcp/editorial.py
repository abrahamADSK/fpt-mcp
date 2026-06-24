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
There are two independent axes in editorial data, and they use DIFFERENT
conventions on purpose:

1. ``edit_in`` / ``edit_out`` — the RECORD (timeline) position of each item.
   * 0-based: the first item starts at ``edit_in == 0``.
   * Exclusive end (half-open interval ``[edit_in, edit_out)``):
     ``edit_out == edit_in + duration``.
   * Contiguous & cumulative: ``edit_out`` of item *k* equals ``edit_in`` of
     item *k+1*, so the timeline has no gaps and no overlaps. This is the
     standard EDL / OpenTimelineIO record-time convention and is REQUIRED for
     the cumulation to be well defined (``edit_in(k) == sum of durations
     before k``).

2. ``cut_item_in`` / ``cut_item_out`` — the SOURCE (media) range pulled from
   each shot's plate.
   * Anchored at ``source_start_frame`` (default 1001) — it does NOT cumulate,
     because every shot has its own source media that restarts at
     ``source_start_frame``.
   * Exclusive end, matching the edit axis: with no handles,
     ``cut_item_in == source_start_frame`` and
     ``cut_item_out == source_start_frame + duration``.
   * Handles (``handles``, default 0) widen the source range symmetrically by
     ``handles`` frames on EACH side (head + tail pull), so
     ``cut_item_in == source_start_frame - handles`` and
     ``cut_item_out == source_start_frame + duration + handles``.
     ``cut_item_duration`` always tracks the EDIT length (``duration``) and is
     NOT widened by handles, so ``cut_item_out - cut_item_in`` exceeds
     ``cut_item_duration`` exactly by ``2 * handles`` when handles are pulled.

Note on the contrasting Shot convention:
``SG_API.md`` (the create-Shot example, ``sg_cut_in: 1001 / sg_cut_out: 1100 /
sg_cut_duration: 100``) documents the SHOT entity's ``sg_cut_in`` /
``sg_cut_out`` fields as INCLUSIVE (``out == in + duration - 1``). That is a
different entity (Shot, not CutItem) and a different field family; this module
deliberately uses the exclusive/half-open convention for the CutItem record
and source ranges because (a) the editorial spec defines the math as
``edit_out = edit_in + duration`` and ``source_start_frame + duration``, and
(b) exclusive-out is what makes the timeline cumulation contiguous. If a live
ShotGrid CutItem schema audit ever shows the site expects inclusive CutItem
ranges, flip ``cut_item_out`` / ``edit_out`` to ``... - 1`` here and update the
unit tests in lockstep.
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
    handles: int = 0,
    revision_number: int | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Compute the field dicts for one Cut and its ordered CutItems.

    This is a PURE function: it performs the deterministic editorial timecode
    math and returns plain dicts — it does NOT touch ShotGrid. The caller
    (``_do_sg_editorial``) creates the Cut, then injects the resulting Cut link
    into each CutItem dict before the batch create.

    See the module docstring for the full frame-range convention. In short:
    ``edit_*`` is the 0-based, exclusive-out, contiguous timeline position;
    ``cut_item_*`` is the ``source_start_frame``-anchored, exclusive-out source
    range widened by ``handles`` on each side.

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
        handles: Handle frames added to EACH side of every shot's source range
            (default 0).
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
    edit_in = 0  # 0-based timeline cursor; advances by each shot's duration.
    for cut_order, entry in enumerate(shots, start=1):
        duration = int(entry["duration"])
        edit_out = edit_in + duration  # exclusive end → contiguous next item
        cut_item_fields.append(
            {
                "shot": entry["shot"],
                "cut_order": cut_order,
                # Record (timeline) range — cumulative, 0-based, exclusive-out.
                "edit_in": edit_in,
                "edit_out": edit_out,
                # Source (media) range — per-shot, anchored at
                # source_start_frame, widened symmetrically by handles.
                "cut_item_in": source_start_frame - handles,
                "cut_item_out": source_start_frame + duration + handles,
                # The EDIT length (excludes handles), by spec.
                "cut_item_duration": duration,
            }
        )
        edit_in = edit_out  # next item starts where this one ended

    return cut_fields, cut_item_fields
