# ShotGrid REST API — Reference for RAG

> Source: developers.shotgridsoftware.com/rest-api
> Complementary to shotgun_api3. fpt-mcp uses the Python SDK, not REST directly.
> This doc exists to prevent LLM confusion between the two APIs.

## When to use REST vs Python SDK

| Aspect | Python SDK (shotgun_api3) | REST API |
|---|---|---|
| **fpt-mcp uses** | ✅ Yes — all sg_* tools | ❌ No — reference only |
| **Auth** | Script credentials | OAuth 2.0 bearer tokens |
| **Filters** | Python list syntax | JSONAPI array/hash syntax |
| **Pagination** | `limit` + `page` params | Cursor-based pagination |
| **Language** | Python only | Any language (HTTP) |
| **Use case** | Pipeline scripts, MCP tools | Web apps, integrations |

## REST filter syntax vs Python SDK

### Python SDK (what fpt-mcp uses)
```python
sg.find("Asset",
    [["sg_status_list", "is", "ip"],
     ["sg_asset_type", "is", "Character"]],
    ["code", "sg_asset_type"]
)
```

### REST API equivalent (NOT used by fpt-mcp)
```
GET /api/v1/entity/assets?filter[sg_status_list]=ip&filter[sg_asset_type]=Character&fields=code,sg_asset_type
```

### Key differences that cause confusion

1. **Entity references**: Python SDK uses `{"type": "Asset", "id": 123}`. REST uses `{"type": "Asset", "id": 123}` in JSON body but entity links in URL are just IDs.

2. **Pagination**: Python SDK returns all results with `limit=0`. REST uses cursor-based pagination with `page[size]` and `page[number]`.

3. **Auth**: Python SDK authenticates once in constructor. REST requires `Authorization: Bearer <token>` header on every request.

4. **Operators**: Python SDK uses string operators `"is"`, `"contains"`. REST uses them differently in filter syntax.

## REST API endpoints (reference only)

- `GET /api/v1/entity/{entity_type}` — list entities
- `GET /api/v1/entity/{entity_type}/{id}` — get single entity
- `POST /api/v1/entity/{entity_type}` — create entity
- `PUT /api/v1/entity/{entity_type}/{id}` — update entity
- `DELETE /api/v1/entity/{entity_type}/{id}` — delete entity
- `GET /api/v1/entity/{entity_type}/{id}/relationships/{field}` — get related entities
- `POST /api/v1/auth/access_token` — get OAuth 2.0 token

## Anti-patterns (NEVER do these)

- Mixing REST URL syntax with Python SDK filter format
- Using REST pagination params in sg.find() calls
- Sending Bearer token to shotgun_api3 (it uses script credentials)
- Assuming REST and Python SDK return identical response formats
