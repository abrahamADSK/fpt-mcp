# Toolkit (sgtk) — Reference for RAG

> Source: developers.shotgridsoftware.com/tk-core, github.com/shotgunsoftware/tk-config-default2
> This document is indexed by the fpt-mcp RAG engine. Keep it accurate.

## Overview

Toolkit (sgtk) is a pipeline framework for ShotGrid. It resolves file paths
from templates and manages project configurations. fpt-mcp uses Toolkit
conventions WITHOUT requiring sgtk bootstrap — it reads config files directly.

## PipelineConfiguration entity

The PipelineConfiguration entity in ShotGrid stores the project's config location:
- `code`: Config name (typically "Primary")
- `mac_path` / `linux_path` / `windows_path`: Local path to config directory
- `descriptor`: Dict describing a distributed config source
- `project`: Link to the Project entity

### Discovery from ShotGrid

```python
configs = sg.find("PipelineConfiguration",
    [["project", "is", {"type": "Project", "id": PROJECT_ID}],
     ["code", "is", "Primary"]],
    ["code", "mac_path", "linux_path", "windows_path", "descriptor"]
)
# Returns: [{"mac_path": "/Users/Shared/FPT_MCP", ...}]
```

### Descriptor types

- `{"type": "app_store", "name": "tk-config-default2", "version": "v1.2.3"}` — downloaded from SG App Store
- `{"type": "git", "path": "https://github.com/org/tk-config-custom.git", "version": "v1.0"}` — cloned from git
- `{"type": "dev", "path": "/local/path/to/config"}` — local development

### Bundle cache locations (for distributed configs)

- macOS: `~/Library/Caches/Shotgun/<site_name>/bundle_cache/`
- Linux: `~/.shotgun/bundle_cache/`
- Windows: `%APPDATA%\Shotgun\bundle_cache\`

## Config directory structure

```
config_path/
├── config/
│   ├── core/
│   │   ├── roots.yml          ← storage roots (project_root)
│   │   ├── templates.yml      ← path templates
│   │   └── schema/
│   ├── env/                   ← environment configs
│   └── hooks/                 ← custom hooks
├── install/                   ← cached apps/engines/frameworks
└── tank/                      ← tank command
```

## roots.yml — Storage roots

Defines where project files live on disk:

```yaml
primary:
  mac_path: /Users/Shared/FPT_MCP
  linux_path: /mnt/projects/FPT_MCP
  windows_path: P:\FPT_MCP
  default: true
  shotgun_storage_id: 432
```

The `default: true` root is the primary storage. Its platform path becomes `project_root`.

## templates.yml — Path templates (CRITICAL for RAG)

### Template token syntax (case-sensitive!)

CORRECT tokens (PascalCase for entity fields):
- `{Shot}` — Shot code (e.g. "SH010")
- `{Asset}` — Asset code (e.g. "hero_robot")
- `{Sequence}` — Sequence code (e.g. "SEQ01")
- `{Step}` — Pipeline step short name (e.g. "model", "anim")
- `{sg_asset_type}` — Asset type (e.g. "Character", "Prop")
- `{name}` — Publish/work name (e.g. "main")
- `{version}` — Version number (format: 03d → "001", "002")
- `{maya_extension}` — File extension (default: "ma")
- `{SEQ}` — Frame number for sequences (format: 04d → "0001")

INCORRECT tokens (common hallucinations):
- `{shot_name}` — WRONG, use `{Shot}`
- `{asset_name}` — WRONG, use `{Asset}`
- `{project_name}` — WRONG, not a template token
- `{step}` — WRONG, use `{Step}` (PascalCase)
- `{frame}` — WRONG, use `{SEQ}` for image sequences

### Aliases (path shortcuts)

```yaml
asset_root: assets/{sg_asset_type}/{Asset}/{Step}
shot_root: sequences/{Sequence}/{Shot}/{Step}
sequence_root: sequences/{Sequence}
```

Usage in templates: `@asset_root/publish/maya/{name}.v{version}.{maya_extension}`

### Standard tk-config-default2 templates

#### Maya publishes
- `maya_asset_publish`: `@asset_root/publish/maya/{name}.v{version}.{maya_extension}`
- `maya_shot_publish`: `@shot_root/publish/maya/{name}.v{version}.{maya_extension}`
- `maya_asset_work`: `@asset_root/work/maya/{name}.v{version}.{maya_extension}`
- `maya_shot_work`: `@shot_root/work/maya/{name}.v{version}.{maya_extension}`

#### Alembic
- `asset_alembic_cache`: `@asset_root/publish/caches/{name}.v{version}.abc`

#### Nuke
- `nuke_asset_publish`: `@asset_root/publish/nuke/{name}.v{version}.nk`
- `nuke_shot_publish`: `@shot_root/publish/nuke/{name}.v{version}.nk`

### Derived templates (Vision3D pipeline — added by fpt-mcp)

These are injected by `tk_config.py` for types not in tk-config-default2:

- `usd_asset_publish`: `@asset_root/publish/usd/{name}.v{version}.usd`
- `fbx_asset_publish`: `@asset_root/publish/fbx/{name}.v{version}.fbx`
- `texture_asset_publish`: `@asset_root/publish/textures/{name}.v{version}.png`
- `review_asset_mov`: `@asset_root/review/{Asset}_{name}_v{version}.mov`
- `usd_shot_publish`: `@shot_root/publish/usd/{name}.v{version}.usd`
- `fbx_shot_publish`: `@shot_root/publish/fbx/{name}.v{version}.fbx`
- `camera_shot_fbx_publish`: `@shot_root/publish/camera/{name}.v{version}.fbx`
- `exr_shot_render`: `@shot_root/publish/renders/{name}/v{version}/{name}.v{version}.{SEQ}.exr`
- `review_shot_mov`: `@shot_root/review/{Shot}_{name}_v{version}.mov`

### PUBLISH_TYPE_MAP — type to template mapping

| Publish Type | Asset Template | Shot Template |
|---|---|---|
| Maya Scene | maya_asset_publish | maya_shot_publish |
| USD Scene | usd_asset_publish | usd_shot_publish |
| FBX Model | fbx_asset_publish | fbx_shot_publish |
| Texture | texture_asset_publish | — |
| Alembic Cache | asset_alembic_cache | — |
| Camera FBX | — | camera_shot_fbx_publish |
| EXR Render | — | exr_shot_render |
| Review MOV | review_asset_mov | review_shot_mov |

## Path resolution example

Given: Asset "hero_robot", type "Character", step "model", version 3

1. Template: `maya_asset_publish` → `@asset_root/publish/maya/{name}.v{version}.{maya_extension}`
2. Expand alias: `assets/{sg_asset_type}/{Asset}/{Step}/publish/maya/{name}.v{version}.{maya_extension}`
3. Apply fields: `assets/Character/hero_robot/model/publish/maya/main.v003.ma`
4. Prepend project_root: `/Users/Shared/FPT_MCP/assets/Character/hero_robot/model/publish/maya/main.v003.ma`

## Version auto-increment

`next_version()` scans the publish directory for existing versioned files:
- Pattern: `.v{NNN}.` in filenames
- Returns `max_version + 1`, or 1 if no files exist

## PublishedFile vs Version

- `PublishedFile` — reusable pipeline file (Maya scene, USD, texture, etc.)
  - Has `path`, `published_file_type`, `version_number`
  - Loaded by tk-multi-loader2 in Maya, Nuke, Flame
- `Version` — review/preview entity
  - Has `sg_uploaded_movie` (QuickTime/MOV for review)
  - Reviewed in RV, ShotGrid web, screening room

## Anti-patterns (NEVER do these)

- Using lowercase `{shot}` instead of `{Shot}` — templates are case-sensitive
- Hardcoding paths without reading roots.yml — paths differ per platform
- Modifying the user's templates.yml or roots.yml from the MCP server
- Assuming all projects have a PipelineConfiguration — basic projects don't
- Using sgtk.bootstrap when you can read YAML directly — bootstrap is heavy
