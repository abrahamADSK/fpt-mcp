"""
safety.py
=========
Dangerous pattern detection for ShotGrid API operations.

Scans tool parameters for patterns known to cause data loss, corruption,
or unintended side effects in ShotGrid production databases.

Based on flame-mcp's proven regex + explanation + alternative pattern.
Adapted for the ShotGrid domain where the risk is corrupting production
data visible to the entire team, not just crashing an application.
"""

import re
from typing import Optional

# Each entry: (regex, explanation, safe_alternative)
_DANGEROUS_PATTERNS = [
    # ── Destructive bulk operations ───────────────────────────────────────────
    (
        r'sg_delete.*limit.*0|sg_delete.*all|"retire_all"',
        "Bulk delete without specific entity IDs — this could retire hundreds of entities.",
        "Always specify exact entity IDs to delete. Use sg_find first to identify "
        "targets, review them, then delete individually.",
    ),
    (
        r'sg_find.*filters.*\[\s*\].*limit.*0',
        "Unfiltered search with no limit — returns ALL entities of this type. "
        "This can be extremely slow and consume excessive tokens.",
        "Always add at least one filter (e.g. project, status, date range) "
        "and set a reasonable limit (50-100).",
    ),
    # ── Entity format errors ──────────────────────────────────────────────────
    (
        r'"entity"\s*:\s*\d+|"project"\s*:\s*\d+|"task"\s*:\s*\d+',
        "Entity reference as integer — ShotGrid requires dict format "
        '{"type": "EntityType", "id": N}. An integer will cause API errors.',
        'Use the correct format: {"type": "Asset", "id": 123}',
    ),
    # ── Path traversal in publish ─────────────────────────────────────────────
    (
        r'\.\./|\.\.\\',
        "Path traversal detected in file path — could write files outside "
        "the intended publish directory.",
        "Use absolute paths or paths relative to the project root. "
        "Never include '..' in publish paths.",
    ),
    # ── Schema modification ───────────────────────────────────────────────────
    (
        r'schema_field_create|schema_field_delete|schema_entity_create',
        "Schema modification detected — this permanently alters the ShotGrid "
        "database structure for ALL users.",
        "Schema changes should be done manually via ShotGrid admin UI. "
        "Use sg_schema to read schema, not modify it.",
    ),
    # ── Dangerous field updates ───────────────────────────────────────────────
    (
        r'sg_update.*"project"\s*:\s*null|sg_update.*"project"\s*:\s*None',
        "Setting project to null would unlink this entity from its project — "
        "this is almost always unintended and may hide the entity from all views.",
        "If you need to move an entity to a different project, set it to the "
        'new project dict: {"type": "Project", "id": NEW_ID}',
    ),
    (
        r'sg_update.*"sg_status_list"\s*:\s*"omt"',
        "Setting status to 'omt' (omitted) hides entities from most views. "
        "This is the ShotGrid equivalent of a soft-delete — entities become "
        "invisible unless specifically filtered.",
        "Use 'wtg' (waiting), 'ip' (in progress), 'cmpt' (complete), "
        "or 'hld' (on hold) for normal status changes.",
    ),
    # ── PublishedFile dangers ─────────────────────────────────────────────────
    (
        r'sg_delete.*PublishedFile',
        "Deleting a PublishedFile can break Toolkit loader references. "
        "Other artists may have loaded or referenced this file.",
        "Instead of deleting, set sg_status_list to 'omt' (omit) to "
        "hide it from loaders while preserving the reference chain.",
    ),
    (
        r'sg_update.*PublishedFile.*"path"',
        "Modifying the path of an existing PublishedFile can break references "
        "for anyone who has loaded this file.",
        "Create a new version (PublishedFile) with the correct path instead "
        "of modifying the existing one.",
    ),
    # ── Filter operator hallucinations ────────────────────────────────────────
    (
        r'"is_exactly"|"exact"|"matches"|"regex"|"like"',
        "Invalid filter operator — these do not exist in the ShotGrid API. "
        "Common hallucination by LLMs.",
        "Valid operators: is, is_not, contains, not_contains, starts_with, "
        "ends_with, greater_than, less_than, between, in, not_in, "
        "type_is, type_is_not, name_contains, name_not_contains.",
    ),
    # ── Batch without safety ──────────────────────────────────────────────────
    (
        r'batch.*(?:create|update|delete).*len.*>\s*(?:50|100|500|1000)',
        "Large batch operation detected — ShotGrid batch operations are "
        "transactional (all-or-nothing). A failure in any item rolls back everything.",
        "For large batches, split into groups of 25-50 operations and "
        "process each batch separately to limit rollback scope.",
    ),
    # ── Toolkit template errors ───────────────────────────────────────────────
    (
        r'\{shot_name\}|\{asset_name\}|\{project_name\}',
        "Invalid template token — ShotGrid Toolkit uses {Shot}, {Asset}, "
        "{Step} (PascalCase), not {shot_name} or {asset_name}.",
        "Use the correct tokens: {Shot}, {Asset}, {Sequence}, {Step}, "
        "{sg_asset_type}, {name}, {version}, {maya_extension}.",
    ),
    # ── Status code hallucinations (C.5) ──────────────────────────────────────
    # Catches any sg_status_list value outside the canonical short codes.
    # The negative lookahead lists every legal code; anything else trips.
    # Common LLM hallucinations: "review", "ready", "in_progress", "complete".
    (
        r'"sg_status_list"\s*:\s*"(?!ip|wtg|cmpt|hld|fin|omt|rev|kik|apr|na|rdy)[a-z_]+"',
        "Invalid sg_status_list value — ShotGrid uses short codes, not full words. "
        "Common hallucinations: 'review' (use 'rev'), 'ready' (use 'rdy'), "
        "'in_progress' (use 'ip'), 'complete' (use 'cmpt').",
        "Use one of the canonical short codes: 'wtg' (waiting to start), "
        "'ip' (in progress), 'rev' (pending review), 'rdy' (ready to start), "
        "'cmpt' (complete), 'fin' (final), 'hld' (on hold), 'omt' (omitted), "
        "'apr' (approved), 'kik' (kickback), 'na' (n/a). "
        "Run sg_schema on the entity to confirm legal status codes for "
        "your project — custom pipelines may add or remove codes.",
    ),
    # ── Bare integer IDs in update.entity_id position (C.5) ───────────────────
    # Catches sg_update calls where the value of an entity-link field is a
    # raw int instead of a {"type":..., "id":...} dict. The pattern looks for
    # any of the common entity link field names followed by a bare integer.
    (
        r'"(?:entity|task|user|asset|shot|sequence|version|playlist|step|published_file|parent)"\s*:\s*\d+',
        "Entity-link field assigned a bare integer — ShotGrid requires the "
        "full {\"type\":..., \"id\":N} dict for any field that links to "
        "another entity, even on update payloads.",
        "Wrap the id in a dict: {\"type\": \"Asset\", \"id\": 123}. "
        "Run sg_schema to discover which fields are entity links if you "
        "are unsure.",
    ),
]


def check_dangerous(params_str: str) -> Optional[str]:
    """
    Scan serialized tool parameters for dangerous patterns.

    Args:
        params_str: JSON-serialized string of tool parameters.

    Returns:
        Formatted warning string if patterns found, None if safe.
    """
    hits = []
    for pattern, reason, alternative in _DANGEROUS_PATTERNS:
        if re.search(pattern, params_str, re.IGNORECASE):
            hits.append(f"  • {reason}\n    ✅ Instead: {alternative}")

    if not hits:
        return None

    return (
        "⚠️  Safety check — potentially dangerous pattern(s) detected:\n\n"
        + "\n\n".join(hits)
        + "\n\nReview and revise the parameters before proceeding. "
        "Use search_sg_docs to find the correct approach if unsure."
    )
