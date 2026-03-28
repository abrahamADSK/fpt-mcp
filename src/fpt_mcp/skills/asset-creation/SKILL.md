---
name: asset-creation
description: |
  Workflow completo para crear modelos 3D de assets VFX desde ShotGrid. Usa este skill
  SIEMPRE que el usuario pida crear, generar, o modelar un asset 3D, un prop, un personaje,
  una escena, o cualquier objeto tridimensional. También se activa cuando el usuario dice
  "crea el modelo", "genera la geometría", "haz el 3D de", "modela en Maya", o cualquier
  variación de crear contenido 3D. Este skill orquesta la búsqueda de material de referencia
  en ShotGrid, presenta las opciones al usuario, y ejecuta el pipeline elegido (IA generativa
  o modelado directo en Maya). OBLIGATORIO antes de cualquier creación 3D.
---

# Asset Creation Workflow

Este skill define el flujo completo para crear modelos 3D a partir de assets registrados
en ShotGrid. El objetivo es que el usuario nunca tenga que buscar manualmente las referencias
ni decidir rutas técnicas — el asistente lo hace por él, presentando opciones claras.

## Contexto

El sistema tiene dos servidores MCP:
- **fpt-mcp**: ShotGrid API (sg_find, sg_download, etc.)
- **maya-mcp**: Maya + GPU remota (shape_generate_remote, shape_generate_text, maya_create_primitive, etc.)

## Flujo principal

### Paso 1: Identificar la entidad

Comprueba si hay contexto de ShotGrid en el mensaje (viene como JSON al final del prompt
cuando se lanza desde la consola Qt con AMI). Busca campos como `entity_type`, `entity_id`,
`project_id`.

**Si hay contexto AMI** (entity_type + entity_id presentes):
- Ya tienes la entidad. Salta al Paso 2.

**Si NO hay contexto** (el usuario escribió algo como "crea el modelo del dragón"):
- Extrae del texto del usuario qué asset buscar (nombre, tipo, descripción)
- Usa `sg_find` para buscar Assets que coincidan:
  ```
  sg_find(entity_type="Asset",
          filters=[["code", "contains", "<término>"]],
          fields=["id", "code", "sg_asset_type", "description", "image", "sg_status_list"])
  ```
- Si hay varios resultados, presenta la lista al usuario y pide que elija:
  ```
  Encontré estos assets:
  1. Dragon_Hero (Character) — ID #1478
  2. Dragon_BG (Environment) — ID #1502
  3. Dragon_Prop (Prop) — ID #1489
  ¿Cuál es?
  ```
- Si hay exactamente uno, confirma: "Encontré Asset 'Dragon_Hero' (#1478). ¿Es este?"
- Si no hay resultados, informa y pregunta si quiere buscarlo de otra forma o crearlo nuevo.

### Paso 2: Descubrir material gráfico de referencia

Una vez identificada la entidad, busca TODO el material visual disponible. Esto es crucial
porque la calidad del modelo 3D depende directamente de la referencia usada.

Ejecuta estas búsquedas en paralelo (o secuencialmente si no es posible):

**2a. Thumbnail del propio Asset:**
```
sg_find(entity_type="Asset",
        filters=[["id", "is", <asset_id>]],
        fields=["image", "code", "description"])
```
El campo `image` es el thumbnail principal del asset.

**2b. Versions vinculadas con ficheros subidos:**
```
sg_find(entity_type="Version",
        filters=[["entity", "is", {"type": "Asset", "id": <asset_id>}]],
        fields=["id", "code", "sg_uploaded_movie", "image", "sg_task",
                "sg_status_list", "created_at", "description"],
        order=[{"field_name": "created_at", "direction": "desc"}],
        limit=10)
```
Las Versions pueden tener thumbnails (`image`), movies (`sg_uploaded_movie`),
o frames estáticos que sirven como referencia.

**2c. PublishedFiles de imagen vinculados:**
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

**2d. Notes con adjuntos (concept art, feedback visual):**
```
sg_find(entity_type="Note",
        filters=[["note_links", "is", {"type": "Asset", "id": <asset_id>}],
                 ["attachments", "is_not", null]],
        fields=["id", "subject", "attachments", "created_at"],
        order=[{"field_name": "created_at", "direction": "desc"}],
        limit=5)
```

### Paso 3: Presentar las opciones al usuario

Organiza todo el material encontrado en una lista clara y numerada. Agrupa por fuente
para que el usuario entienda de dónde viene cada referencia:

```
📋 Material de referencia disponible para "Dragon_Hero" (#1478):

🖼️ Thumbnail del Asset:
  1. Thumbnail principal del asset (imagen de perfil)

🎬 Versions recientes:
  2. v012 — "Concept final aprobado" (2026-03-15) — tiene thumbnail
  3. v008 — "Boceto inicial" (2026-03-10) — tiene thumbnail
  4. v005 — "Referencia de color" (2026-03-08) — tiene movie

📁 Ficheros publicados:
  5. Dragon_Hero_concept_v003.png (Concept, v3)
  6. Dragon_Hero_ref_color_v001.jpg (Reference, v1)

💬 Adjuntos en Notas:
  7. Nota "Aprobación diseño" — 2 adjuntos

¿Qué referencia quieres usar? (número o "ninguna" para text-to-3D / modelado directo)
```

Si alguna referencia tiene thumbnail o imagen descargable, menciónalo. El usuario
necesita ver las imágenes para decidir — si pide verlas, usa `sg_download` para
descargar la imagen y muéstrala (la consola Qt renderiza imágenes en markdown).

### Paso 4: Elegir método de creación

Según lo que elija el usuario, presenta las opciones de creación:

**Si eligió una referencia visual (imagen):**
```
Referencia seleccionada: "Concept final aprobado" (v012)

¿Cómo quieres crear el modelo 3D?
1. 🤖 IA Generativa (image-to-3D) — Envía la imagen al servidor GPU,
   Hunyuan3D-2 genera un mesh detallado (~3-8 min)
2. 🎨 Modelado directo en Maya — Construyo el objeto con primitivas
   y operaciones de modelado (rápido, resultado geométrico)
3. 🧠 Decide tú — Elige la mejor opción según el caso
```

**Si eligió "ninguna" (sin referencia visual):**
```
Sin referencia visual. ¿Cómo quieres crear el modelo?
1. 🤖 IA Generativa (text-to-3D) — Describe el objeto y el servidor GPU
   genera un mesh desde texto (~3-8 min)
2. 🎨 Modelado directo en Maya — Construyo el objeto con primitivas
   y operaciones de modelado (rápido, resultado geométrico)
```

### Paso 5: Ejecutar el pipeline elegido

**Image-to-3D:**
1. `sg_download` → descargar la imagen de referencia seleccionada a disco local
2. `shape_generate_remote` → enviar imagen al servidor GPU (mesh.glb ~3-8 min)
3. `texture_mesh_remote` → pintar textura sobre el mesh (~3-5 min)
4. `maya_execute_python` → importar el mesh texturizado en Maya

**Text-to-3D:**
1. Traduce la descripción del usuario a inglés (mejores resultados con prompts en inglés)
2. `shape_generate_text` → enviar prompt al servidor GPU (mesh.glb ~3-8 min)
3. Opcionalmente `texture_mesh_remote` si hay imagen de referencia disponible
4. `maya_execute_python` → importar el mesh en Maya

**Modelado directo en Maya:**
1. `maya_launch` si Maya no está abierta
2. `maya_new_scene` o confirmar usar la escena actual
3. Combinar `maya_create_primitive` + `maya_transform` + `maya_assign_material`
   + `maya_execute_python` para construir el objeto
4. Para formas complejas, usar cmds.polyExtrudeFacet, polyBevel, polyUnite, etc.

### Paso 6: Post-creación (opcional)

Después de crear el modelo, ofrece:
- Guardar la escena: `maya_save_scene`
- Publicar en ShotGrid: `tk_resolve_path` + `tk_publish`
- Crear una Version con thumbnail del resultado

## Reglas importantes

- **Siempre pregunta antes de actuar.** No lances un pipeline de 8 minutos sin que el
  usuario haya confirmado qué referencia y qué método quiere.
- **Responde en español.** El usuario es hispanohablante.
- **Usa las herramientas MCP.** Nunca le digas al usuario que haga algo manualmente si
  puedes hacerlo tú con sg_find, sg_download, shape_generate_remote, etc.
- **Si Maya no responde**, usa `maya_launch` para abrirlo automáticamente.
- **Traduce prompts de text-to-3D a inglés** internamente para mejores resultados.
- **Sé conciso pero informativo.** Presenta las opciones de forma clara sin párrafos
  extensos. Usa listas y numeración.
