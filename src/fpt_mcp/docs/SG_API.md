# ShotGrid Python API (shotgun_api3) — Reference for RAG

> Source: developers.shotgridsoftware.com/python-api
> This document is indexed by the fpt-mcp RAG engine. Keep it accurate.

## Connection and Authentication

```python
from shotgun_api3 import Shotgun
sg = Shotgun("https://site.shotgrid.autodesk.com", "script_name", "script_key")
```

Three auth methods:
- `Shotgun(url, script_name, api_key)` — script-based (most common for automation)
- `Shotgun(url, login=user, password=pw)` — user login (not recommended for scripts)
- `Shotgun(url, session_token=token)` — session token (from web session)

## sg.find() — Search entities

```python
sg.find(entity_type, filters, fields=None, order=None,
        filter_operator=None, limit=0, retired_only=False,
        page=0, include_archived_projects=True,
        additional_filter_presets=None)
```

- `entity_type`: str — "Asset", "Shot", "Task", "Version", "PublishedFile", "Sequence", "HumanUser", "Project", "Note", etc.
- `filters`: list of lists — ALWAYS `[["field", "operator", value]]`
- `fields`: list[str] — fields to return. If None, returns ALL fields (expensive!)
- `order`: list of dicts — `[{"field_name": "code", "direction": "asc"}]`
- `limit`: int — 0 = unlimited (dangerous for large datasets)

### Entity reference format (CRITICAL — common hallucination)

CORRECT:
```python
[["entity", "is", {"type": "Asset", "id": 123}]]
[["project", "is", {"type": "Project", "id": 456}]]
[["task", "is", {"type": "Task", "id": 789}]]
```

INCORRECT (will fail or return wrong results):
```python
[["entity", "is", 123]]         # ← WRONG: must be dict with type/id
[["project", "is", "MyProject"]] # ← WRONG: must be dict, not string
```

### Filter operators by field type

- `text/str`: is, is_not, contains, not_contains, starts_with, ends_with, in, not_in
- `entity`: is, is_not, type_is, type_is_not, name_contains, name_not_contains, in, not_in
- `multi_entity`: is, is_not, name_contains, name_not_contains, in, not_in
- `number/float`: is, is_not, less_than, greater_than, between, in, not_in
- `date/datetime`: is, is_not, greater_than, less_than, in_last, not_in_last, in_next, not_in_next, in_calendar_day/week/month/year, between
- `status_list`: is, is_not, in, not_in
- `checkbox`: is (True/False)
- `list (single select)`: is, is_not, in, not_in
- `tag_list`: is, is_not, name_contains

INVALID operators (common LLM hallucinations):
- `is_exactly` — does NOT exist, use `is`
- `exact` — does NOT exist, use `is`
- `matches` — does NOT exist
- `regex` — does NOT exist
- `like` — does NOT exist, use `contains`

### Example: find Assets by type and status

```python
assets = sg.find("Asset",
    [["sg_asset_type", "is", "Character"],
     ["sg_status_list", "is", "ip"]],
    ["code", "sg_asset_type", "tasks", "description"],
    order=[{"field_name": "code", "direction": "asc"}],
    limit=50
)
# Returns: [{"type": "Asset", "id": 1, "code": "hero", ...}, ...]
```

### Example: find PublishedFiles for a Shot

```python
publishes = sg.find("PublishedFile",
    [["entity", "is", {"type": "Shot", "id": 1234}],
     ["published_file_type.PublishedFileType.code", "is", "Maya Scene"]],
    ["code", "path", "version_number", "published_file_type", "task"],
    order=[{"field_name": "version_number", "direction": "desc"}],
    limit=10
)
```

### Example: find Tasks for an Asset

```python
tasks = sg.find("Task",
    [["entity", "is", {"type": "Asset", "id": 100}],
     ["step.Step.short_name", "is", "model"]],
    ["content", "sg_status_list", "task_assignees", "step"],
    limit=20
)
```

## sg.find_one() — Find single entity

```python
entity = sg.find_one(entity_type, filters, fields)
```

Returns a single dict or None. Same filter syntax as find().

## sg.create() — Create entities

```python
result = sg.create(entity_type, data, return_fields=None)
```

- `data`: dict — field values for the new entity
- Project is NOT auto-added — include it explicitly

```python
asset = sg.create("Asset", {
    "code": "hero_robot",
    "sg_asset_type": "Character",
    "project": {"type": "Project", "id": 123},
    "description": "Main character",
    "sg_status_list": "wtg",
})
```

### Creating PublishedFile

```python
pf = sg.create("PublishedFile", {
    "code": "hero_robot_model_v001",
    "published_file_type": {"type": "PublishedFileType", "id": 1},
    "entity": {"type": "Asset", "id": 100},
    "project": {"type": "Project", "id": 123},
    "path": {"local_path": "/path/to/file.ma"},
    "version_number": 1,
    "task": {"type": "Task", "id": 456},
    "sg_status_list": "wtg",
    "description": "Initial model publish",
})
```

## sg.update() — Update entities

```python
result = sg.update(entity_type, entity_id, data)
```

```python
sg.update("Asset", 100, {
    "sg_status_list": "cmpt",
    "description": "Final approved version",
})
```

## sg.delete() — Retire (soft-delete) entities

```python
result = sg.delete(entity_type, entity_id)
# Returns True on success
```

This is a SOFT DELETE (retire). Entity moves to trash and can be restored.

## sg.batch() — Batch operations

```python
batch_data = [
    {"request_type": "create", "entity_type": "Shot", "data": {...}},
    {"request_type": "update", "entity_type": "Shot", "entity_id": 1, "data": {...}},
    {"request_type": "delete", "entity_type": "Shot", "entity_id": 2},
]
results = sg.batch(batch_data)
```

IMPORTANT: Batch operations are TRANSACTIONAL — if any operation fails, ALL are rolled back.

## sg.upload() and sg.upload_thumbnail()

```python
# Upload file to field
sg.upload(entity_type, entity_id, file_path, field_name="sg_uploaded_movie", display_name="v001.mov")

# Upload thumbnail
sg.upload_thumbnail(entity_type, entity_id, file_path)
```

## sg.schema_field_read() — Read schema

```python
schema = sg.schema_field_read(entity_type, field_name=None)
# Returns: {field_name: {properties...}, ...}
```

Use this to discover what fields exist on an entity type.

## Error handling and retry

The ShotGrid API has built-in retry:
- MAX_ATTEMPTS = 3
- Backoff delay: 0.75 seconds × attempt number
- Configurable: `SHOTGUN_API_RETRY_INTERVAL` env var or `sg.config.rpc_attempt_interval`
- Only retries transient errors (timeout, connection, network)
- Does NOT retry general exceptions (to avoid duplicate creates)

## Common status values

- `wtg` — Waiting to Start
- `rdy` — Ready to Start
- `ip` — In Progress
- `hld` — On Hold
- `cmpt` — Final/Complete
- `omt` — Omitted (hidden from most views)
- `pub` — Published (for PublishedFile)

## Anti-patterns (NEVER do these)

- `sg.find("Asset", [], None)` — returns ALL fields for ALL assets (extremely slow)
- `sg.find(..., limit=0)` with empty filters — unlimited results, no filter
- Using string instead of dict for entity links in filters
- Assuming filter operators from SQL (LIKE, ILIKE, REGEXP don't exist)
- Calling `sg.batch()` with 100+ operations — split into smaller batches
- Modifying `path` field on existing PublishedFile — breaks references
