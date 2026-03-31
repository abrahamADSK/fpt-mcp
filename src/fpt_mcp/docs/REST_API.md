# ShotGrid REST API — Complete Reference for RAG

> **Source:** developers.shotgridsoftware.com/rest-api
> **Important:** fpt-mcp uses the Python SDK (shotgun_api3), NOT the REST API directly.
> **Purpose:** This document prevents LLM confusion between the two APIs and provides complete REST reference.

## Quick Context: Which API does fpt-mcp use?

fpt-mcp uses **Python SDK (shotgun_api3)** exclusively. All sg_* tools in fpt-mcp call methods like:
- `sg.find(entity_type, filters, fields)`
- `sg.create(entity_type, data)`
- `sg.update(entity_type, entity_id, data)`

This REST API documentation exists **as reference only** to clarify that REST and Python SDK are different interfaces to the same ShotGrid backend.

---

## Authentication and OAuth 2.0

### Client Credentials Grant (Machine-to-Machine)

**Endpoint:** `POST /api/v1/auth/access_token`

**Request:**
```
POST https://{site_name}.shotgunstudio.com/api/v1/auth/access_token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&client_id={CLIENT_ID}&client_secret={CLIENT_SECRET}
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "user"
}
```

### Authorization Code Grant (Web Applications)

**Step 1 - Redirect user to authorization:**
```
GET https://{site_name}.shotgunstudio.com/api/v1/auth/authorize_user
  ?client_id={CLIENT_ID}
  &response_type=code
  &redirect_uri=https://app.example.com/callback
  &state=random_state_string
  &scope=user
```

**Step 2 - Exchange code for token:**
```
POST https://{site_name}.shotgunstudio.com/api/v1/auth/access_token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
&client_id={CLIENT_ID}
&client_secret={CLIENT_SECRET}
&code={AUTH_CODE}
&redirect_uri=https://app.example.com/callback
```

### Refresh Token Flow

**Request:**
```
POST https://{site_name}.shotgunstudio.com/api/v1/auth/access_token
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token
&client_id={CLIENT_ID}
&client_secret={CLIENT_SECRET}
&refresh_token={REFRESH_TOKEN}
```

**Response:**
```json
{
  "access_token": "new_token...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "refresh_token": "new_refresh_token..."
}
```

### Session Token Authentication

Legacy method using server-generated session tokens:

**Get session token:**
```
POST /api/v1/auth/session_token
Authorization: Basic base64(username:password)
```

**Use in subsequent requests:**
```
GET /api/v1/entity/assets
X-ShotGrid-Session-Token: {session_token}
```

### Token Structure and Expiration

- **Access Token Lifetime:** 3600 seconds (1 hour)
- **Refresh Token Lifetime:** 30 days
- **Format:** JWT (JSON Web Token)
- **Payload contains:** `sub` (user ID), `aud` (audience), `exp` (expiration), `scope`

### Required Headers

All REST API requests require:

```
Authorization: Bearer {access_token}
Accept: application/json
Content-Type: application/json
```

Optional for pagination/filtering:
```
X-ShotGrid-User-Agent: MyApp/1.0
```

---

## CRUD Endpoints — Complete Syntax

### List Entities

**Endpoint:** `GET /api/v1/entity/{entity_type}`

**Parameters:**
- `fields` — comma-separated field names to return (sparse fieldset)
- `filter[field_name]` — simple filter (eq operator)
- `filter[field_name][operator]` — with explicit operator
- `sort` — comma-separated fields; prefix `-` for descending
- `page[size]` — results per page (default 20, max 500)
- `page[number]` — page number (1-based)
- `include` — comma-separated related entity types to expand

**Request:**
```
GET /api/v1/entity/assets?fields=code,sg_status_list&filter[sg_status_list]=ip&sort=-updated_at&page[size]=50&page[number]=1
Authorization: Bearer {token}
Accept: application/json
```

**Response (JSONAPI format):**
```json
{
  "data": [
    {
      "type": "Asset",
      "id": "123",
      "attributes": {
        "code": "hero_character",
        "sg_status_list": "ip",
        "updated_at": "2024-01-15T10:30:00Z"
      },
      "relationships": {
        "project": {
          "data": { "type": "Project", "id": "45" },
          "links": { "self": "/api/v1/entity/assets/123/relationships/project" }
        }
      },
      "links": { "self": "/api/v1/entity/assets/123" }
    }
  ],
  "links": {
    "self": "/api/v1/entity/assets?page[size]=50&page[number]=1",
    "next": "/api/v1/entity/assets?page[size]=50&page[number]=2",
    "prev": null
  },
  "meta": {
    "pagination": {
      "count": 125,
      "page_size": 50,
      "current_page": 1,
      "total_pages": 3
    }
  }
}
```

### Read Single Entity

**Endpoint:** `GET /api/v1/entity/{entity_type}/{id}`

**Parameters:**
- `fields` — limit response to specific fields
- `include` — expand related entities in-line

**Request:**
```
GET /api/v1/entity/assets/123?fields=code,sg_asset_type,project&include=project
Authorization: Bearer {token}
```

**Response:**
```json
{
  "data": {
    "type": "Asset",
    "id": "123",
    "attributes": {
      "code": "hero_character",
      "sg_asset_type": "Character"
    },
    "relationships": {
      "project": {
        "data": { "type": "Project", "id": "45" }
      }
    },
    "included": [
      {
        "type": "Project",
        "id": "45",
        "attributes": { "name": "Project_A" }
      }
    ]
  }
}
```

### Create Entity

**Endpoint:** `POST /api/v1/entity/{entity_type}`

**Request Body:**
```json
{
  "data": {
    "type": "Asset",
    "attributes": {
      "code": "new_asset",
      "sg_asset_type": "Prop",
      "sg_status_list": "wtg"
    },
    "relationships": {
      "project": {
        "data": { "type": "Project", "id": "45" }
      }
    }
  }
}
```

**Full Request:**
```
POST /api/v1/entity/assets
Content-Type: application/json
Authorization: Bearer {token}

{
  "data": {
    "type": "Asset",
    "attributes": {
      "code": "new_asset",
      "sg_asset_type": "Prop"
    }
  }
}
```

**Response (201 Created):**
```json
{
  "data": {
    "type": "Asset",
    "id": "456",
    "attributes": {
      "code": "new_asset",
      "sg_asset_type": "Prop"
    }
  }
}
```

### Update Entity

**Endpoint:** `PUT /api/v1/entity/{entity_type}/{id}`

**Request Body (partial update):**
```json
{
  "data": {
    "type": "Asset",
    "id": "123",
    "attributes": {
      "sg_status_list": "rdy",
      "description": "Updated description"
    }
  }
}
```

**Full Request:**
```
PUT /api/v1/entity/assets/123
Content-Type: application/json
Authorization: Bearer {token}

{
  "data": {
    "type": "Asset",
    "id": "123",
    "attributes": {
      "sg_status_list": "rdy"
    }
  }
}
```

**Response (200 OK):**
```json
{
  "data": {
    "type": "Asset",
    "id": "123",
    "attributes": {
      "sg_status_list": "rdy",
      "description": "Updated description"
    }
  }
}
```

### Delete Entity

**Endpoint:** `DELETE /api/v1/entity/{entity_type}/{id}`

**Request:**
```
DELETE /api/v1/entity/assets/123
Authorization: Bearer {token}
```

**Response (204 No Content):**
```
HTTP 204
```

### Advanced Search via POST

**Endpoint:** `POST /api/v1/entity/{entity_type}/_search`

Complex multi-condition search without query string length limits:

**Request Body:**
```json
{
  "filters": [
    {
      "field": "sg_status_list",
      "operator": "is",
      "values": ["ip", "wtg"]
    },
    {
      "field": "created_at",
      "operator": "greater_than",
      "values": ["2024-01-01"]
    }
  ],
  "filter_operator": "and",
  "fields": ["code", "sg_asset_type"],
  "sort": [
    {
      "field": "code",
      "direction": "asc"
    }
  ],
  "page": {
    "size": 50,
    "number": 1
  }
}
```

**Response:** Same format as GET list endpoint

---

## Relationships Endpoints

### Get Related Entities

**Endpoint:** `GET /api/v1/entity/{type}/{id}/relationships/{field}`

**Request:**
```
GET /api/v1/entity/assets/123/relationships/project
Authorization: Bearer {token}
```

**Response:**
```json
{
  "data": {
    "type": "Project",
    "id": "45"
  },
  "links": {
    "self": "/api/v1/entity/assets/123/relationships/project",
    "related": "/api/v1/entity/projects/45"
  }
}
```

### Add to Multi-Entity Relationship

**Endpoint:** `POST /api/v1/entity/{type}/{id}/relationships/{field}`

For many-to-many or one-to-many fields:

**Request Body:**
```json
{
  "data": [
    { "type": "Task", "id": "789" },
    { "type": "Task", "id": "790" }
  ]
}
```

**Full Request:**
```
POST /api/v1/entity/shots/200/relationships/tasks
Content-Type: application/json
Authorization: Bearer {token}

{
  "data": [
    { "type": "Task", "id": "789" }
  ]
}
```

### Replace Relationship

**Endpoint:** `PUT /api/v1/entity/{type}/{id}/relationships/{field}`

Replaces all related entities with provided list:

**Request Body:**
```json
{
  "data": [
    { "type": "Task", "id": "100" },
    { "type": "Task", "id": "101" }
  ]
}
```

**Response (200 OK):**
```json
{
  "data": [
    { "type": "Task", "id": "100" },
    { "type": "Task", "id": "101" }
  ]
}
```

### Remove from Relationship

**Endpoint:** `DELETE /api/v1/entity/{type}/{id}/relationships/{field}`

Remove specific related entities:

**Request Body:**
```json
{
  "data": [
    { "type": "Task", "id": "789" }
  ]
}
```

---

## Schema Endpoints

### Get All Fields for Entity Type

**Endpoint:** `GET /api/v1/schema/{entity_type}/fields`

**Request:**
```
GET /api/v1/schema/assets/fields
Authorization: Bearer {token}
```

**Response:**
```json
{
  "data": [
    {
      "id": "code",
      "name": "code",
      "type": "text",
      "data_type": {
        "type": "string"
      },
      "editable": true,
      "visible": true,
      "mandatory": false
    },
    {
      "id": "sg_asset_type",
      "name": "Asset Type",
      "type": "entity",
      "data_type": {
        "type": "entity",
        "entity_types": ["AssetType"]
      },
      "editable": true,
      "visible": true,
      "mandatory": true
    }
  ]
}
```

### Get Single Field Schema

**Endpoint:** `GET /api/v1/schema/{entity_type}/fields/{field_name}`

**Request:**
```
GET /api/v1/schema/assets/fields/sg_status_list
Authorization: Bearer {token}
```

**Response:**
```json
{
  "data": {
    "id": "sg_status_list",
    "name": "Status",
    "type": "status_list",
    "data_type": {
      "type": "status_list",
      "options": ["ip", "rdy", "apr", "pub"]
    },
    "editable": true,
    "visible": true
  }
}
```

### Create Custom Field

**Endpoint:** `POST /api/v1/schema/{entity_type}/fields`

**Request Body:**
```json
{
  "data": {
    "name": "Custom Field",
    "field_name": "sg_custom_field",
    "type": "text",
    "description": "A custom text field"
  }
}
```

### Update Field Schema

**Endpoint:** `PUT /api/v1/schema/{entity_type}/fields/{field_name}`

Modify field properties like visibility, editability:

**Request Body:**
```json
{
  "data": {
    "visible": false,
    "editable": false
  }
}
```

---

## Preferences and Project Hierarchy

### Get Project Preferences

**Endpoint:** `GET /api/v1/entity/projects/{id}/preferences`

Retrieve project-specific settings:

**Request:**
```
GET /api/v1/entity/projects/45/preferences
Authorization: Bearer {token}
```

**Response:**
```json
{
  "data": {
    "type": "Preference",
    "attributes": {
      "tag_filter_list": ["character", "prop"],
      "default_view_preferences": {...}
    }
  }
}
```

### Hierarchy Search

**Endpoint:** `GET /api/v1/hierarchy/search`

Navigate project structure (sequences → shots):

**Request:**
```
GET /api/v1/hierarchy/search?parent_id=45&entity_type=Sequence
Authorization: Bearer {token}
```

---

## Filter Syntax (JSONAPI Style)

### Simple Equality Filter

```
GET /api/v1/entity/assets?filter[sg_status_list]=ip
```

Equivalent to: `sg_status_list == "ip"`

### Operators in Filter

```
GET /api/v1/entity/assets?filter[created_at][greater_than]=2024-01-01
GET /api/v1/entity/assets?filter[code][contains]=hero
GET /api/v1/entity/assets?filter[sg_status_list][is_not]=del
```

**Available REST operators:**
- `is` (or omit for default)
- `is_not`
- `contains`
- `not_contains`
- `starts_with`
- `ends_with`
- `greater_than` / `gt`
- `less_than` / `lt`
- `greater_than_or_equal_to` / `gte`
- `less_than_or_equal_to` / `lte`
- `in` (multiple values)
- `between`

### Multiple Filters (AND by default)

```
GET /api/v1/entity/assets?filter[sg_status_list]=ip&filter[sg_asset_type]=Character
```

Equivalent to: `status == "ip" AND asset_type == "Character"`

### Array Notation for Multiple Values

```
GET /api/v1/entity/assets?filter[sg_status_list][in]=ip,wtg,rdy
```

### Complex Filters via POST Body

For complex nested logic use POST `_search`:

```json
{
  "filters": [
    {
      "field": "sg_status_list",
      "operator": "in",
      "values": ["ip", "wtg"]
    },
    {
      "logical_operator": "or",
      "filters": [
        {
          "field": "code",
          "operator": "contains",
          "values": ["hero"]
        },
        {
          "field": "code",
          "operator": "contains",
          "values": ["villain"]
        }
      ]
    }
  ],
  "filter_operator": "and"
}
```

### Python SDK ↔ REST Filter Operator Mapping

| Python SDK | REST API |
|---|---|
| `"is"` | `is` or no operator |
| `"is_not"` | `is_not` |
| `"contains"` | `contains` |
| `"not_contains"` | `not_contains` |
| `"starts_with"` | `starts_with` |
| `"greater_than"` | `greater_than` / `gt` |
| `"less_than"` | `less_than` / `lt` |
| `"in"` | `in` |
| `"type_is"` (for entity) | `is` |
| `"name_contains"` | `contains` on name field |

---

## Pagination

### Offset-Based Pagination

**Parameters:**
- `page[size]` — items per page (default 20, max 500)
- `page[number]` — page number (1-indexed, starts at 1)

**Request:**
```
GET /api/v1/entity/assets?page[size]=100&page[number]=2
```

**Response includes:**
```json
{
  "meta": {
    "pagination": {
      "count": 1250,
      "page_size": 100,
      "current_page": 2,
      "total_pages": 13
    }
  },
  "links": {
    "self": "...",
    "first": "...",
    "last": "...",
    "next": "...",
    "prev": "..."
  }
}
```

### Cursor-Based Pagination

For large datasets, use cursor pagination to avoid offset performance issues:

**Request:**
```
GET /api/v1/entity/assets?page[cursor]=abc123def456&page[size]=50
```

**Response:**
```json
{
  "links": {
    "next": "?page[cursor]=xyz789&page[size]=50"
  }
}
```

Follow `links.next` until it's null (end of results).

### Python SDK vs REST Pagination

| Aspect | Python SDK | REST |
|---|---|---|
| **Default** | `limit=0` (all results) | `page[size]=20` |
| **Method** | `limit` + `page` parameters | `page[size]` + `page[number]` |
| **Handling many results** | May timeout; split into pages manually | Use cursor pagination |
| **Iteration** | Single call or loop with page increment | Follow `links.next` |

---

## Sorting

### Single Field Sort

**Ascending:**
```
GET /api/v1/entity/assets?sort=code
```

**Descending (prefix with `-`):**
```
GET /api/v1/entity/assets?sort=-updated_at
```

### Multiple Sort Fields

```
GET /api/v1/entity/assets?sort=sg_asset_type,-created_at,code
```

Sorts by `sg_asset_type` (asc), then `created_at` (desc), then `code` (asc).

### Sortable Fields

Only fields marked as sortable in schema can be used in `sort` parameter. Typically includes:
- `code`, `name`
- `created_at`, `updated_at`
- Status fields
- Custom sortable fields

Attempting to sort on non-sortable field returns error.

---

## Fields Selection and Include (Sparse Fieldsets)

### Select Specific Fields

**Endpoint:**
```
GET /api/v1/entity/assets?fields=code,sg_asset_type,sg_status_list
```

Returns only requested attributes. Related entities still appear in `relationships` but not expanded.

### Include Related Entities

**Endpoint:**
```
GET /api/v1/entity/assets/123?include=project,tasks
```

Expands related entities in-line under `included` array:

**Response:**
```json
{
  "data": {
    "type": "Asset",
    "id": "123",
    "relationships": {
      "project": { "data": { "type": "Project", "id": "45" } },
      "tasks": { "data": [{ "type": "Task", "id": "100" }] }
    }
  },
  "included": [
    {
      "type": "Project",
      "id": "45",
      "attributes": { "name": "ProjectA" }
    },
    {
      "type": "Task",
      "id": "100",
      "attributes": { "content": "Model" }
    }
  ]
}
```

### Deep Include (Nested Related)

```
GET /api/v1/entity/shots?include=sequence,sequence.project
```

Includes sequence AND sequence's project relationship.

---

## Response Format (JSONAPI Specification)

### Standard Response Structure

```json
{
  "data": { ... },
  "included": [ ... ],
  "links": { ... },
  "meta": { ... }
}
```

### Entity Representation

```json
{
  "type": "Asset",
  "id": "123",
  "attributes": {
    "code": "hero_char",
    "sg_asset_type": "Character",
    "sg_status_list": "ip"
  },
  "relationships": {
    "project": {
      "data": { "type": "Project", "id": "45" },
      "links": {
        "self": "/api/v1/entity/assets/123/relationships/project",
        "related": "/api/v1/entity/projects/45"
      }
    },
    "tasks": {
      "data": [
        { "type": "Task", "id": "100" },
        { "type": "Task", "id": "101" }
      ]
    }
  },
  "links": {
    "self": "/api/v1/entity/assets/123"
  }
}
```

### Error Response Format

```json
{
  "errors": [
    {
      "status": 404,
      "title": "Not Found",
      "detail": "Asset with id 999 not found",
      "source": {
        "pointer": "/data/id"
      }
    }
  ]
}
```

### Common Error Fields

- `status` — HTTP status code
- `title` — Error title (e.g., "Bad Request")
- `detail` — Human-readable description
- `source.pointer` — JSONAPI pointer to problematic field
- `source.parameter` — Query parameter that caused error

---

## File Operations via REST

### Upload File

**Endpoint:** `POST /api/v1/entity/{type}/{id}/sg_{field_name}`

Multipart form data for file attachment fields:

**Request:**
```
POST /api/v1/entity/shots/200/sg_uploaded_movie
Content-Type: multipart/form-data

file=@/path/to/movie.mov
```

**Response:**
```json
{
  "data": {
    "type": "Attachment",
    "id": "500",
    "attributes": {
      "filename": "movie.mov",
      "url": "https://cdn.shotgunstudio.com/..."
    }
  }
}
```

### Download File

**Endpoint:** `GET /api/v1/entity/{type}/{id}/sg_{field_name}`

Returns redirect to signed S3 URL:

**Request:**
```
GET /api/v1/entity/shots/200/sg_uploaded_movie
Authorization: Bearer {token}
```

**Response (302 Found):**
```
Location: https://s3.amazonaws.com/shotgun/...
```

### Get Thumbnail URL

**Endpoint:** `GET /api/v1/entity/{type}/{id}/thumbnail`

Returns thumbnail image or redirect to CDN:

**Request:**
```
GET /api/v1/entity/assets/123/thumbnail
Authorization: Bearer {token}
```

**Response:**
```json
{
  "data": {
    "url": "https://cdn.shotgunstudio.com/thumb_123.jpg"
  }
}
```

---

## Webhooks

### Create Webhook

**Endpoint:** `POST /api/v1/entity/webhook`

Subscribe to entity change events:

**Request Body:**
```json
{
  "data": {
    "type": "Webhook",
    "attributes": {
      "url": "https://myapp.example.com/webhook",
      "target_entity_type": "Task",
      "events": ["create", "update", "delete"],
      "secret": "your_webhook_secret"
    }
  }
}
```

**Response (201 Created):**
```json
{
  "data": {
    "type": "Webhook",
    "id": "webhook_abc123",
    "attributes": {
      "url": "https://myapp.example.com/webhook",
      "status": "active",
      "created_at": "2024-01-15T10:00:00Z"
    }
  }
}
```

### Webhook Payload Structure

**POST to your webhook URL:**
```json
{
  "id": "event_123",
  "timestamp": "2024-01-15T10:30:00Z",
  "event_type": "entity.update",
  "entity": {
    "type": "Task",
    "id": "100"
  },
  "entity_update": {
    "attributes": {
      "content": "Updated task name"
    }
  },
  "user": {
    "type": "HumanUser",
    "id": "50"
  }
}
```

### Webhook Event Types

- `entity.create` — New entity created
- `entity.update` — Entity fields updated
- `entity.delete` — Entity deleted
- `entity.retirement` — Entity retired
- `entity.revival` — Retired entity restored

### Signature Verification

Verify authenticity using HMAC-SHA256:

```python
import hmac
import hashlib
import json

webhook_secret = "your_webhook_secret"
body = request.get_data()
signature = request.headers.get("X-SG-Webhook-Signature")

expected = hmac.new(
    webhook_secret.encode(),
    body,
    hashlib.sha256
).hexdigest()

if hmac.compare_digest(signature, expected):
    # Valid webhook
    payload = json.loads(body)
else:
    # Invalid signature
    return 401
```

---

## Activity Stream

### Get Entity Activity

**Endpoint:** `GET /api/v1/entity/{type}/{id}/activity`

Retrieve notes, updates, and change history:

**Request:**
```
GET /api/v1/entity/shots/200/activity?page[size]=50
Authorization: Bearer {token}
```

**Response:**
```json
{
  "data": [
    {
      "type": "Note",
      "id": "1000",
      "attributes": {
        "content": "Approved for publish",
        "created_at": "2024-01-15T14:30:00Z",
        "user": {
          "type": "HumanUser",
          "id": "50",
          "name": "Alice"
        }
      }
    },
    {
      "type": "ActivityStreamUpdate",
      "id": "1001",
      "attributes": {
        "field_name": "sg_status_list",
        "old_value": "rdy",
        "new_value": "pub",
        "created_at": "2024-01-15T15:00:00Z"
      }
    }
  ]
}
```

### Note Threading

Notes are linked via `parent` relationship for threading:

```json
{
  "type": "Note",
  "id": "1000",
  "relationships": {
    "parent": {
      "data": { "type": "Note", "id": "999" }
    }
  }
}
```

To retrieve reply chain, follow parent relationships backward.

---

## Rate Limiting

### Rate Limit Headers

All responses include rate limit info:

```
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 950
X-RateLimit-Reset: 1705344000
```

### 429 Too Many Requests

When rate limited, endpoint returns 429 with `Retry-After`:

**Response:**
```
HTTP 429 Too Many Requests
Retry-After: 60

{
  "errors": [{
    "status": 429,
    "title": "Too Many Requests",
    "detail": "Rate limit exceeded. Retry after 60 seconds."
  }]
}
```

### Rate Limit Tiers

- **Standard:** 1000 requests/hour per script/app
- **Burst:** 100 requests/minute
- **Concurrent:** 10 simultaneous requests max

### Best Practices

1. **Batch operations:** Use POST `_search` or single bulk calls instead of many small requests
2. **Implement backoff:** Exponential backoff on 429 responses
3. **Cache results:** Don't refetch unchanged data frequently
4. **Pagination:** Use cursor pagination for large datasets to avoid repeated full queries
5. **Monitor headers:** Watch `X-RateLimit-Remaining` to anticipate limits

---

## HTTP Status Codes and Error Responses

### 400 Bad Request

Invalid request parameters or malformed JSON:

```json
{
  "errors": [{
    "status": 400,
    "title": "Bad Request",
    "detail": "Invalid filter syntax: filter[unknown_field]"
  }]
}
```

**Causes:**
- Invalid filter syntax
- Malformed JSON body
- Unknown field names
- Type mismatch in data

### 401 Unauthorized

Missing or invalid authentication:

```json
{
  "errors": [{
    "status": 401,
    "title": "Unauthorized",
    "detail": "Bearer token expired or invalid"
  }]
}
```

**Causes:**
- Missing `Authorization` header
- Expired access token
- Invalid credentials
- Missing OAuth scope

### 403 Forbidden

User lacks permission for resource:

```json
{
  "errors": [{
    "status": 403,
    "title": "Forbidden",
    "detail": "User does not have permission to update Asset 123"
  }]
}
```

**Causes:**
- Insufficient project permissions
- Read-only field update attempt
- Restricted entity type access

### 404 Not Found

Resource doesn't exist:

```json
{
  "errors": [{
    "status": 404,
    "title": "Not Found",
    "detail": "Asset with id 999 does not exist"
  }]
}
```

### 409 Conflict

Update conflict (concurrent modification):

```json
{
  "errors": [{
    "status": 409,
    "title": "Conflict",
    "detail": "Entity was modified by another user. Please refresh and try again."
  }]
}
```

### 422 Unprocessable Entity

Validation error (required field missing, invalid value):

```json
{
  "errors": [{
    "status": 422,
    "title": "Unprocessable Entity",
    "detail": "code is required",
    "source": { "pointer": "/data/attributes/code" }
  }]
}
```

### 429 Too Many Requests

Rate limit exceeded (see Rate Limiting section).

### 500 Internal Server Error

Unexpected server error:

```json
{
  "errors": [{
    "status": 500,
    "title": "Internal Server Error",
    "detail": "An unexpected error occurred. Contact support with request ID xyz."
  }]
}
```

---

## Complete Comparison: Python SDK ↔ REST API

### Authentication

| Task | Python SDK | REST API |
|---|---|---|
| **Authenticate** | `sg = Shotgun(url, script_name, api_key)` | `POST /api/v1/auth/access_token` → `Authorization: Bearer {token}` |
| **Session lifecycle** | Single session object | Stateless HTTP; token per request |
| **Re-authentication** | Automatic on expiration | Manual token refresh via refresh_token |

### CRUD Operations

| Task | Python SDK | REST API |
|---|---|---|
| **List entities** | `sg.find("Asset", [...], ["code"])` | `GET /api/v1/entity/assets?fields=code` |
| **Get single** | `sg.find_one("Asset", [...])` or `sg.get_entity("Asset", id)` | `GET /api/v1/entity/assets/{id}` |
| **Create** | `sg.create("Asset", {data})` | `POST /api/v1/entity/assets` + JSON body |
| **Update** | `sg.update("Asset", id, {data})` | `PUT /api/v1/entity/assets/{id}` + JSON body |
| **Delete** | `sg.delete("Asset", id)` | `DELETE /api/v1/entity/assets/{id}` |

### Filtering

| Task | Python SDK | REST API |
|---|---|---|
| **Simple filter** | `[["code", "is", "hero"]]` | `?filter[code]=hero` |
| **Operator filter** | `[["created_at", "greater_than", "2024-01-01"]]` | `?filter[created_at][greater_than]=2024-01-01` |
| **Multiple filters (AND)** | `[[...], [...]]` list | `?filter[field1]=val1&filter[field2]=val2` |
| **Complex logic (OR)** | Custom code or sg.batch() | `POST _search` with `filter_operator` |

### Pagination

| Task | Python SDK | REST API |
|---|---|---|
| **Get all** | `sg.find(..., limit=0)` | Multiple pages via `page[size]` + `page[number]` |
| **Paginate** | `limit=100, page=2` params | `?page[size]=100&page[number]=2` |
| **Iteration** | Loop incrementing `page` | Follow `links.next` in response |

### Sorting

| Task | Python SDK | REST API |
|---|---|---|
| **Sort ascending** | `order=[{'field_name': 'code', ...}]` | `?sort=code` |
| **Sort descending** | `order=[{'field_name': 'code', 'direction': 'desc'}]` | `?sort=-code` |
| **Multiple sorts** | List multiple order dicts | `?sort=type,-created_at,code` |

### Field Selection

| Task | Python SDK | REST API |
|---|---|---|
| **All fields** | `fields=None` or omit | All attributes returned by default |
| **Specific fields** | `fields=["code", "sg_status_list"]` | `?fields=code,sg_status_list` |
| **Expand relations** | `fields=["code", "project"]` (returns link only) | `?include=project` (expands in-line) |

### Response Handling

| Aspect | Python SDK | REST API |
|---|---|---|
| **Entity refs** | `{"type": "Asset", "id": 123}` dict | Same in JSON, but relationship links point to `/api/v1/entity/...` |
| **List return** | Python list of dicts | JSONAPI wrapper: `{"data": [...], "links": {...}}` |
| **Attributes access** | Direct dict key: `asset["code"]` | Same: `data[0]["attributes"]["code"]` |
| **Error handling** | Raises `ShotgunError` exceptions | Returns JSON error array with status codes |

### Relationships

| Task | Python SDK | REST API |
|---|---|---|
| **Get related** | `sg.find("Task", [["entity", "is", asset]], ...)` | `GET /api/v1/entity/assets/123/relationships/tasks` |
| **Add relation** | `sg.update(..., {"field": [...]})` | `POST /api/v1/entity/.../relationships/field` |
| **Replace relation** | `sg.update(..., {"field": [...]})` | `PUT /api/v1/entity/.../relationships/field` |

---

## Anti-Patterns (NEVER Do These)

### 1. Mix REST and Python SDK syntax in same code

**WRONG:**
```python
# This makes no sense
filters = "filter[code]=hero&filter[status]=ip"
sg.find("Asset", filters, ["code"])
```

**RIGHT:**
```python
# Use Python SDK syntax
sg.find("Asset",
    [["code", "is", "hero"], ["sg_status_list", "is", "ip"]],
    ["code"])
```

### 2. Send Bearer token to shotgun_api3

**WRONG:**
```python
sg = Shotgun(url, auth_token="Bearer abc123...")
```

**RIGHT:**
```python
# shotgun_api3 uses script credentials
sg = Shotgun(url, script_name="my_script", api_key="api_key_value")
```

### 3. Use REST pagination params in sg.find()

**WRONG:**
```python
# page[size] is REST syntax, not Python SDK
sg.find("Asset", [...], page_size=50, page_number=2)
```

**RIGHT:**
```python
# Python SDK uses limit and page
sg.find("Asset", [...], limit=50, page=2)
```

### 4. Assume response formats are identical

**WRONG:**
```python
# REST returns JSONAPI wrapper
response = requests.get(url, headers=headers)
assets = response.json()["data"]  # Must unwrap

# Then try same format in Python SDK
results = sg.find("Asset", ...)
asset = results[0]  # Direct list, not wrapped
```

### 5. Pass REST filter syntax to sg.find()

**WRONG:**
```python
# This is REST URL query syntax
sg.find("Asset", "filter[code]=hero&filter[status]=ip", ...)
```

**RIGHT:**
```python
# This is Python SDK filter list syntax
sg.find("Asset", [["code", "is", "hero"], ["sg_status_list", "is", "ip"]], ...)
```

### 6. Mix JSONAPI relationships with Python SDK entity refs

**WRONG:**
```python
# JSONAPI style
project = {"type": "Project", "id": 45}
sg.update("Asset", 123, {"project": project})
# This might work but returns JSONAPI response, not Python dict
```

**RIGHT:**
```python
# Python SDK takes entity dict same way
project = {"type": "Project", "id": 45}
result = sg.update("Asset", 123, {"project": project})
# Returns Python dict directly
```

### 7. Treat REST limit/page like unlimited querying

**WRONG:**
```python
# REST defaults to 20 items; assume you get everything
response = requests.get("/api/v1/entity/assets", headers=headers)
assets = response.json()["data"]
# Only got first 20, missing rest
```

**RIGHT:**
```python
# Iterate through pages or use cursor
page = 1
all_assets = []
while True:
    response = requests.get(
        f"/api/v1/entity/assets?page[size]=500&page[number]={page}",
        headers=headers
    )
    data = response.json()["data"]
    all_assets.extend(data)
    if not response.json()["links"].get("next"):
        break
    page += 1
```

### 8. Forget Authorization header in REST calls

**WRONG:**
```python
response = requests.get("/api/v1/entity/assets")
# Returns 401 Unauthorized
```

**RIGHT:**
```python
headers = {"Authorization": f"Bearer {access_token}"}
response = requests.get("/api/v1/entity/assets", headers=headers)
```

### 9. Use entity field names without knowing context

**WRONG:**
```python
# "status_list" is Python SDK; REST uses the actual field name
sg.find("Asset", [["status_list", "is", "ip"]], ...)  # Fails in Python SDK

# REST filter uses actual ShotGrid field name
GET /api/v1/entity/assets?filter[sg_status_list]=ip  # Correct
```

### 10. Cache tokens indefinitely

**WRONG:**
```python
# Token expires after 3600s; don't cache forever
cached_token = "token_from_yesterday"
headers = {"Authorization": f"Bearer {cached_token}"}
# Returns 401
```

**RIGHT:**
```python
# Check expiration and refresh if needed
if token_expired():
    token = refresh_token(refresh_token_value)
headers = {"Authorization": f"Bearer {token}"}
```

---

## Key Takeaways for fpt-mcp Users

1. **fpt-mcp uses Python SDK only.** All `sg_*` tools call `shotgun_api3` methods, not REST endpoints.

2. **Know the differences:**
   - Python SDK: Script credentials, list-based filters, native pagination with limit/page
   - REST API: OAuth bearer tokens, JSONAPI format, URL-based pagination

3. **Use this doc as reference** when understanding REST concepts that might apply to your SDK code logic.

4. **Don't mix them.** Trying to use REST syntax in Python SDK or vice versa will fail.

5. **For web integrations**, REST API is the standard. For pipeline scripts and MCP tools, Python SDK (as used by fpt-mcp) is correct.
