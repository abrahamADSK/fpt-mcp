# O3 — `next_suggested_actions` (Design Doc)

**Status**: DESIGN ONLY — no implementation yet.
**Author**: Claude session Chat 46 (2026-04-20)
**Scope**: fpt-mcp. Pattern could later propagate to maya-mcp / flame-mcp.

---

## 1. Motivation

Today, every fpt-mcp MCP tool returns a payload that answers exactly the
question asked. Claude then relies on `SYSTEM_PROMPT` heuristics + RAG to
decide what to do next. This works but:

- Discovery of adjacent operations is implicit (buried in the 2300-token
  prompt).
- Non-obvious follow-ups (e.g. `sg_download` after `sg_find`-with-
  `image` field) only happen if Claude remembers to chain them.
- Qwen-class local models particularly benefit from explicit chaining
  hints (less implicit reasoning than Anthropic models).

A small, opt-in `next_suggested_actions` field appended to tool responses
surfaces likely follow-ups cheaply. Claude reads them, optionally
surfaces to the user in a "Next, you could…" block, and picks the
relevant one.

This is the fpt-mcp O3 objective deferred across Chats 38–45 as "feature
not designed yet". This doc closes the design gap so a future session
can implement without re-debating shape.

## 2. Non-goals

- **Not** an LLM-driven planner. No second LLM call per tool response.
  Rules are static Python/YAML.
- **Not** a workflow engine. Suggestions are hints, not commands; Claude
  is free to ignore them.
- **Not** auto-execute. No tool ever invokes another tool because of a
  suggestion — the user stays in the loop.

## 3. Shape of the feature

### 3.1 Response schema

Every @mcp.tool that opts in appends `next_suggested_actions` to its
JSON response:

```python
{
    "ok": True,
    "result": [...],             # normal tool payload
    "next_suggested_actions": [  # optional, may be empty or absent
        {
            "tool": "sg_download",
            "reason": "Fetch the reference image you just located.",
            "params_hint": {"entity_type": "Asset", "field": "image"},
        },
        {
            "tool": "maya_vision3d",
            "reason": "Generate a 3D mesh from this image.",
            "params_hint": {"action": "generate_image"},
        },
    ],
}
```

- `tool` — exact MCP tool name (`mcp__fpt-mcp__sg_download` or the bare
  name; TBD, see §7).
- `reason` — one-liner the user will see, plain English.
- `params_hint` — partial param dict Claude can start from; not
  required to be complete.

### 3.2 Rules registry

**Option A (recommended for v1)** — static Python dict in
`src/fpt_mcp/suggestions.py`:

```python
from typing import Callable, TypedDict

class Suggestion(TypedDict, total=False):
    tool: str
    reason: str
    params_hint: dict

# key = (tool_name, response_shape_predicate_id)
# value = callable(tool_response) -> list[Suggestion]
SUGGESTION_RULES: dict[str, Callable[[dict], list[Suggestion]]] = {
    "sg_find": _suggest_after_sg_find,
    "sg_download": _suggest_after_sg_download,
    "tk_publish": _suggest_after_tk_publish,
    "fpt_bulk": _suggest_after_fpt_bulk,
    # Tools without an entry → no suggestions (same as returning []).
}
```

The helper `_suggest_after_sg_find(response)` inspects the response
body (entity type, fields present, list length) and returns 0–3
suggestions. Example:

```python
def _suggest_after_sg_find(response: dict) -> list[Suggestion]:
    rows = response.get("result") or []
    if not rows:
        return []
    entity_type = (rows[0] or {}).get("type", "")
    has_image = any(row.get("image") for row in rows)

    suggestions = []
    if entity_type == "Asset" and has_image:
        suggestions.append({
            "tool": "sg_download",
            "reason": "Download the reference image for use in 3D generation.",
            "params_hint": {"entity_type": "Asset", "field": "image"},
        })
        suggestions.append({
            "tool": "maya_vision3d",
            "reason": "Generate a 3D mesh from this asset image.",
            "params_hint": {"action": "generate_image"},
        })
    if entity_type in ("Task", "Version"):
        suggestions.append({
            "tool": "fpt_reporting",
            "reason": "Read the activity stream for this entity.",
            "params_hint": {"action": "activity", "entity_type": entity_type},
        })
    return suggestions[:3]  # hard cap
```

**Option B (deferred)** — YAML-driven rules in
`src/fpt_mcp/suggestions.yml`. User-editable without code change;
loaded at startup, hot-reload optional. Migrate to this when >10 rules
or when non-Python users want to edit.

### 3.3 Emission helper

Single entry point wrapped into tool dispatchers:

```python
# src/fpt_mcp/suggestions.py
def maybe_annotate_with_suggestions(tool_name: str, response: dict) -> dict:
    """Return response with `next_suggested_actions` appended when any
    rule fires. Idempotent — safe to call on already-annotated responses.
    Errors in rule execution are swallowed (suggestions are hints, must
    never break the tool)."""
    if "next_suggested_actions" in response:
        return response
    rule = SUGGESTION_RULES.get(tool_name)
    if not rule:
        return response
    try:
        suggestions = rule(response) or []
    except Exception:
        return response  # non-fatal
    if suggestions:
        response["next_suggested_actions"] = suggestions
    return response
```

Every dispatcher wrapper calls this once before returning:

```python
# server.py @mcp.tool sg_find:
result = sg_find_impl(...)
return maybe_annotate_with_suggestions("sg_find", result)
```

## 4. SYSTEM_PROMPT changes

Add a short rule to both `SYSTEM_PROMPT` (Anthropic) and
`SYSTEM_PROMPT_QWEN` (Qwen) in `src/fpt_mcp/qt/claude_worker.py`:

> If a tool response includes `next_suggested_actions`, read it. You
> may mention 1–3 of them to the user as an aside ("Next you could also
> …") only when the user's explicit request is already satisfied.
> Never chain into a suggestion automatically. Never mention the raw
> field name.

The Qwen variant stays byte-equivalent in structure (one imperative
bullet) per the existing Bucket D policy.

## 5. Candidate rules for v1

Scoped small. Ship with these 5, expand later.

| Trigger | Suggestion(s) |
|---|---|
| `sg_find` returning Asset rows with `image` field set | `sg_download` (image), `maya_vision3d(generate_image)` |
| `sg_find` returning Task/Version rows | `fpt_reporting(activity)` for the first row |
| `sg_download` success of image | `maya_vision3d(generate_image)` |
| `tk_publish` success | `fpt_reporting(note_thread)` to notify, `sg_find(Shot)` for scope |
| `fpt_bulk(delete)` success | `fpt_bulk(revive)` with the same ids (safety-net) |

No suggestions for: `sg_schema`, `sg_upload`, `search_sg_docs`,
`session_stats`, `learn_pattern`, `fpt_launch_app` (terminal /
introspection / action tools whose follow-up is context-dependent).

## 6. Invariant additions

`.concepts.yml` gets a new concept once the feature ships:

```yaml
next_suggested_actions_contract:
  description: >
    Every @mcp.tool that returns a dict and is listed in
    SUGGESTION_RULES must call maybe_annotate_with_suggestions before
    returning. Enforces the chaining hint contract.
  source_of_truth:
    file: src/fpt_mcp/suggestions.py
    symbol: SUGGESTION_RULES
  invariants:
    - id: every_rule_is_wired
      type: subset
      direction: a_subset_b
      a_source:
        type: ast_dict_keys
        file: src/fpt_mcp/suggestions.py
        symbol: SUGGESTION_RULES
      b_source:
        type: file_regex_matches
        file: src/fpt_mcp/server.py
        pattern: 'maybe_annotate_with_suggestions\("([a-z_]+)"'
```

This is the invariant that prevents the feature from silently drifting
(a rule added but the wrapper never calls `maybe_annotate_with_suggestions`
for that tool).

## 7. Open questions

1. **Tool name format** — emit bare names (`"sg_download"`) or fully
   qualified (`"mcp__fpt-mcp__sg_download"`)? Bare is friendlier for
   the user but Claude's tool-call selector prefers fully qualified.
   **Proposal**: emit bare; SYSTEM_PROMPT maps to qualified at
   invocation time.

2. **Cross-MCP suggestions** — rule after `sg_download` suggests
   `maya_vision3d` which is a *different* MCP. Does the user have that
   MCP enabled? **Proposal**: emit the suggestion regardless; Claude
   handles the "not available" case gracefully (graceful degradation
   vs. fpt-mcp knowing about maya-mcp's availability is a worse
   coupling).

3. **User surfacing** — should Claude ALWAYS mention suggestions, or
   only when asked? **Proposal**: mention 1 after a completed workflow,
   never interrupt a request in progress.

4. **Dedup on multi-step chains** — if `sg_find` → `sg_download` →
   `maya_vision3d`, the Step-2 response may again suggest `sg_download`.
   **Proposal**: de-duplicate by checking Claude's recent tool history
   in SYSTEM_PROMPT; do NOT build it into the rule engine.

5. **Testability** — rules are pure functions of response body →
   trivial to unit-test. Add `tests/test_suggestions.py` with 1 test
   per rule.

## 8. Implementation sequence (future session)

1. **Phase 1** — stub `src/fpt_mcp/suggestions.py` with empty
   SUGGESTION_RULES + helper + test scaffolding. Wire into 1 tool
   (`sg_find`). Confirm no regression.
2. **Phase 2** — add 3 rules from §5. Update SYSTEM_PROMPT with the
   surfacing rule.
3. **Phase 3** — add `.concepts.yml` invariant from §6. Flip it to
   strict after a few days of observation.
4. **Phase 4** — audit Qwen runs on glorfindel (O1e gate); if
   suggestions improve tool-chain precision, ship; otherwise drop.

## 9. Out of scope

- maya-mcp / flame-mcp analogous feature — fpt-mcp pattern first, then
  replicate.
- "Previous tool call history" injection into rules. Rules are
  stateless functions of the current response only.
- User preference file for disabling suggestions. Feature is opt-in at
  the rule level (tools without rules emit nothing).

---

**Next action**: a future session opens `src/fpt_mcp/suggestions.py`,
starts Phase 1. No implementation in this doc's session (Chat 46) —
design only, per the explicit "design not implementation" scope.
