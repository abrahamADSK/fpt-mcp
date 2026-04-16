# Toolkit (sgtk) — Complete Reference for RAG

> Source: developers.shotgridsoftware.com/tk-core, github.com/shotgunsoftware/tk-config-default2
> This document is indexed by the fpt-mcp RAG engine. Keep it accurate.

## Overview

Toolkit (sgtk) is a pipeline framework for ShotGrid. It resolves file paths
from templates, manages project configurations, and provides apps/engines/hooks
for DCCs (Maya, Nuke, Flame, Houdini, etc.). fpt-mcp uses Toolkit conventions
WITHOUT requiring sgtk bootstrap — it reads config files (YAML) directly.

## PipelineConfiguration entity

The PipelineConfiguration entity in ShotGrid stores the project's config location:
- `code`: Config name (typically "Primary")
- `mac_path` / `linux_path` / `windows_path`: Local path to config directory
- `descriptor`: Dict describing a distributed config source
- `project`: Link to the Project entity
- `plugin_ids`: Comma-separated engine identifiers for centralized configs
- `uploaded_config`: Zipped config for uploaded distributed setups

### Discovery from ShotGrid

```python
configs = sg.find("PipelineConfiguration",
    [["project", "is", {"type": "Project", "id": PROJECT_ID}],
     ["code", "is", "Primary"]],
    ["code", "mac_path", "linux_path", "windows_path", "descriptor"]
)
# Returns: [{"mac_path": "/Users/Shared/FPT_MCP", ...}]
```

If no PipelineConfiguration exists, the project uses Basic Setup (no Toolkit).

### Advanced Setup vs Basic Setup

- **Advanced Setup**: Project has a PipelineConfiguration entity. Full template resolution, apps, hooks.
- **Basic Setup**: No PipelineConfiguration. Integrations (like ShotGrid Desktop) use a site-wide default config.

## Descriptor types

Descriptors define where a Toolkit configuration or bundle lives:

### app_store descriptor
```yaml
{"type": "app_store", "name": "tk-config-default2", "version": "v1.2.16"}
```
Downloaded from the ShotGrid App Store. Cached in bundle_cache.

### git descriptor
```yaml
{"type": "git", "path": "https://github.com/org/tk-config-custom.git", "version": "v1.0.0"}
```
Cloned from a git repository. Supports tags, branches, commits.

### git_branch descriptor
```yaml
{"type": "git_branch", "path": "https://github.com/org/tk-config-custom.git", "branch": "main", "version": "abc1234"}
```
Tracks a specific branch and commit. Used for continuous integration workflows.

### dev descriptor
```yaml
{"type": "dev", "path": "/local/path/to/config"}
```
Points to a local directory. Used for development and testing. No caching.

### path descriptor
```yaml
{"type": "path", "path": "/shared/configs/tk-config-project"}
```
Similar to dev but for shared network locations. Not version-controlled.

### manual descriptor
```yaml
{"type": "manual", "name": "tk-maya", "version": "v0.9.0"}
```
Manually managed bundles. Rarely used.

### shotgun descriptor
```yaml
{"type": "shotgun", "entity_type": "PipelineConfiguration", "id": 123, "field": "uploaded_config", "version": 456}
```
Uploaded directly to ShotGrid. Config stored as attachment on the PipelineConfiguration entity.

## Bundle cache locations

For distributed configs (app_store, git, shotgun), bundles are cached locally:

- macOS: `~/Library/Caches/Shotgun/<site_name>/bundle_cache/`
- Linux: `~/.shotgun/bundle_cache/`
- Windows: `%APPDATA%\Shotgun\bundle_cache\`

Inside bundle_cache:
```
bundle_cache/
├── app_store/
│   ├── tk-config-default2/
│   │   └── v1.2.16/
│   ├── tk-maya/
│   │   └── v0.12.1/
│   ├── tk-multi-publish2/
│   │   └── v2.6.7/
│   └── ...
├── git/
│   └── tk-config-custom.git/
│       └── v1.0.0/
└── gitbranch/
    └── ...
```

## Config directory structure

```
config_path/
├── config/
│   ├── core/
│   │   ├── roots.yml          ← storage roots (project_root)
│   │   ├── templates.yml      ← path templates
│   │   ├── schema/            ← filesystem schema (folder structure)
│   │   │   ├── project.yml
│   │   │   ├── asset.yml
│   │   │   ├── shot.yml
│   │   │   ├── sequence.yml
│   │   │   └── step.yml
│   │   └── shotgun.yml        ← schema dispatch rules
│   ├── env/                   ← environment configs
│   │   ├── project.yml
│   │   ├── asset.yml
│   │   ├── asset_step.yml
│   │   ├── shot.yml
│   │   ├── shot_step.yml
│   │   └── includes/
│   │       ├── settings/
│   │       └── frameworks/
│   └── hooks/                 ← custom hooks
│       ├── pick_environment.py
│       └── ...
├── install/                   ← cached apps/engines/frameworks
│   ├── engines/
│   ├── apps/
│   └── frameworks/
└── tank/                      ← tank command
```

## roots.yml — Storage roots

Defines where project files live on disk. Each root maps to a local storage in ShotGrid:

```yaml
primary:
  mac_path: /Users/Shared/FPT_MCP
  linux_path: /mnt/projects/FPT_MCP
  windows_path: "P:\\FPT_MCP"
  default: true
  shotgun_storage_id: 432
```

Multiple roots supported:
```yaml
primary:
  mac_path: /mnt/fast/projects
  default: true
secondary:
  mac_path: /mnt/archive/projects
  default: false
textures:
  mac_path: /mnt/textures
  default: false
```

The `default: true` root is the primary storage. Its platform path becomes `project_root` in templates.

## templates.yml — Path templates (CRITICAL for RAG)

### Template definition syntax

Templates are defined in YAML with a `definition` and optional `root_name`:

```yaml
keys:
  Shot:
    type: str
  Asset:
    type: str
  Step:
    type: str
    shotgun_entity_type: Step
    shotgun_field_name: short_name
  sg_asset_type:
    type: str
  name:
    type: str
    default: main
  version:
    type: int
    format_spec: "03"
  maya_extension:
    type: str
    default: ma
    choices:
      ma: Maya ASCII
      mb: Maya Binary
  nuke_extension:
    type: str
    default: nk
  houdini_extension:
    type: str
    default: hip
    choices:
      hip: Houdini
      hipnc: Houdini Non-Commercial
      hiplc: Houdini Learning
  SEQ:
    type: sequence
    format_spec: "04"
  eye:
    type: str
    choices:
      "%V": Stereo eye variable
  Sequence:
    type: str
  channel:
    type: str
  width:
    type: int
  height:
    type: int
  output:
    type: str

paths:
  # ... template definitions ...
```

### Template token syntax (case-sensitive!)

CORRECT tokens (PascalCase for entity fields):
- `{Shot}` — Shot code (e.g. "SH010")
- `{Asset}` — Asset code (e.g. "hero_robot")
- `{Sequence}` — Sequence code (e.g. "SEQ01")
- `{Step}` — Pipeline step short name (e.g. "model", "anim", "comp")
- `{sg_asset_type}` — Asset type (e.g. "Character", "Prop", "Environment")
- `{name}` — Publish/work name (e.g. "main", "sculpt", "lookdev")
- `{version}` — Version number (format: 03d → "001", "002", "042")
- `{maya_extension}` — Maya file extension (default: "ma", choices: "ma", "mb")
- `{nuke_extension}` — Nuke file extension (default: "nk")
- `{houdini_extension}` — Houdini file extension (default: "hip")
- `{SEQ}` — Frame number for sequences (format: 04d → "0001", "0100")
- `{eye}` — Stereo eye (for multi-view renders)
- `{channel}` — Render channel/AOV name (e.g. "beauty", "diffuse", "specular")
- `{output}` — Output name for Nuke write nodes
- `{width}` — Image width in pixels
- `{height}` — Image height in pixels

### INCORRECT tokens (common hallucinations)

- `{shot_name}` — WRONG, use `{Shot}`
- `{asset_name}` — WRONG, use `{Asset}`
- `{project_name}` — WRONG, not a template token
- `{step}` — WRONG, use `{Step}` (PascalCase)
- `{frame}` — WRONG, use `{SEQ}` for image sequences
- `{ext}` — WRONG, use `{maya_extension}`, `{nuke_extension}`, etc.
- `{task}` — WRONG, not a standard token. Step ≠ Task
- `{sequence}` — WRONG, use `{Sequence}` (PascalCase)
- `{v}` or `{ver}` — WRONG, use `{version}`
- `{shot_code}` — WRONG, use `{Shot}`
- `{asset_code}` — WRONG, use `{Asset}`
- `{pipeline_step}` — WRONG, use `{Step}`

### Aliases (path shortcuts)

```yaml
asset_root: assets/{sg_asset_type}/{Asset}/{Step}
shot_root: sequences/{Sequence}/{Shot}/{Step}
sequence_root: sequences/{Sequence}
```

Usage in templates: `@asset_root/publish/maya/{name}.v{version}.{maya_extension}`

Aliases expand at resolution time. `@asset_root` becomes `assets/{sg_asset_type}/{Asset}/{Step}`.

## Standard tk-config-default2 templates — COMPLETE LIST

### Asset work templates

- `maya_asset_work`: `@asset_root/work/maya/{name}.v{version}.{maya_extension}`
- `nuke_asset_work`: `@asset_root/work/nuke/{name}.v{version}.{nuke_extension}`
- `houdini_asset_work`: `@asset_root/work/houdini/{name}.v{version}.{houdini_extension}`
- `houdini_asset_work_alembic_cache`: `@asset_root/work/houdini/{name}/v{version}/abc/{node}.abc`
- `photoshop_asset_work`: `@asset_root/work/photoshop/{name}.v{version}.psd`
- `aftereffects_asset_work`: `@asset_root/work/afx/{name}.v{version}.aep`
- `3dsmax_asset_work`: `@asset_root/work/3dsmax/{name}.v{version}.max`
- `motionbuilder_asset_work`: `@asset_root/work/mobu/{name}.v{version}.fbx`
- `alias_asset_work`: `@asset_root/work/alias/{name}.v{version}.wire`
- `vred_asset_work`: `@asset_root/work/vred/{name}.v{version}.vpb`

### Asset publish templates

- `maya_asset_publish`: `@asset_root/publish/maya/{name}.v{version}.{maya_extension}`
- `nuke_asset_publish`: `@asset_root/publish/nuke/{name}.v{version}.{nuke_extension}`
- `houdini_asset_publish`: `@asset_root/publish/houdini/{name}.v{version}.{houdini_extension}`
- `asset_alembic_cache`: `@asset_root/publish/houdini/{name}/v{version}/abc/{node}.abc`
- `photoshop_asset_publish`: `@asset_root/publish/photoshop/{name}.v{version}.psd`
- `aftereffects_asset_publish`: `@asset_root/publish/afx/{name}.v{version}.aep`
- `max_asset_publish`: `@asset_root/publish/3dsmax/{name}.v{version}.max`
- `mobu_asset_publish`: `@asset_root/publish/mobu/{name}.v{version}.fbx`
- `alias_asset_publish`: `@asset_root/publish/alias/{name}.v{version}.wire`
- `vred_asset_publish`: `@asset_root/publish/vred/{name}.v{version}.vpb`

### Asset snapshot/backup templates

- `maya_asset_snapshot`: `@asset_root/work/maya/snapshots/{name}.v{version}.{timestamp}.{maya_extension}`
- `nuke_asset_snapshot`: `@asset_root/work/nuke/snapshots/{name}.v{version}.{timestamp}.{nuke_extension}`
- `houdini_asset_snapshot`: `@asset_root/work/houdini/snapshots/{name}.v{version}.{timestamp}.{houdini_extension}`
- `photoshop_asset_snapshot`: `@asset_root/work/photoshop/snapshots/{name}.v{version}.{timestamp}.psd`

### Asset render/image templates

- `asset_alembic_cache`: `@asset_root/publish/caches/{name}.v{version}.abc`
- `photoshop_asset_jpg_publish`: `@asset_root/publish/photoshop/{name}.v{version}.jpg`

### Shot work templates

- `maya_shot_work`: `@shot_root/work/maya/{name}.v{version}.{maya_extension}`
- `nuke_shot_work`: `@shot_root/work/nuke/{name}.v{version}.{nuke_extension}`
- `houdini_shot_work`: `@shot_root/work/houdini/{name}.v{version}.{houdini_extension}`
- `houdini_shot_work_alembic_cache`: `@shot_root/work/houdini/{name}/v{version}/abc/{node}.abc`
- `photoshop_shot_work`: `@shot_root/work/photoshop/{name}.v{version}.psd`
- `aftereffects_shot_work`: `@shot_root/work/afx/{name}.v{version}.aep`
- `3dsmax_shot_work`: `@shot_root/work/3dsmax/{name}.v{version}.max`
- `motionbuilder_shot_work`: `@shot_root/work/mobu/{name}.v{version}.fbx`
- `flame_shot_work`: `@shot_root/work/flame/{name}.v{version}.clip`

### Shot publish templates

- `maya_shot_publish`: `@shot_root/publish/maya/{name}.v{version}.{maya_extension}`
- `nuke_shot_publish`: `@shot_root/publish/nuke/{name}.v{version}.{nuke_extension}`
- `houdini_shot_publish`: `@shot_root/publish/houdini/{name}.v{version}.{houdini_extension}`
- `shot_alembic_cache`: `@shot_root/publish/houdini/{name}/v{version}/abc/{node}.abc`
- `photoshop_shot_publish`: `@shot_root/publish/photoshop/{name}.v{version}.psd`
- `aftereffects_shot_publish`: `@shot_root/publish/afx/{name}.v{version}.aep`
- `max_shot_publish`: `@shot_root/publish/3dsmax/{name}.v{version}.max`
- `mobu_shot_publish`: `@shot_root/publish/mobu/{name}.v{version}.fbx`
- `flame_shot_render_exr`: `@shot_root/publish/flame/{name}.v{version}.clip`

### Shot snapshot templates

- `maya_shot_snapshot`: `@shot_root/work/maya/snapshots/{name}.v{version}.{timestamp}.{maya_extension}`
- `nuke_shot_snapshot`: `@shot_root/work/nuke/snapshots/{name}.v{version}.{timestamp}.{nuke_extension}`
- `houdini_shot_snapshot`: `@shot_root/work/houdini/snapshots/{name}.v{version}.{timestamp}.{houdini_extension}`

### Shot render/image templates

- `nuke_shot_render_mono_dpx`: `@shot_root/work/images/{name}/v{version}/{width}x{height}/{Shot}.{SEQ}.dpx`
- `nuke_shot_render_pub_mono_dpx`: `@shot_root/publish/elements/{name}/v{version}/{width}x{height}/{Shot}.{SEQ}.dpx`
- `nuke_shot_render_mono_exr`: `@shot_root/work/images/{name}/v{version}/{width}x{height}/{Shot}.{SEQ}.exr`
- `nuke_shot_render_pub_mono_exr`: `@shot_root/publish/elements/{name}/v{version}/{width}x{height}/{Shot}.{SEQ}.exr`
- `nuke_shot_render_stereo`: `@shot_root/work/images/{name}/v{version}/{width}x{height}/{eye}/{Shot}.{SEQ}.exr`
- `nuke_shot_render_pub_stereo`: `@shot_root/publish/elements/{name}/v{version}/{width}x{height}/{eye}/{Shot}.{SEQ}.exr`
- `houdini_shot_render`: `@shot_root/work/images/{name}/v{version}/{width}x{height}/{Shot}.{SEQ}.exr`
- `nuke_shot_render_pub_stereo`: `@shot_root/publish/elements/{name}/v{version}/{width}x{height}/{Shot}.{SEQ}.exr`
- `photoshop_shot_jpg_publish`: `@shot_root/publish/photoshop/{name}.v{version}.jpg`

### Review templates (Quicktime/MOV for dailies)

- `maya_asset_render_review_quicktime`: `@asset_root/review/{Asset}_{name}_v{version}.mov`
- `maya_shot_render_review_quicktime`: `@shot_root/review/{Shot}_{name}_v{version}.mov`
- `nuke_shot_render_review_quicktime`: `@shot_root/review/{Shot}_{name}_v{version}.mov`
- `houdini_shot_render_review_quicktime`: `@shot_root/review/{Shot}_{name}_v{version}.mov`

### Pipeline interchange, render and review templates

These templates are defined in the project's `templates.yml` (added to
`abrahamADSK/toolkit_config_custom_template` in commit `4ea29d3`).
They are NOT injected by code — `tk_config.py` is generic and reads
whatever templates the project config defines.

**Asset templates:**
- `rendered_image_asset_publish`: `@asset_root/publish/renders/{name}/v{version}/{Asset}_{name}_v{version}.{SEQ}.exr`
- `movie_asset_publish`: `@asset_root/review/{Asset}_{name}_v{version}.mov`
- `usd_asset_publish`: `@asset_root/publish/usd/{name}.v{version}.usd`
- `fbx_asset_publish`: `@asset_root/publish/fbx/{name}.v{version}.fbx`
- `glb_asset_publish`: `@asset_root/publish/glb/{name}.v{version}.glb`
- `obj_asset_publish`: `@asset_root/publish/obj/{name}.v{version}.obj`
- `texture_asset_publish`: `@asset_root/publish/textures/{name}.v{version}.png`

**Shot templates:**
- `rendered_image_shot_publish`: `@shot_root/publish/renders/{name}/v{version}/{Shot}_{name}_v{version}.{SEQ}.exr`
- `movie_shot_publish`: `@shot_root/review/{Shot}_{name}_v{version}.mov`
- `usd_shot_publish`: `@shot_root/publish/usd/{name}.v{version}.usd`
- `fbx_shot_publish`: `@shot_root/publish/fbx/{name}.v{version}.fbx`

## Publish type to template mapping

Template matching in `tk_publish` is convention-based: given `publish_type`
and `entity_type`, it tries `{ptype_lower}_{entity_key}_publish` as the
template name. The `publish_type` string also becomes the `PublishedFileType`
code in ShotGrid, which determines what each DCC's loader can pick up.

| Publish Type | Asset Template | Shot Template | Flame loads? |
|---|---|---|---|
| Rendered Image | rendered_image_asset_publish | rendered_image_shot_publish | **Yes** (load_clip) |
| Movie | movie_asset_publish | movie_shot_publish | **Yes** (load_clip) |
| Texture | texture_asset_publish | — | **Yes** (load_clip) |
| USD | usd_asset_publish | usd_shot_publish | No |
| FBX | fbx_asset_publish | fbx_shot_publish | No |
| GLB | glb_asset_publish | — | No |
| OBJ | obj_asset_publish | — | No |
| Maya Scene | maya_asset_publish | maya_shot_publish | No |
| Alembic Cache | asset_alembic_cache | — | No |
| Nuke Script | nuke_asset_publish | nuke_shot_publish | No |
| Houdini Scene | houdini_asset_publish | houdini_shot_publish | No |
| Photoshop | photoshop_asset_publish | photoshop_shot_publish | No |
| After Effects | aftereffects_asset_publish | aftereffects_shot_publish | No |
| 3ds Max | `max_asset_publish` | `max_shot_publish` | No |
| MotionBuilder | `mobu_asset_publish` | `mobu_shot_publish` | No |

## Path resolution example

Given: Asset "hero_robot", type "Character", step "model", version 3

1. Template: `maya_asset_publish` → `@asset_root/publish/maya/{name}.v{version}.{maya_extension}`
2. Expand alias: `assets/{sg_asset_type}/{Asset}/{Step}/publish/maya/{name}.v{version}.{maya_extension}`
3. Apply fields: `assets/Character/hero_robot/model/publish/maya/main.v003.ma`
4. Prepend project_root: `/Users/Shared/FPT_MCP/assets/Character/hero_robot/model/publish/maya/main.v003.ma`

## Shot path resolution example

Given: Shot "SH010", Sequence "SEQ01", step "comp", version 5, Nuke

1. Template: `nuke_shot_publish` → `@shot_root/publish/nuke/{name}.v{version}.{nuke_extension}`
2. Expand alias: `sequences/{Sequence}/{Shot}/{Step}/publish/nuke/{name}.v{version}.{nuke_extension}`
3. Apply fields: `sequences/SEQ01/SH010/comp/publish/nuke/main.v005.nk`
4. Prepend project_root: `/Users/Shared/FPT_MCP/sequences/SEQ01/SH010/comp/publish/nuke/main.v005.nk`

## EXR render sequence path resolution example

Given: Shot "SH010", Sequence "SEQ01", step "light", name "beauty", version 2, frames 1001-1100

1. Template: `nuke_shot_render_pub_mono_exr` → `@shot_root/publish/elements/{name}/v{version}/{width}x{height}/{Shot}.{SEQ}.exr`
2. Single frame: `sequences/SEQ01/SH010/light/publish/elements/beauty/v002/1920x1080/SH010.1001.exr`

## Version auto-increment

`next_version()` scans the publish directory for existing versioned files:
- Pattern: `.v{NNN}.` in filenames
- Returns `max_version + 1`, or 1 if no files exist
- Always zero-padded to 3 digits (v001, v002, ..., v999)

## PublishedFile vs Version

- `PublishedFile` — reusable pipeline file (Maya scene, USD, texture, etc.)
  - Has `path`, `published_file_type`, `version_number`
  - Loaded by tk-multi-loader2 in Maya, Nuke, Flame
  - Linked to entity (Asset or Shot) and Task
  - `path` field stores `{"local_path": "/full/path/to/file.ma"}`
- `Version` — review/preview entity
  - Has `sg_uploaded_movie` (QuickTime/MOV for review)
  - Reviewed in RV, ShotGrid web, screening room
  - Often linked to same entity as PublishedFile but serves different purpose

## Toolkit Context object

The Context represents the current working environment in Toolkit:

```python
import sgtk

# Bootstrap (NOT used by fpt-mcp, shown for reference)
mgr = sgtk.bootstrap.ToolkitManager()
mgr.plugin_id = "basic.maya"
engine = mgr.bootstrap_engine("tk-maya", entity={"type": "Asset", "id": 100})
ctx = engine.context

# Context properties
ctx.project        # {"type": "Project", "id": 123, "name": "MyProject"}
ctx.entity         # {"type": "Asset", "id": 100, "name": "hero_robot"}
ctx.step           # {"type": "Step", "id": 5, "name": "Model"}
ctx.task           # {"type": "Task", "id": 456, "name": "model"}
ctx.user           # {"type": "HumanUser", "id": 1, "name": "Abraham"}
ctx.filesystem_locations  # ["/Users/Shared/FPT_MCP/assets/Character/hero_robot/model"]
ctx.shotgun_url    # "https://site.shotgrid.autodesk.com/detail/Asset/100"
```

## ShotgunAuthenticator

Manages user authentication for Toolkit:

```python
from tank_vendor.shotgun_authentication import ShotgunAuthenticator

authenticator = ShotgunAuthenticator()

# Interactive login (shows dialog in DCC)
user = authenticator.create_session_user(
    host="https://site.shotgrid.autodesk.com",
    login="username",
    session_token=None
)

# Script-based (for automation)
user = authenticator.create_script_user(
    host="https://site.shotgrid.autodesk.com",
    api_script="script_name",
    api_key="script_key"
)

sg = user.create_sg_connection()
```

## Engines (DCC integrations)

Engines are Toolkit plugins that run inside DCCs:

### Core engines
- `tk-maya` — Autodesk Maya integration
- `tk-nuke` — The Foundry Nuke integration
- `tk-houdini` — SideFX Houdini integration
- `tk-flame` — Autodesk Flame integration
- `tk-3dsmaxplus` / `tk-3dsmax` — Autodesk 3ds Max
- `tk-photoshopcc` — Adobe Photoshop CC
- `tk-aftereffects` — Adobe After Effects
- `tk-motionbuilder` — Autodesk MotionBuilder
- `tk-alias` — Autodesk Alias
- `tk-vred` — Autodesk VRED
- `tk-desktop` — ShotGrid Desktop app
- `tk-shell` — Command-line/shell engine
- `tk-shotgun` — ShotGrid web engine (browser actions)

### Engine startup
When a DCC launches with Toolkit, the engine:
1. Reads the environment config (e.g. `env/asset_step.yml`)
2. Initializes all configured apps
3. Registers menu items in the DCC
4. Establishes a ShotGrid connection

## Apps (pipeline tools)

Apps are modular tools that run inside engines. Key apps in tk-config-default2:

### tk-multi-publish2 — Publish pipeline
The central publishing app. Collects, validates, and publishes work files.
- Supports configurable publish plugins (collectors, validators, publishers)
- Creates PublishedFile entities in ShotGrid
- Copies files to publish location
- Updates version numbers
- Runs configurable hooks for each phase

### tk-multi-loader2 — Content loader
Loads published files into the current DCC scene.
- Reads PublishedFile entities from ShotGrid
- Supports reference, import, and open actions per file type
- Configurable actions per PublishedFileType
- Shows version history and thumbnails

### tk-multi-workfiles2 — Work file management
Manages work files (open, save, save-as, change context).
- Tracks current work file version
- Auto-increments version on save
- Allows switching between entities (Assets, Shots)
- Creates folders on disk following the schema

### tk-multi-snapshot — Snapshot/backup
Creates quick backups of the current work file.
- Saves to snapshots subdirectory
- Includes timestamp in filename
- No ShotGrid entity created (local backup only)

### tk-multi-breakdown — Scene breakdown
Shows all referenced/imported files in the current scene.
- Identifies outdated references
- One-click update to latest version
- Shows version comparison

### tk-multi-setframerange — Frame range
Sets the DCC timeline frame range from ShotGrid Shot data.
- Reads `sg_head_in`, `sg_cut_in`, `sg_cut_out`, `sg_tail_out`
- Applies to Maya timeline, Nuke frame range, etc.

### tk-multi-reviewsubmission — Submit for review
Creates a Version entity with a review movie.
- Renders quicktime/mov from the DCC
- Uploads to ShotGrid for dailies review
- Links to current entity and task

### tk-multi-shotgunpanel — ShotGrid panel
Embedded ShotGrid browser panel inside the DCC.
- Shows entity details, notes, versions, publishes
- Create notes directly from the DCC
- Navigate entity hierarchy

### tk-multi-launchapp — App launcher
Launches DCCs with Toolkit context pre-configured.
- Used by ShotGrid Desktop to launch Maya, Nuke, etc.
- Passes context (project, entity, task) to the engine

## Hooks

Hooks are customization points. Each app defines hooks that studios can override:

### Hook resolution order
1. Project config hooks (`config/hooks/`)
2. App-provided default hooks
3. Core hooks (tk-core)

### Common hooks
- `pick_environment.py` — Determines which environment config to use based on context
- `before_register_publish.py` — Runs before creating PublishedFile in ShotGrid
- `copy_file.py` — Custom file copy logic (for farm integration)
- `upload_version_for_review.py` — Custom review submission

### Hook example
```python
import sgtk

class MyHook(sgtk.Hook):
    def execute(self, **kwargs):
        # Custom logic here
        engine = self.parent.engine
        sg = engine.shotgun
        ctx = engine.context
        return result
```

## Frameworks

Frameworks are shared libraries used by multiple apps:

- `tk-framework-shotgunutils` — ShotGrid data model, query manager, settings
- `tk-framework-qtwidgets` — Reusable Qt/PySide widgets (thumbnail, navigation, etc.)
- `tk-framework-widget` — Legacy widget framework (deprecated)
- `tk-framework-desktopserver` — Local websocket server for browser integration
- `tk-framework-adobe` — Adobe CEP integration layer
- `tk-framework-adminui` — Admin UI components

## Environment files

Environment files configure which engines, apps, and frameworks are loaded for each context:

### Environment dispatch
The `pick_environment.py` hook determines the environment based on context:
- No entity → `project.yml`
- Asset, no Step → `asset.yml`
- Asset + Step → `asset_step.yml`
- Shot, no Step → `shot.yml`
- Shot + Step → `shot_step.yml`
- Sequence → `sequence.yml`

### Environment YAML structure
```yaml
# env/asset_step.yml
description: Configuration for Asset + Step context

engines:
  tk-maya:
    apps:
      tk-multi-publish2:
        location:
          type: app_store
          name: tk-multi-publish2
          version: v2.6.7
        collector: "{config}/hooks/tk-multi-publish2/collector.py"
        publish_plugins:
          - name: Upload for review
            hook: "{self}/upload_version.py"
          - name: Publish to ShotGrid
            hook: "{self}/publish_file.py:{engine}/tk-multi-publish2/basic/publish_session.py"
      tk-multi-loader2:
        location:
          type: app_store
          name: tk-multi-loader2
          version: v1.22.1
        actions_hook: "{self}/tk-maya_actions.py"
        action_mappings:
          Maya Scene: [reference, import, open]
          Alembic Cache: [reference, import]
          Image: [texture_node]
    location:
      type: app_store
      name: tk-maya
      version: v0.12.1

frameworks:
  tk-framework-shotgunutils_v5.x.x:
    location:
      type: app_store
      name: tk-framework-shotgunutils
      version: v5.8.5
```

## Filesystem schema

Schema files define folder creation rules. When Toolkit creates folders for an entity:

### project.yml
```yaml
type: "shotgun_entity"
name: "code"
entity_type: "Project"
filters: []
```

### asset.yml
```yaml
type: "shotgun_entity"
name: "code"
entity_type: "Asset"
filters:
  - path: "project"
    values: ["{project}"]
    relation: "is"
```

### shot.yml
```yaml
type: "shotgun_entity"
name: "code"
entity_type: "Shot"
filters:
  - path: "sg_sequence"
    values: ["{Sequence}"]
    relation: "is"
```

## tank command

The `tank` CLI tool for Toolkit operations (NOT used by fpt-mcp, reference only):

```bash
# Setup project
tank setup_project

# Create filesystem folders
tank Asset hero_robot folders

# Launch Maya with context
tank Asset hero_robot launch_maya

# Run a shell command with context
tank Shot SH010 shell

# Cache app store
tank cache_apps

# Check for updates
tank updates
```

## Pipeline steps (Step entity)

Standard pipeline steps and their short names:

| Step | Short Name | Entity Type | Color |
|------|-----------|-------------|-------|
| Concept | concept | Asset | Blue |
| Model | model | Asset | Cyan |
| Rig | rig | Asset | Green |
| Texture/Lookdev | lookdev | Asset | Yellow |
| Layout | layout | Shot | Orange |
| Animation | anim | Shot | Red |
| FX | fx | Shot | Purple |
| Lighting | light | Shot | Yellow |
| Compositing | comp | Shot | Blue |
| Matchmove | matchmove | Shot | Green |

## Common PublishedFileType entities

| Code | Description |
|------|-------------|
| Maya Scene | `.ma` or `.mb` Maya file |
| Nuke Script | `.nk` Nuke comp |
| Houdini Scene | `.hip` Houdini file |
| Alembic Cache | `.abc` Alembic geometry cache |
| Image | `.exr`, `.dpx`, `.jpg`, `.png` image |
| Rendered Image | EXR render output |
| Texture | Texture map image |
| Movie | `.mov` QuickTime review movie |
| Photoshop Image | `.psd` Photoshop file |
| VRED Scene | `.vpb` VRED file |
| Flame Clip | `.clip` Flame batch/clip |
| USD Scene | `.usd` / `.usda` / `.usdc` Universal Scene Description |
| FBX Model | `.fbx` FBX interchange |

## Advanced — Distributed config resolution

When a PipelineConfiguration has a descriptor instead of a local path, Toolkit resolves it:

1. Read descriptor from PipelineConfiguration entity
2. Check bundle_cache for matching version
3. If not cached: download/clone to bundle_cache
4. Read `config/core/roots.yml` and `config/core/templates.yml` from cached config
5. Resolve paths using the local storage root for current platform

fpt-mcp's `tk_config.py` implements steps 1-5 without sgtk bootstrap.

## Advanced — Multi-root configs

Projects can have multiple storage roots for different data types:

```yaml
# roots.yml
primary:
  mac_path: /fast_ssd/projects
  default: true
renders:
  mac_path: /large_hdd/renders
editorial:
  mac_path: /shared/editorial
```

Templates reference roots explicitly:
```yaml
paths:
  maya_asset_publish:
    definition: "@asset_root/publish/maya/{name}.v{version}.{maya_extension}"
    root_name: primary
  nuke_shot_render:
    definition: "shots/{Sequence}/{Shot}/{Step}/renders/{name}/v{version}/{Shot}.{SEQ}.exr"
    root_name: renders
```

## Anti-patterns (NEVER do these)

- Using lowercase `{shot}` instead of `{Shot}` — templates are case-sensitive
- Using `{shot_name}`, `{asset_name}`, `{step}` — these tokens don't exist
- Using `{frame}` instead of `{SEQ}` — SEQ is the standard frame token
- Using `{ext}` — use the DCC-specific extension token
- Hardcoding paths without reading roots.yml — paths differ per platform
- Modifying the user's templates.yml or roots.yml from the MCP server
- Assuming all projects have a PipelineConfiguration — basic projects don't
- Using sgtk.bootstrap when you can read YAML directly — bootstrap is heavy
- Confusing Step and Task — Step is the pipeline category, Task is the assignment
- Assuming `@asset_root` always exists — verify aliases are defined in templates.yml
- Hardcoding bundle_cache path — use the platform-specific location
- Assuming descriptor version matches file version — always read from the actual config
- Creating folders manually instead of using `tank folders` — breaks the schema
- Mixing template tokens from different entity contexts (Asset tokens in Shot templates)
