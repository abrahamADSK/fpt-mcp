# ShotGrid Python API (shotgun_api3) — Complete Reference for RAG

> **Source**: developers.shotgridsoftware.com/python-api
>
> **Purpose**: This document is indexed by the fpt-mcp RAG engine and serves as the authoritative reference for ShotGrid API calls in automation workflows. Keep it accurate and up-to-date.
>
> **Last Updated**: 2026-03-31

## Connection and Authentication

### Basic Connection

```python
from shotgun_api3 import Shotgun

# Script-based authentication (recommended for automation)
sg = Shotgun("https://site.shotgrid.autodesk.com", "script_name", "script_key")

# User login (NOT recommended for scripts)
sg = Shotgun("https://site.shotgrid.autodesk.com", login="user", password="pw")

# Session token (from web session)
sg = Shotgun("https://site.shotgrid.autodesk.com", session_token="token_value")
```

### Advanced Connection Configuration

```python
# With timeout, proxy, and custom CA cert
sg = Shotgun(
    "https://site.shotgrid.autodesk.com",
    "script_name",
    "script_key",
    timeout=30,                      # seconds
    ca_certs="/path/to/ca-bundle.crt",  # for HTTPS verification
    proxy={"http": "http://proxy.company.com:8080",
           "https": "https://proxy.company.com:8080"},
    connect_timeout=10               # seconds
)

# Get current session info
token = sg.get_session_token()
user = sg.get_session_user()
```

## sg.find() — Search and filter entities

### Signature

```python
sg.find(
    entity_type,          # str: "Asset", "Shot", "Task", "Version", etc.
    filters,              # list[list]: [["field", "operator", value], ...]
    fields=None,          # list[str] or None (returns all fields if None)
    order=None,           # list[dict]: [{"field_name": "code", "direction": "asc"}]
    filter_operator=None, # "all" (default AND) or "any" (OR)
    limit=0,              # int: 0 = unlimited (dangerous!)
    retired_only=False,   # bool: search only retired entities
    page=0,               # int: pagination (rarely used, use limit instead)
    include_archived_projects=True  # bool
)
```

### Returns

```python
[
    {
        "type": "Asset",
        "id": 1,
        "code": "hero",
        "sg_asset_type": "Character",
        "tasks": [{"type": "Task", "id": 123, "name": "model"}],
        ...
    },
    ...
]
```

### Entity Reference Format (CRITICAL — prevents hallucinations)

**CORRECT — Always use dict with type and id:**

```python
# Filter by linked entity
[["entity", "is", {"type": "Asset", "id": 123}]]
[["project", "is", {"type": "Project", "id": 456}]]
[["task", "is", {"type": "Task", "id": 789}]]
[["task_assignees", "is", {"type": "HumanUser", "id": 1}]]

# Multi-entity relationships
[["task_assignees", "in", [
    {"type": "HumanUser", "id": 1},
    {"type": "HumanUser", "id": 2}
]]]
```

**INCORRECT — These will fail:**

```python
[["entity", "is", 123]]                  # ← WRONG: id only, no type
[["project", "is", "MyProject"]]         # ← WRONG: string, not dict
[["task_assignees", "contains", 1]]      # ← WRONG: operator + format
```

### Filter Operators by Field Type

#### Text/String Fields

Operators: `is`, `is_not`, `contains`, `not_contains`, `starts_with`, `ends_with`, `in`, `not_in`

```python
[["code", "is", "hero_robot"]]
[["code", "contains", "hero"]]
[["code", "starts_with", "ch_"]]
[["code", "in", ["hero", "villain", "npc"]]]
[["description", "not_contains", "deprecated"]]
```

#### Entity Fields (single-link)

Operators: `is`, `is_not`, `type_is`, `type_is_not`, `name_contains`, `name_not_contains`, `in`, `not_in`

```python
[["entity", "is", {"type": "Asset", "id": 100}]]
[["entity", "type_is", "Asset"]]         # filter by type regardless of id
[["entity", "type_is_not", "Shot"]]
[["entity", "name_contains", "hero"]]
[["project", "is", {"type": "Project", "id": 1}]]
```

#### Multi-Entity Fields (many-to-many)

Operators: `is`, `is_not`, `type_is`, `type_is_not`, `name_contains`, `name_not_contains`, `in`, `not_in`

```python
# Find tasks assigned to a specific user
[["task_assignees", "is", {"type": "HumanUser", "id": 1}]]

# Find tasks where ANY assignee matches (includes both)
[["task_assignees", "in", [
    {"type": "HumanUser", "id": 1},
    {"type": "HumanUser", "id": 2}
]]]

# Filter by any linked entity name
[["task_assignees", "name_contains", "john"]]
```

#### Number/Integer/Float/Currency/Percent Fields

Operators: `is`, `is_not`, `less_than`, `greater_than`, `between`, `in`, `not_in`

```python
[["frame_count", "is", 100]]
[["frame_count", "greater_than", 50]]
[["frame_count", "between", [100, 200]]]
[["duration", "less_than", 60]]
[["version_number", "in", [1, 2, 3]]]
[["sg_percent_complete", "is", 50]]
```

#### Date/Datetime Fields

Operators: `is`, `is_not`, `greater_than`, `less_than`, `between`, `in_last`, `not_in_last`, `in_next`, `not_in_next`, `in_calendar_day`, `in_calendar_week`, `in_calendar_month`, `in_calendar_year`

```python
# Exact date
[["start_date", "is", "2026-03-31"]]

# Date range
[["due_date", "between", ["2026-03-01", "2026-03-31"]]]

# Relative: tasks due in the next 7 days
[["due_date", "in_next", [7, "DAY"]]]

# Relative: tasks due in the last 30 days
[["due_date", "in_last", [30, "DAY"]]]

# Relative: tasks due next month
[["due_date", "in_next", [1, "MONTH"]]]

# Calendar-based (Tuesday last week)
[["created_at", "in_calendar_week", "last"]]

# Comparison
[["start_date", "greater_than", "2026-01-01"]]
[["end_date", "less_than", "2026-12-31"]]
```

#### Duration Fields

Operators: `is`, `is_not`, `less_than`, `greater_than`, `between`, `in`, `not_in`

```python
# Duration in minutes
[["duration", "is", 480]]        # 8 hours
[["duration", "greater_than", 240]]
[["duration", "between", [120, 480]]]
```

#### Status List Fields

Operators: `is`, `is_not`, `in`, `not_in`

```python
[["sg_status_list", "is", "ip"]]     # In Progress
[["sg_status_list", "in", ["ip", "rdy"]]]
[["sg_status_list", "is_not", "omt"]]  # Not Omitted
```

#### Checkbox Fields (Boolean)

Operators: `is`

```python
[["sg_locked", "is", True]]
[["sg_locked", "is", False]]
```

#### Single-Select List Fields

Operators: `is`, `is_not`, `in`, `not_in`

```python
[["sg_priority", "is", "High"]]
[["sg_priority", "in", ["High", "Medium"]]]
```

#### Tag List Fields

Operators: `is`, `is_not`, `name_contains`, `name_not_contains`

```python
[["sg_tags", "name_contains", "vfx"]]
[["sg_tags", "is", {"id": 123}]]  # tag by id
```

#### URL/Link Fields

Operators: `is`, `is_not` (null checks only)

```python
[["sg_wiki_url", "is", None]]
[["sg_wiki_url", "is_not", None]]
```

#### Image Fields

Operators: `is`, `is_not` (null checks only)

```python
[["image", "is", None]]
[["image", "is_not", None]]
```

#### Color Fields

Operators: `is`, `is_not`

```python
[["sg_color", "is", "#FF0000"]]
```

#### Timecode Fields

Operators: `is`, `is_not`, `less_than`, `greater_than`, `between`

```python
[["sg_timecode", "is", "01:00:00:00"]]
[["sg_timecode", "greater_than", "01:00:00:00"]]
```

#### NOT Filterable

- `pivot_column` — use grouping in summarize() instead
- `serializable` — store as JSON in text field
- `addressing/url_template/uuid` — limited operators (is/is_not only)

### Deep-Link Filtering (Dot Notation)

Access linked entity fields using dot notation:

```python
# Find PublishedFiles where the linked Task's Step is "model"
[["task.Task.step.Step.short_name", "is", "model"]]

# Find Assets where project code matches
[["project.Project.code", "is", "MY_PROJECT"]]

# Find Tasks where assigned user has email containing "vfx"
[["task_assignees.HumanUser.email", "contains", "@vfx.company.com"]]

# Chain multiple links
[["entity.Asset.project.Project.code", "is", "MY_PROJECT"]]
```

### Filter Operators (AND vs OR)

```python
# AND logic (default) — ALL filters must match
filters = [
    ["sg_asset_type", "is", "Character"],
    ["sg_status_list", "is", "ip"]
]
results = sg.find("Asset", filters)

# OR logic — ANY filter matches
filters = [
    ["sg_status_list", "is", "ip"],
    ["sg_status_list", "is", "rdy"]
]
results = sg.find("Asset", filters, filter_operator="any")

# Mixed: (Type is Character) AND (Status is ip OR rdy)
filters = [
    ["sg_asset_type", "is", "Character"],
    {"filter_operator": "any", "filters": [
        ["sg_status_list", "is", "ip"],
        ["sg_status_list", "is", "rdy"]
    ]}
]
results = sg.find("Asset", filters)
```

### Null/Empty Checks

```python
# Find Assets with no description
[["description", "is", None]]

# Find Assets with description
[["description", "is_not", None]]

# Find Tasks with no assignees
[["task_assignees", "is", None]]
```

### Example: Find Assets by Type and Status

```python
assets = sg.find(
    "Asset",
    [
        ["sg_asset_type", "is", "Character"],
        ["sg_status_list", "is", "ip"],
        ["project", "is", {"type": "Project", "id": 123}]
    ],
    fields=["code", "sg_asset_type", "sg_status_list", "tasks", "description"],
    order=[{"field_name": "code", "direction": "asc"}],
    limit=50
)
# Returns: [{"type": "Asset", "id": 1, "code": "hero", ...}, ...]
```

### Example: Find PublishedFiles for a Shot with Deep-Link

```python
publishes = sg.find(
    "PublishedFile",
    [
        ["entity", "is", {"type": "Shot", "id": 1234}],
        ["published_file_type.PublishedFileType.code", "is", "Maya Scene"],
        ["sg_status_list", "is", "pub"]
    ],
    fields=[
        "code", "path", "version_number", "published_file_type",
        "task", "created_by", "created_at"
    ],
    order=[{"field_name": "version_number", "direction": "desc"}],
    limit=10
)
```

### Example: Find Tasks with Complex Filters

```python
tasks = sg.find(
    "Task",
    [
        ["entity", "is", {"type": "Asset", "id": 100}],
        ["step.Step.short_name", "is", "model"],
        ["task_assignees", "is_not", None],
        ["due_date", "in_next", [7, "DAY"]]
    ],
    fields=["content", "sg_status_list", "task_assignees", "step", "due_date"],
    limit=20
)
```

## sg.find_one() — Find single entity

### Signature

```python
entity = sg.find_one(entity_type, filters, fields=None)
```

Returns a single dict or None. Uses identical filter syntax as find().

```python
# Get a single asset
asset = sg.find_one("Asset", [["code", "is", "hero"]], ["code", "sg_asset_type"])

# Get current project
project = sg.find_one("Project", [["sg_status", "is", "Active"]])

# Will return None if not found
task = sg.find_one("Task", [["content", "is", "nonexistent"]])
```

## sg.create() — Create new entities

### Signature

```python
result = sg.create(entity_type, data, return_fields=None)
```

- `entity_type`: str — entity type name
- `data`: dict — field names to values
- `return_fields`: list[str] — fields to return in response
- **Project must be included explicitly** — no auto-linking

### Create Asset Example

```python
asset = sg.create("Asset", {
    "code": "hero_robot",
    "sg_asset_type": "Character",
    "project": {"type": "Project", "id": 123},
    "description": "Main character robot",
    "sg_status_list": "wtg"
}, return_fields=["code", "sg_asset_type", "id"])

# Returns: {"type": "Asset", "id": 500, "code": "hero_robot", ...}
```

### Create Shot Example

```python
shot = sg.create("Shot", {
    "code": "010_010",
    "sg_sequence": {"type": "Sequence", "id": 1},
    "project": {"type": "Project", "id": 123},
    "sg_status_list": "wtg",
    "sg_cut_in": 1001,
    "sg_cut_out": 1100,
    "sg_cut_duration": 100,
    "description": "Hero robot enters scene"
})
```

### Create Task Example

```python
task = sg.create("Task", {
    "content": "Model hero robot",
    "entity": {"type": "Asset", "id": 500},
    "project": {"type": "Project", "id": 123},
    "step": {"type": "Step", "id": 10},
    "task_assignees": [{"type": "HumanUser", "id": 1}],
    "sg_status_list": "rdy",
    "start_date": "2026-04-01",
    "due_date": "2026-04-14",
    "duration": 480
})
```

### Create PublishedFile Example

```python
pf = sg.create("PublishedFile", {
    "code": "hero_robot_model_v001",
    "published_file_type": {"type": "PublishedFileType", "id": 1},
    "entity": {"type": "Asset", "id": 500},
    "task": {"type": "Task", "id": 1001},
    "project": {"type": "Project", "id": 123},
    "path": {"local_path": "/projects/MY_PROJECT/publish/assets/hero_robot/model/v001/hero_robot_model_v001.ma"},
    "version_number": 1,
    "sg_status_list": "pub",
    "description": "Initial model publish",
    "created_by": {"type": "HumanUser", "id": 1}
}, return_fields=["code", "path", "version_number"])
```

### Create Note Example

```python
note = sg.create("Note", {
    "subject": "Review feedback",
    "content": "Great model! Minor adjustments needed on hands.",
    "note_links": [{"type": "Asset", "id": 500}],
    "user": {"type": "HumanUser", "id": 1},
    "project": {"type": "Project", "id": 123},
    "sg_status_list": "open",
    "addressings_to": [{"type": "HumanUser", "id": 2}]
})
```

## sg.update() — Update existing entities

### Signature

```python
result = sg.update(entity_type, entity_id, data)
```

Returns the updated entity dict.

### Update Examples

```python
# Update asset status
sg.update("Asset", 500, {
    "sg_status_list": "cmpt",
    "description": "Final approved version"
})

# Update task progress
sg.update("Task", 1001, {
    "sg_status_list": "ip",
    "task_assignees": [{"type": "HumanUser", "id": 1}]
})

# Update version uploaded movie
sg.update("Version", 2000, {
    "sg_uploaded_movie": {"local_path": "/path/to/render.mov"},
    "sg_path_to_frames": "/path/to/frames/%04d.exr"
})

# Bulk-style update (do one at a time or use batch)
for asset_id in [100, 101, 102]:
    sg.update("Asset", asset_id, {"sg_status_list": "cmpt"})
```

## sg.delete() — Retire (soft-delete) entities

### Signature

```python
result = sg.delete(entity_type, entity_id)
# Returns: True on success, raises exception on failure
```

**Important**: This is a SOFT DELETE. The entity moves to trash and can be restored via the ShotGrid web UI or by setting `retired=False` in an update.

```python
# Retire an asset
sg.delete("Asset", 500)

# Restore a retired asset
sg.update("Asset", 500, {"retired": False})

# Find only retired entities
retired = sg.find("Asset", [], retired_only=True)
```

## sg.revive() — Restore retired entities

### Signature

```python
result = sg.revive(entity_type, entity_id)
```

Restores a retired entity (equivalent to `update(..., {"retired": False})`).

```python
sg.revive("Asset", 500)
```

## sg.batch() — Batch operations (transactional)

### Signature

```python
results = sg.batch(request_list)
```

Executes multiple create/update/delete operations in a single API call. **TRANSACTIONAL**: if any operation fails, the entire batch is rolled back.

```python
batch_data = [
    {
        "request_type": "create",
        "entity_type": "Asset",
        "data": {
            "code": "new_asset",
            "sg_asset_type": "Character",
            "project": {"type": "Project", "id": 123}
        }
    },
    {
        "request_type": "update",
        "entity_type": "Asset",
        "entity_id": 500,
        "data": {"sg_status_list": "cmpt"}
    },
    {
        "request_type": "delete",
        "entity_type": "Task",
        "entity_id": 1001
    }
]

results = sg.batch(batch_data)
# Returns: [{"type": "Asset", "id": 501, ...}, {"type": "Asset", "id": 500, ...}, True]
```

**Best Practices**:
- Limit batch size to 100 operations (split into multiple batches for larger jobs)
- Batch operations are MUCH faster than individual calls (10-50x speedup)
- Use batch for bulk updates, mass creates, and deletions
- If ANY operation fails, the ENTIRE batch fails

## sg.upload() — Upload files to entity fields

### Signature

```python
sg.upload(entity_type, entity_id, file_path, field_name="field", display_name=None)
```

Uploads a file to any file-type field (sg_uploaded_movie, attachments, etc.).

```python
# Upload rendered movie to Version
sg.upload("Version", 2000, "/local/path/render.mov", field_name="sg_uploaded_movie")

# Upload with custom display name
sg.upload("Version", 2000, "/local/path/render.mov",
          field_name="sg_uploaded_movie",
          display_name="final_comp_v001.mov")

# Upload image to Asset
sg.upload("Asset", 500, "/local/path/concept.jpg", field_name="image")
```

## sg.upload_thumbnail() — Upload entity thumbnail

### Signature

```python
sg.upload_thumbnail(entity_type, entity_id, file_path)
```

Uploads a thumbnail image for the entity (visible in grid/list views).

```python
sg.upload_thumbnail("Asset", 500, "/local/path/hero_thumb.jpg")
sg.upload_thumbnail("Shot", 1234, "/local/path/shot_frame.png")
```

## sg.upload_filmstrip_thumbnail() — Upload filmstrip thumbnail (deprecated)

### Signature

```python
sg.upload_filmstrip_thumbnail(entity_type, entity_id, file_path)
```

Legacy method. Use `upload_thumbnail()` instead.

## sg.download_attachment() — Download file from entity

### Signature

```python
local_path = sg.download_attachment(attachment_dict)
```

Downloads a file attachment or field to local disk.

```python
# First find the attachment reference
asset = sg.find_one("Asset", [["code", "is", "hero"]], ["image"])

if asset["image"]:
    # Download the image
    local_path = sg.download_attachment(asset["image"])
    # Returns: "/tmp/shotgun_downloads/image_12345.jpg"
```

## sg.schema_field_read() — Read entity field schema

### Signature

```python
schema = sg.schema_field_read(entity_type, field_name=None)
```

Returns field metadata for an entity type. Essential for discovering available fields and their types.

```python
# Get schema for all Asset fields
schema = sg.schema_field_read("Asset")
# Returns: {
#     "code": {"data_type": {"value": "text"}, ...},
#     "sg_asset_type": {"data_type": {"value": "text"}, ...},
#     "description": {"data_type": {"value": "text"}, ...},
#     ...
# }

# Get schema for a single field
schema = sg.schema_field_read("Asset", "sg_asset_type")
# Returns: {"sg_asset_type": {"data_type": {...}, ...}}
```

**Use Cases**:
- Discover field names on an entity type
- Check field data type (text, number, date, entity, etc.)
- Check if field is editable
- Check field constraints and valid values

## sg.schema_entity_read() — Read entity type schema

### Signature

```python
schema = sg.schema_entity_read()
```

Returns metadata for all entity types available in your ShotGrid instance.

```python
schema = sg.schema_entity_read()
# Returns: {
#     "Asset": {"label": "Asset", ...},
#     "Shot": {"label": "Shot", ...},
#     "Task": {"label": "Task", ...},
#     ...
# }
```

## sg.schema_field_create() — Create custom field

### Signature

```python
result = sg.schema_field_create(entity_type, data_type, field_name, properties=None)
```

Creates a new custom field on an entity type. Requires admin privileges.

```python
# Create a custom text field
sg.schema_field_create("Asset", "text", "sg_custom_notes", {
    "description": "Custom notes field",
    "ui_attr": {"ui_item_type": "text"}
})

# Create a custom checkbox field
sg.schema_field_create("Task", "checkbox", "sg_is_urgent")
```

## sg.schema_field_update() — Update field schema

### Signature

```python
result = sg.schema_field_update(entity_type, field_name, properties)
```

Updates field properties (display name, description, etc.). Limited changes — cannot change data type.

```python
sg.schema_field_update("Asset", "sg_custom_notes", {
    "description": "Updated description"
})
```

## sg.schema_field_delete() — Delete custom field

### Signature

```python
result = sg.schema_field_delete(entity_type, field_name)
```

Deletes a custom field. Only custom fields can be deleted; built-in fields are protected.

```python
sg.schema_field_delete("Asset", "sg_custom_notes")
```

## sg.schema_read() — Read all schema at once

### Signature

```python
schema = sg.schema_read()
```

Returns comprehensive schema for entities and fields. More expensive call but gives everything.

```python
schema = sg.schema_read()
# Returns: {"Asset": {...}, "Shot": {...}, ...}
```

## sg.summarize() — Aggregation and grouping

### Signature

```python
result = sg.summarize(
    entity_type,
    filters,
    summary_fields=None,  # list of aggregations
    grouping=None,        # list of grouping expressions
    order=None,           # sort aggregation results
    limit=0               # limit result rows
)
```

Performs SQL-like GROUP BY aggregations. Returns aggregated results without individual entity records.

### Summary Field Types

- `sum` — sum of numeric field
- `count` — count of records
- `avg` — average of numeric field
- `min` — minimum value
- `max` — maximum value
- `count_distinct` — count unique values

### Example: Sum Task Duration by Status

```python
result = sg.summarize(
    "Task",
    [["entity", "is", {"type": "Asset", "id": 500}]],
    summary_fields=[
        {"field": "duration", "type": "sum"},
        {"field": "id", "type": "count"}
    ],
    grouping=[
        {"field": "sg_status_list", "type": "exact", "direction": "asc"}
    ]
)
# Returns: [
#     {"sg_status_list": "ip", "duration": 480, "id": 2},
#     {"sg_status_list": "rdy", "duration": 240, "id": 1},
#     ...
# ]
```

### Example: Count Shots by Sequence

```python
result = sg.summarize(
    "Shot",
    [["project", "is", {"type": "Project", "id": 123}]],
    summary_fields=[
        {"field": "id", "type": "count"}
    ],
    grouping=[
        {"field": "sg_sequence", "type": "exact"}
    ],
    order=[{"field": "id", "direction": "desc"}],
    limit=10
)
```

### Example: Max Version Number by Asset

```python
result = sg.summarize(
    "PublishedFile",
    [["entity.Asset.project", "is", {"type": "Project", "id": 123}]],
    summary_fields=[
        {"field": "version_number", "type": "max"}
    ],
    grouping=[
        {"field": "entity", "type": "exact"}
    ]
)
```

## sg.text_search() — Full-text search

### Signature

```python
result = sg.text_search(search_string, entity_types=None, limit=10)
```

Searches across text fields in specified entity types.

```python
# Search all entity types
results = sg.text_search("hero robot", limit=20)

# Search specific entity types only
results = sg.text_search("hero", entity_types=["Asset", "Shot"], limit=10)
```

## sg.note_thread_read() — Read note thread replies

### Signature

```python
replies = sg.note_thread_read(note_id)
```

Returns all replies to a note (including the original note).

```python
note = sg.find_one("Note", [["subject", "is", "Review feedback"]])
replies = sg.note_thread_read(note["id"])
# Returns: [original_note, reply1, reply2, ...]
```

## sg.activity_stream_read() — Read activity stream

### Signature

```python
activity = sg.activity_stream_read(
    entity_type,
    entity_id,
    mode="all",           # "all", "projects", "tasks"
    limit=100,
    offset=0
)
```

Reads activity log/event stream for an entity.

```python
activity = sg.activity_stream_read("Asset", 500, mode="all", limit=50)
# Returns: [
#     {"id": 1, "event_type": "Shotgun_Asset_Change", "user": {...}, ...},
#     ...
# ]
```

## sg.share_thumbnail() — Share entity thumbnail

### Signature

```python
result = sg.share_thumbnail(entity_type, entity_id)
```

Generates a shareable thumbnail URL for the entity.

```python
result = sg.share_thumbnail("Asset", 500)
# Returns: {"url": "https://sg-media-uom-prod.s3.amazonaws.com/..."}
```

## sg.get_session_token() — Get current session token

### Signature

```python
token = sg.get_session_token()
```

Returns the current session token string (used for web session management).

```python
token = sg.get_session_token()
# Returns: "abc123def456..."
```

## sg.get_session_user() — Get current user

### Signature

```python
user = sg.get_session_user()
```

Returns the HumanUser dict for the authenticated user.

```python
user = sg.get_session_user()
# Returns: {"type": "HumanUser", "id": 1, "login": "john.doe"}
```

## sg.set_session_uuid() — Set session UUID (advanced)

### Signature

```python
sg.set_session_uuid(uuid_string)
```

Low-level method for session management. Rarely used in scripts.

## sg.authenticate_human_user() — Authenticate as user (web flow)

### Signature

```python
token = sg.authenticate_human_user(user_login, user_password)
```

Authenticates as a specific user and returns session token. Use for web login flows only — not recommended for scripts.

```python
token = sg.authenticate_human_user("john.doe", "password123")
```

## sg.preferences_read() — Read user preferences

### Signature

```python
prefs = sg.preferences_read()
```

Returns current user's ShotGrid preferences (UI settings, etc.).

```python
prefs = sg.preferences_read()
# Returns: {"ui_theme": "dark", "language": "en", ...}
```

## sg.following() — Get entities user is following

### Signature

```python
following = sg.following()
```

Returns list of entities the current user is following.

```python
following = sg.following()
# Returns: [
#     {"type": "Asset", "id": 100},
#     {"type": "Shot", "id": 200},
#     ...
# ]
```

## sg.followers() — Get followers of an entity

### Signature

```python
followers_list = sg.followers(entity_type, entity_id)
```

Returns HumanUsers following a specific entity.

```python
followers = sg.followers("Asset", 500)
# Returns: [
#     {"type": "HumanUser", "id": 1, "name": "John Doe"},
#     ...
# ]
```

## sg.follow() — Follow an entity

### Signature

```python
result = sg.follow(entity_type, entity_id)
```

Marks an entity as followed by current user.

```python
sg.follow("Asset", 500)
```

## sg.unfollow() — Unfollow an entity

### Signature

```python
result = sg.unfollow(entity_type, entity_id)
```

Removes entity from current user's following list.

```python
sg.unfollow("Asset", 500)
```

## Common Entity Types and Fields

### Project

**Key Fields**:
- `code` (text) — Project identifier
- `name` (text) — Project display name
- `sg_status` (status) — Active, Completed, On Hold, etc.
- `start_date` (date) — Project start
- `end_date` (date) — Project end
- `sg_description` (text) — Project description
- `users` (multi_entity) — Users assigned to project
- `tank_name` (text) — Toolkit config name

### Asset

**Key Fields**:
- `code` (text) — Asset identifier
- `sg_asset_type` (text) — Character, Prop, Environment, etc.
- `sg_status_list` (status) — wtg, rdy, ip, hld, cmpt, omt
- `tasks` (multi_entity) — Task records
- `description` (text) — Asset description
- `project` (entity) — Linked Project
- `updated_at` (datetime) — Last modification time
- `created_at` (datetime) — Creation time

### Shot

**Key Fields**:
- `code` (text) — Shot identifier
- `sg_sequence` (entity) — Linked Sequence
- `sg_status_list` (status) — wtg, rdy, ip, hld, cmpt, omt
- `sg_cut_in` (number) — Cut-in frame
- `sg_cut_out` (number) — Cut-out frame
- `sg_head_in` (number) — Handle head frame
- `sg_tail_out` (number) — Handle tail frame
- `sg_cut_duration` (number) — Duration in frames
- `tasks` (multi_entity) — Task records
- `project` (entity) — Linked Project

### Sequence

**Key Fields**:
- `code` (text) — Sequence identifier
- `shots` (multi_entity) — Shot records
- `sg_status_list` (status) — wtg, rdy, ip, hld, cmpt, omt
- `project` (entity) — Linked Project

### Task

**Key Fields**:
- `content` (text) — Task name
- `sg_status_list` (status) — wtg, rdy, ip, hld, cmpt, omt
- `task_assignees` (multi_entity) — HumanUsers assigned
- `entity` (entity) — Asset/Shot/Sequence being worked on
- `step` (entity) — Pipeline Step
- `start_date` (date) — Task start
- `due_date` (date) — Task due date
- `duration` (number) — Estimated hours
- `project` (entity) — Linked Project
- `updated_at` (datetime) — Last change

### Version

**Key Fields**:
- `code` (text) — Version identifier
- `sg_task` (entity) — Linked Task
- `entity` (entity) — Asset/Shot being versioned
- `sg_status_list` (status) — wtg, rdy, ip, hld, cmpt, omt
- `sg_uploaded_movie` (file) — Video file
- `sg_path_to_movie` (text) — Movie path (legacy)
- `sg_path_to_frames` (text) — Frame sequence path
- `user` (entity) — Creator (HumanUser)
- `project` (entity) — Linked Project
- `description` (text) — Version notes
- `frame_count` (number) — Total frames
- `frame_range` (text) — Frame range string

### PublishedFile

**Key Fields**:
- `code` (text) — Publish identifier
- `published_file_type` (entity) — PublishedFileType
- `entity` (entity) — Asset/Shot published
- `task` (entity) — Linked Task
- `path` (file) — Local/cloud file path
- `path_cache` (text) — Cached path for lookups
- `version_number` (number) — Incrementing version
- `sg_status_list` (status) — wtg, pub, omt
- `project` (entity) — Linked Project
- `description` (text) — Publish notes
- `created_by` (entity) — Creator (HumanUser)
- `created_at` (datetime) — Creation time
- `version` (entity) — Linked Version (if rendered)

### PublishedFileType

**Key Fields**:
- `code` (text) — Type name (Maya Scene, Rendered Movie, etc.)
- `description` (text) — Description

### Note

**Key Fields**:
- `subject` (text) — Note subject
- `content` (text) — Note body
- `note_links` (multi_entity) — Asset/Shot/Task the note is on
- `addressings_to` (multi_entity) — HumanUsers addressed (TO)
- `addressings_cc` (multi_entity) — HumanUsers addressed (CC)
- `sg_status_list` (status) — open, closed
- `user` (entity) — Author (HumanUser)
- `project` (entity) — Linked Project
- `reply_content` (text) — Latest reply text
- `created_at` (datetime) — Creation time

### HumanUser

**Key Fields**:
- `login` (text) — Username
- `name` (text) — Display name
- `email` (text) — Email address
- `department` (entity) — Department
- `groups` (multi_entity) — Group memberships
- `sg_status_list` (status) — Active, Inactive
- `created_at` (datetime) — Account creation

### Step

**Key Fields**:
- `code` (text) — Step name
- `short_name` (text) — Abbreviated name (3-4 chars)
- `entity_type` (text) — Asset or Shot
- `color` (color) — Display color (#RRGGBB)

### TimeLog

**Key Fields**:
- `duration` (number) — Hours logged
- `entity` (entity) — Task the time is on
- `date` (date) — Date logged
- `user` (entity) — HumanUser who logged time
- `project` (entity) — Linked Project
- `description` (text) — Work description

### PipelineConfiguration

**Key Fields**:
- `code` (text) — Config name
- `mac_path` (text) — macOS Toolkit path
- `linux_path` (text) — Linux Toolkit path
- `windows_path` (text) — Windows Toolkit path
- `descriptor` (text) — Config descriptor
- `project` (entity) — Linked Project

### EventLogEntry

**Key Fields**:
- `event_type` (text) — Event category
- `description` (text) — Event description
- `entity` (entity) — Entity that triggered event
- `meta` (text) — JSON metadata
- `user` (entity) — HumanUser who triggered
- `project` (entity) — Linked Project
- `created_at` (datetime) — Event timestamp

### Playlist

**Key Fields**:
- `code` (text) — Playlist name
- `versions` (multi_entity) — Versions in playlist
- `sg_status_list` (status) — Active, etc.
- `project` (entity) — Linked Project

## Common Status Values

### Asset/Shot/Sequence

- `wtg` — Waiting to Start
- `rdy` — Ready to Start
- `ip` — In Progress
- `hld` — On Hold
- `cmpt` — Final/Complete
- `omt` — Omitted (hidden)

### Task

Same as Asset/Shot:
- `wtg` — Waiting to Start
- `rdy` — Ready to Start
- `ip` — In Progress
- `hld` — On Hold
- `cmpt` — Complete
- `omt` — Omitted

### Version

- `wtg` — Waiting to Start
- `rdy` — Ready
- `ip` — In Progress
- `hld` — On Hold
- `cmpt` — Complete
- `omt` — Omitted

### PublishedFile

- `wtg` — Waiting to Start
- `pub` — Published
- `omt` — Omitted

### Note

- `open` — Open
- `closed` — Closed

### HumanUser

- `Active` — User is active
- `Inactive` — User is disabled

## Error Handling and Retry Semantics

### Shotgun Fault Exception

```python
from shotgun_api3 import Fault

try:
    result = sg.find("Asset", [["code", "is", "nonexistent"]])
except Fault as e:
    print(f"Error: {e.faultCode} - {e.faultString}")
```

### Common Error Codes

| Code | Meaning | Retry? |
|------|---------|--------|
| 500 | Timeout/Connection | YES |
| 502 | Bad Gateway | YES |
| 503 | Service Unavailable | YES |
| 400 | Invalid Request | NO |
| 401 | Unauthorized | NO (auth failed) |
| 403 | Forbidden | NO |
| 404 | Not Found | NO |

### Built-in Retry Logic

The ShotGrid API has automatic retry:

- **MAX_ATTEMPTS**: 3 retries
- **Backoff**: 0.75 seconds × attempt number
- **Configurable via**:
  ```python
  import os
  os.environ["SHOTGUN_API_RETRY_INTERVAL"] = "1.0"
  # or
  sg.config.rpc_attempt_interval = 1.0
  ```
- **Retries transient errors only**: timeout, connection reset, network errors
- **Does NOT retry**: general exceptions, authentication, validation errors
- **Reason**: Prevents duplicate creates (idempotency)

### Manual Retry Pattern

```python
import time

def find_with_retry(sg, entity_type, filters, max_attempts=3):
    for attempt in range(max_attempts):
        try:
            return sg.find(entity_type, filters)
        except Fault as e:
            if attempt < max_attempts - 1:
                wait_time = 2 ** attempt  # exponential backoff
                print(f"Attempt {attempt + 1} failed, retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise
```

## INVALID Operators (Common Hallucinations)

**These do NOT exist in the ShotGrid API — never use them:**

- `is_exactly` — use `is` instead
- `exact` — use `is` instead
- `matches` — use `contains` or deep-link instead
- `regex` — not supported
- `like` — use `contains` instead
- `ilike` — use `contains` instead (case-insensitive)
- `eq` — use `is` instead
- `neq` — use `is_not` instead
- `gt` — use `greater_than` instead
- `lt` — use `less_than` instead
- `gte` — use `greater_than` or `is` instead
- `lte` — use `less_than` or `is` instead
- `equal` — use `is` instead
- `not_equal` — use `is_not` instead
- `in_range` — use `between` instead

## Anti-Patterns (NEVER do these)

### 1. No Fields Specified with Large Datasets

```python
# WRONG — fetches ALL fields for ALL assets (very slow)
assets = sg.find("Asset", [], None)

# RIGHT — specify only needed fields with a limit
assets = sg.find("Asset", [], ["code", "sg_asset_type"], limit=100)
```

### 2. Unlimited Results with Empty Filters

```python
# WRONG — potentially thousands of entities
all_tasks = sg.find("Task", [], ["content"], limit=0)

# RIGHT — filter + reasonable limit
my_tasks = sg.find(
    "Task",
    [["task_assignees", "is", {"type": "HumanUser", "id": 1}]],
    ["content"],
    limit=100
)
```

### 3. String Entity References Instead of Dicts

```python
# WRONG
tasks = sg.find("Task", [["entity", "is", "100"]], ["content"])

# RIGHT
tasks = sg.find("Task", [["entity", "is", {"type": "Asset", "id": 100}]], ["content"])
```

### 4. SQL-Style Filter Operators

```python
# WRONG — these don't exist
[["code", "LIKE", "%hero%"]]
[["frame_count", ">", 100]]
[["sg_status_list", "IN", ["ip", "rdy"]]]

# RIGHT
[["code", "contains", "hero"]]
[["frame_count", "greater_than", 100]]
[["sg_status_list", "in", ["ip", "rdy"]]]
```

### 5. Large Batch Operations (>100 items)

```python
# WRONG — batch too large, prone to timeout
large_batch = [{"request_type": "create", ...} for _ in range(500)]
sg.batch(large_batch)

# RIGHT — split into chunks
for i in range(0, len(large_batch), 100):
    chunk = large_batch[i:i+100]
    sg.batch(chunk)
```

### 6. Modifying path Field on Existing PublishedFile

```python
# WRONG — breaks all references in software
sg.update("PublishedFile", 1000, {"path": "/new/path"})

# RIGHT — create a new PublishedFile with updated version_number
sg.create("PublishedFile", {
    "code": "hero_robot_model_v002",
    "path": {"local_path": "/new/path"},
    "version_number": 2,
    ...
})
```

### 7. Not Handling Null Results

```python
# WRONG — crashes if find_one returns None
entity = sg.find_one("Asset", [["code", "is", "nonexistent"]])
print(entity["code"])  # KeyError

# RIGHT
entity = sg.find_one("Asset", [["code", "is", "nonexistent"]])
if entity:
    print(entity["code"])
else:
    print("Entity not found")
```

### 8. Assuming Field Exists Without Schema Check

```python
# WRONG — custom fields might not exist in all projects
asset = sg.find_one("Asset", [["code", "is", "hero"]], ["sg_custom_field"])

# RIGHT — check schema or handle gracefully
schema = sg.schema_field_read("Asset")
if "sg_custom_field" in schema:
    asset = sg.find_one("Asset", [["code", "is", "hero"]], ["sg_custom_field"])
else:
    # field doesn't exist
    pass
```

### 9. Creating Duplicates Without Uniqueness Check

```python
# WRONG — creates duplicate assets if script runs twice
sg.create("Asset", {"code": "hero", "project": {...}})

# RIGHT — check for existing first
existing = sg.find_one("Asset", [["code", "is", "hero"]], ["id"])
if not existing:
    sg.create("Asset", {"code": "hero", "project": {...}})
```

### 10. Not Using Project Filter in find()

```python
# WRONG — if your instance has multiple projects
tasks = sg.find("Task", [["content", "is", "model"]], ["content"])

# RIGHT — always specify project if you care
tasks = sg.find(
    "Task",
    [
        ["content", "is", "model"],
        ["project", "is", {"type": "Project", "id": 123}]
    ],
    ["content"]
)
```

## Tips for RAG System Integration

1. **Use headers extensively** — Each `##` section is a chunk boundary. Keep methods self-contained.
2. **Include examples** — RAG systems retrieve based on context; examples improve relevance.
3. **List valid operators** — The operator matrix prevents hallucinations about nonexistent operators.
4. **Highlight anti-patterns** — Explicitly list what NOT to do.
5. **Keep entity reference format prominent** — This is the #1 mistake in generated code.
6. **Include field names** — Each entity type lists its common fields for context.
7. **Use consistent formatting** — Code blocks, bullet points, and tables are easier for RAG to chunk.

---

**Document Version**: 2026-03-31
**API Version**: shotgun_api3
**Scope**: Python automation, script-based authentication, standard find/create/update/delete workflows
