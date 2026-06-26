"""Source-media resolver — choose the best generation input for an Asset
from its ShotGrid context (shared by the World Labs and Vision3D pipelines).

Priority: ``video > image > text`` (the Asset description). Video is
**DEFERRED** — the World Labs client does not yet build a ``video_prompt`` —
so the video tier is reserved (``VIDEO_ENABLED = False``) and slots in as top
priority without a refactor; today the effective order is ``image > text``.

Candidates considered:
  * **image** — a linked Version's ``image`` (uploaded still / thumbnail),
                then the Asset's own ``image`` thumbnail as a lower-ranked
                fallback.
  * **text**  — the Asset's ``description`` → a generation ``text_prompt``.
  * **video** — (reserved) a linked Version's ``sg_uploaded_movie``.

The module is **pure and synchronous**: :func:`rank_candidates` and
:func:`decide` take already-fetched ShotGrid dicts and never touch the
network, so they are unit-tested in isolation (mirrors ``software_resolver``
and the project's move of deterministic logic out of the system prompt). The
SG queries and the attachment download live in the tool impl
(``shotgrid.py::sg_resolve_source_impl``).

Two-phase contract — a tool call cannot block for a user pick, mirroring
``sg_download``:
  * **phase 1 (rank)** → :func:`decide` returns one of:
      - ``resolved``        a single top-priority image/video; ready to download
      - ``requires_choice`` several images tie at top; caller surfaces the pick
      - ``text_only``       no image/video, but a description → use as text_prompt
      - ``no_source``       nothing usable
  * **phase 2 (download)** → the tool downloads the chosen candidate's field.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# Video is the highest-quality World Labs input, but the WL client does not
# yet build ``video_prompt`` (deferred — see maya_worldlabs). Flip to True
# when the client gains video support; the ranking then prefers video with no
# other change here.
VIDEO_ENABLED = False

# Priority tiers (higher = preferred). Declared explicitly so the deferred
# video tier already outranks image.
_PRIORITY = {"video": 3, "image": 2, "text": 1}


@dataclass
class SourceCandidate:
    """One candidate generation input for an Asset.

    ``entity_type``/``entity_id``/``field_name`` locate the downloadable
    attachment (all ``None`` for a ``text`` candidate, which carries ``text``
    instead).
    """

    kind: str  # "video" | "image" | "text"
    label: str
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    field_name: Optional[str] = None
    text: Optional[str] = None

    @property
    def priority(self) -> int:
        return _PRIORITY.get(self.kind, 0)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "kind": self.kind,
            "label": self.label,
            "priority": self.priority,
        }
        if self.entity_type is not None:
            d["entity_type"] = self.entity_type
            d["entity_id"] = self.entity_id
            d["field_name"] = self.field_name
        if self.text is not None:
            d["text"] = self.text
        return d


def rank_candidates(
    asset: dict[str, Any],
    versions: list[dict[str, Any]],
    *,
    video_enabled: bool = VIDEO_ENABLED,
) -> list[SourceCandidate]:
    """Build the priority-ordered candidate list for an Asset.

    ``asset`` needs ``id``/``code``/``description``/``image``; each
    ``versions`` dict needs ``id``/``code``/``image``/``sg_uploaded_movie``.
    ``versions`` is expected newest-first (the caller orders by ``created_at``
    desc); that order is preserved within a tier so the newest review media
    ranks first. The sort is stable, so tiers stack by ``_PRIORITY`` while
    intra-tier insertion order is kept.
    """
    candidates: list[SourceCandidate] = []

    # video tier (reserved) — a Version's uploaded movie
    if video_enabled:
        for v in versions:
            if v.get("sg_uploaded_movie"):
                candidates.append(
                    SourceCandidate(
                        kind="video",
                        label=f"Version '{v.get('code') or v.get('id')}' movie",
                        entity_type="Version",
                        entity_id=v.get("id"),
                        field_name="sg_uploaded_movie",
                    )
                )

    # image tier — Version stills/thumbnails first, then the Asset thumbnail
    for v in versions:
        if v.get("image"):
            candidates.append(
                SourceCandidate(
                    kind="image",
                    label=f"Version '{v.get('code') or v.get('id')}' image",
                    entity_type="Version",
                    entity_id=v.get("id"),
                    field_name="image",
                )
            )
    if asset.get("image"):
        candidates.append(
            SourceCandidate(
                kind="image",
                label=f"Asset '{asset.get('code')}' thumbnail",
                entity_type="Asset",
                entity_id=asset.get("id"),
                field_name="image",
            )
        )

    # text tier — the Asset description
    desc = (asset.get("description") or "").strip()
    if desc:
        candidates.append(
            SourceCandidate(
                kind="text",
                label=f"Asset '{asset.get('code')}' description",
                text=desc,
            )
        )

    # stable sort by priority desc (preserves within-tier insertion order)
    candidates.sort(key=lambda c: c.priority, reverse=True)
    return candidates


def decide(candidates: list[SourceCandidate]) -> dict[str, Any]:
    """Resolve the ranked candidates into a status decision (pure).

    Returns a dict with ``status`` in {``resolved``, ``requires_choice``,
    ``text_only``, ``no_source``} plus the relevant payload. The Asset
    description (when present) is always surfaced as ``text_prompt`` so an
    image generation can pass it as the optional companion prompt.
    """
    text_prompt = next((c.text for c in candidates if c.kind == "text"), None)

    if not candidates:
        return {"status": "no_source"}

    top_priority = candidates[0].priority
    top = [c for c in candidates if c.priority == top_priority]

    if top[0].kind == "text":
        return {"status": "text_only", "text_prompt": top[0].text}

    if len(top) == 1:
        return {
            "status": "resolved",
            "candidate": top[0].to_dict(),
            "text_prompt": text_prompt,
        }

    return {
        "status": "requires_choice",
        "candidates": [c.to_dict() for c in top],
        "text_prompt": text_prompt,
    }
