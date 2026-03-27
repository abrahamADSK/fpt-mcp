"""Tools for creating and querying Shots in FPT."""

from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict

from fpt_mcp.client import sg_create, sg_find, sg_find_one, get_project_filter, PROJECT_ID


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class CreateShotInput(BaseModel):
    """Input for fpt_create_shot."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: str = Field(
        ...,
        description="Shot code, e.g. 'SHOT010'. Must be unique within the sequence.",
        min_length=1,
        max_length=64,
    )
    sequence_id: int = Field(
        ...,
        description="ShotGrid entity ID of the parent Sequence.",
        ge=1,
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional shot description or notes.",
    )
    status: str = Field(
        default="wtg",
        description="Status code: 'wtg' (waiting), 'ip' (in progress), 'fin' (final).",
    )
    cut_in: Optional[int] = Field(
        default=None,
        description="Cut-in frame number.",
        ge=0,
    )
    cut_out: Optional[int] = Field(
        default=None,
        description="Cut-out frame number.",
        ge=0,
    )
    frame_range: Optional[str] = Field(
        default=None,
        description="Frame range string, e.g. '1001-1120'.",
    )


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------

async def create_shot_impl(params: CreateShotInput) -> str:
    """Create a new Shot entity linked to a Sequence in FPT.

    Returns the created Shot's id, code, sequence link, and frame info.
    Validates that the parent Sequence exists.
    """
    # Validate sequence exists
    seq = await sg_find_one("Sequence", [["id", "is", params.sequence_id]], ["id", "code"])
    if not seq:
        return json.dumps({
            "error": f"Sequence with id={params.sequence_id} not found. "
            "Create the sequence first with fpt_create_sequence.",
        })

    # Check for duplicate shot code within sequence
    dup_filters = [
        ["sg_sequence", "is", {"type": "Sequence", "id": params.sequence_id}],
        ["code", "is", params.code],
    ]
    existing = await sg_find("Shot", dup_filters, ["id", "code"], limit=1)
    if existing:
        return json.dumps({
            "error": f"Shot '{params.code}' already exists in Sequence '{seq['code']}' "
            f"(id={existing[0]['id']}).",
            "existing": existing[0],
        })

    data: dict = {
        "code": params.code,
        "sg_sequence": {"type": "Sequence", "id": params.sequence_id},
        "sg_status_list": params.status,
    }
    if params.description:
        data["description"] = params.description
    if PROJECT_ID:
        data["project"] = get_project_filter()
    if params.cut_in is not None:
        data["sg_cut_in"] = params.cut_in
    if params.cut_out is not None:
        data["sg_cut_out"] = params.cut_out
    if params.frame_range:
        data["sg_frame_range"] = params.frame_range

    result = await sg_create("Shot", data)

    return json.dumps({
        "id": result["id"],
        "code": params.code,
        "type": "Shot",
        "sequence": {"id": params.sequence_id, "code": seq["code"]},
        "status": params.status,
        "cut_in": params.cut_in,
        "cut_out": params.cut_out,
        "frame_range": params.frame_range,
        "message": f"Shot '{params.code}' created in Sequence '{seq['code']}'.",
    }, indent=2)
