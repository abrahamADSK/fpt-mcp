"""Tools for creating and querying Sequences in FPT."""

from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict

from fpt_mcp.client import sg_create, sg_find, get_project_filter, PROJECT_ID


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class CreateSequenceInput(BaseModel):
    """Input for fpt_create_sequence."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: str = Field(
        ...,
        description="Sequence code/name, e.g. 'SEQ010'. Must be unique within the project.",
        min_length=1,
        max_length=64,
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional description for the sequence.",
    )
    status: str = Field(
        default="ip",
        description="Status code: 'ip' (in progress), 'wtg' (waiting), 'fin' (final).",
    )


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------

async def create_sequence_impl(params: CreateSequenceInput) -> str:
    """Create a new Sequence entity in FPT.

    Returns the created Sequence's id, code, and project.
    Fails with a clear message if the sequence code already exists.
    """
    # Check for duplicates
    if PROJECT_ID:
        dup_filters = [
            ["project", "is", get_project_filter()],
            ["code", "is", params.code],
        ]
    else:
        dup_filters = [["code", "is", params.code]]

    existing = await sg_find("Sequence", dup_filters, ["id", "code"], limit=1)
    if existing:
        return json.dumps({
            "error": f"Sequence '{params.code}' already exists (id={existing[0]['id']}). "
            "Use a different code or work with the existing one.",
            "existing": existing[0],
        })

    data: dict = {
        "code": params.code,
        "sg_status_list": params.status,
    }
    if params.description:
        data["description"] = params.description
    if PROJECT_ID:
        data["project"] = get_project_filter()

    result = await sg_create("Sequence", data)

    return json.dumps({
        "id": result["id"],
        "code": params.code,
        "type": "Sequence",
        "status": params.status,
        "message": f"Sequence '{params.code}' created successfully.",
    }, indent=2)
