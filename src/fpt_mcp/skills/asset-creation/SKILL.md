---
name: asset-creation
description: |
  Complete workflow for creating 3D models of VFX assets from ShotGrid. Use this skill
  WHENEVER the user asks to create, generate, or model a 3D asset, a prop, a character,
  a scene, or any three-dimensional object. Also triggered when the user says "create the
  model", "generate the geometry", "make the 3D of", "model in Maya", or any variation of
  creating 3D content. This skill orchestrates the search for reference material in ShotGrid,
  presents the options to the user, and executes the chosen pipeline (AI generation or direct
  Maya modeling). MANDATORY before any 3D creation.
---

# Asset Creation Workflow

This skill defines the complete flow for creating 3D models from assets registered
in ShotGrid. The goal is that the user never has to manually search for references
or decide technical paths — the assistant does it for them, presenting clear options.

## Context

The system has two MCP servers:
- **fpt-mcp**: ShotGrid API (sg_find, sg_download, etc.)
- **maya-mcp**: Maya + remote GPU (shape_generate_remote, shape_generate_text, maya_create_primitive, etc.)

## Main Flow

### Step 1: Identify the entity

Check if there is ShotGrid context in the message (it comes as JSON at the end of the prompt
when launched from the Qt console via AMI). Look for fields like `entity_type`, `entity_id`,
`project_id`.

**If there is AMI context** (entity_type + entity_id present):
- You already have the entity. Skip to Step 2.

**If there is NO context** (the user wrote something like "create the dragon model"):
- Extract from the user's text which asset to search for (name, type, description)
- Use `sg_find` to search for matching Assets:
  ```
  sg_find(entity_type="Asset",
          filters=[["code", "contains", "<term>"]],
          fields=["id", "code", "sg_asset_type", "description", "image", "sg_status_list"])
  ```
- If there are multiple results, present the list and ask the user to choose:
  ```
  Found these assets:
  1. Dragon_Hero (Character) — ID #1478
  2. Dragon_BG (Environment) — ID #1502
  3. Dragon_Prop (Prop) — ID #1489
  Which one?
  ```
- If there is exactly one, confirm: "Found Asset 'Dragon_Hero' (#1478). Is this the one?"
- If there are no results, inform and ask if they want to search differently or create a new one.

### Step 2: Discover visual reference material

Once the entity is identified, search ALL available visual material. This is crucial
because the quality of the 3D model directly depends on the reference used.

Execute these searches in parallel (or sequentially if not possible):

**2a. Asset's own thumbnail:**
```
sg_find(entity_type="Asset",
        filters=[["id", "is", <asset_id>]],
        fields=["image", "code", "description"])
```
The `image` field is the asset's main thumbnail.

**2b. Linked Versions with uploaded files:**
```
sg_find(entity_type="Version",
        filters=[["entity", "is", {"type": "Asset", "id": <asset_id>}]],
        fields=["id", "code", "sg_uploaded_movie", "image", "sg_task",
                "sg_status_list", "created_at", "description"],
        order=[{"field_name": "created_at", "direction": "desc"}],
        limit=10)
```
Versions may have thumbnails (`image`), movies (`sg_uploaded_movie`),
or static frames that serve as reference.

**2c. Linked image PublishedFiles:**
```
sg_find(entity_type="PublishedFile",
        filters=[["entity", "is", {"type": "Asset", "id": <asset_id>}],
                 ["published_file_type.PublishedFileType.code", "in",
                  ["Image", "Texture", "Concept", "Reference", "image", "texture"]]],
        fields=["id", "code", "path", "image", "published_file_type",
                "version_number", "created_at", "description"],
        order=[{"field_name": "created_at", "direction": "desc"}],
        limit=10)
```

**2d. Notes with attachments (concept art, visual feedback):**
```
sg_find(entity_type="Note",
        filters=[["note_links", "is", {"type": "Asset", "id": <asset_id>}],
                 ["attachments", "is_not", null]],
        fields=["id", "subject", "attachments", "created_at"],
        order=[{"field_name": "created_at", "direction": "desc"}],
        limit=5)
```

### Step 3: Present the options to the user

Organize all found material in a clear, numbered list. Group by source
so the user understands where each reference comes from:

```
Reference material available for "Dragon_Hero" (#1478):

Asset Thumbnail:
  1. Asset's main thumbnail (profile image)

Recent Versions:
  2. v012 — "Final approved concept" (2026-03-15) — has thumbnail
  3. v008 — "Initial sketch" (2026-03-10) — has thumbnail
  4. v005 — "Color reference" (2026-03-08) — has movie

Published Files:
  5. Dragon_Hero_concept_v003.png (Concept, v3)
  6. Dragon_Hero_ref_color_v001.jpg (Reference, v1)

Note Attachments:
  7. Note "Design approval" — 2 attachments

Which reference do you want to use? (number or "none" for text-to-3D / direct modeling)
```

If a reference has a downloadable thumbnail or image, mention it. The user
needs to see the images to decide — if they ask to see them, use `sg_download` to
download the image and display it (the Qt console renders images in markdown).

### Step 4: Choose creation method

Based on the user's selection, present the creation options:

**If they chose a visual reference (image):**
```
Selected reference: "Final approved concept" (v012)

How do you want to create the 3D model?
1. AI Generation (image-to-3D) — Sends the image to the GPU server,
   Hunyuan3D-2 generates a detailed mesh (~3-8 min)
2. Direct Maya Modeling — Build the object with primitives
   and modeling operations (fast, geometric result)
3. You decide — Choose the best option for this case
```

**If they chose "none" (no visual reference):**
```
No visual reference. How do you want to create the model?
1. AI Generation (text-to-3D) — Describe the object and the GPU server
   generates a mesh from text (~3-8 min)
2. Direct Maya Modeling — Build the object with primitives
   and modeling operations (fast, geometric result)
```

### Step 5: Execute the chosen pipeline

**Image-to-3D:**
1. `sg_download` → download the selected reference image to local disk
2. `shape_generate_remote` → send image to GPU server (mesh.glb ~3-8 min)
3. `texture_mesh_remote` → paint texture onto the mesh (~3-5 min)
4. `maya_execute_python` → import the textured mesh into Maya

**Text-to-3D:**
1. Translate the user's description to English (better results with English prompts)
2. `shape_generate_text` → send prompt to GPU server (mesh.glb ~3-8 min)
3. Optionally `texture_mesh_remote` if a reference image is available
4. `maya_execute_python` → import the mesh into Maya

**Direct Maya Modeling:**
1. `maya_launch` if Maya is not open
2. `maya_new_scene` or confirm using the current scene
3. Combine `maya_create_primitive` + `maya_transform` + `maya_assign_material`
   + `maya_execute_python` to build the object
4. For complex shapes, use cmds.polyExtrudeFacet, polyBevel, polyUnite, etc.

### Step 6: Post-creation (optional)

After creating the model, offer:
- Save the scene: `maya_save_scene`
- Publish to ShotGrid: `tk_resolve_path` + `tk_publish`
- Create a Version with a thumbnail of the result

## Important Rules

- **Always ask before acting.** Do not launch an 8-minute pipeline without the
  user confirming which reference and which method they want.
- **Respond in the user's language.**
- **Use MCP tools.** Never tell the user to do something manually if
  you can do it with sg_find, sg_download, shape_generate_remote, etc.
- **If Maya doesn't respond**, use `maya_launch` to open it automatically.
- **Translate text-to-3D prompts to English** internally for better results.
- **Be concise but informative.** Present options clearly without lengthy
  paragraphs. Use lists and numbering.
