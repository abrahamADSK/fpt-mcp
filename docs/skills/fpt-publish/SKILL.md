---
name: fpt-publish
description: Publish a file, render or cache to ShotGrid/FPT and register the PublishedFile — image sequences, version-ups, publish types, work-area paths. Fire BEFORE tk_publish — the {name}-from-Step contract, the %04d rejection, and why a published path is never rewritten. E.g. "publica el render del shot", "registra esta versión en FPT", "sube el EXR como publish", "publish this cache".
---

# Publishing to ShotGrid Toolkit

Applies to **any** Toolkit-configured project and publish type, not one spot's
comp delivery. Everything here was falsified in-vivo; none of it is inferable
from the template definitions.

For template tokens, `PublishedFileType` lists and the `PublishedFile` vs
`Version` distinction use `search_sg_docs` — that is the reference layer. This
skill is **the order of operations and what fails silently**.

## The two silent failures

### 1. `{name}` derives from the Step — it is never invented

The `{name}` token in a publish template resolves from the pipeline **Step**
(`LGT`, `ANM`, `CMP`), not from a literal you choose. Writing a plausible word
there produces a path that resolves, publishes without error, and lands
somewhere no loader looks for it.

When the user says "put light in the name", that is the *convention* being
requested, not a string literal.

Corollary, confirmed in-vivo: **`{name}` rejects underscores.** A `filter_by`
on the token silently refuses `my_name`-shaped values. If templates behave as
though your edit never happened, reload them (`tk.reload_templates()`) before
suspecting anything else.

### 2. Resolve before you publish

Call `tk_resolve_path` and **read the path it returns** before `tk_publish`.
It answers from the project's real `PipelineConfiguration`, so it is the only
statement about where the file is going that is not a guess.

Publishing first and inspecting after means the copy has already happened.

## Image sequences: the `%04d` exception

`tk_publish` **rejects `%04d` paths in both modes**. This is a known unfixed gap,
not a usage error.

For a rendered sequence — and validated where the project has no
`PipelineConfiguration` — create the `PublishedFile` directly with `sg_create`,
with the `%04d` path and the `Rendered Image` type, instead of routing through
`tk_publish`. Everything else about the publish (Task link, version number,
type) still applies.

## Publish type, not missing Task

If a native publish does not appear where expected, the usual cause is the
**`PublishedFileType`**, not an absent Task. `extra_publish_types` exists for
that case: a publish can have a perfectly good Task and still be invisible to a
loader filtering on type.

Note that LGT renders and comp renders deliberately share `Rendered Image` for
cross-DCC loader interop — so type alone does not disambiguate pipeline step.
That is why `openclip_create` asks rather than guessing which Task feeds a
conform.

## A published path is never rewritten

`safety.py` refuses `sg_update` on a `PublishedFile` `path`, and refuses
deleting one. Both refusals are correct — other artists may already have loaded
the reference.

What to do **instead**:

- Wrong path, file still needed → publish a **new version**. Version numbers
  auto-increment; the old row stays as history.
- Publish should disappear from loaders → set `sg_status_list` to `omt`, which
  hides it while preserving the reference chain. Do not delete.
- A genuine bulk path migration → out of scope for a tool call. It needs a
  deliberate script with a registry and a revert path, run knowingly.

## Where the file lives before it is published

Publish sources come from the **task work area**, resolved through the template —
not from home, `/tmp`, or an invented root. Transient render artifacts belong
inside the shot's Toolkit structure too. A publish whose source sits outside the
structure will publish fine and be unreproducible.

## What this skill is not

- **Not querying.** Counting publishes, status rollups and "is it there?" checks
  are `fpt-query` — and note that checking only `Version` does not tell you
  whether the `PublishedFile` landed.
- **Not an API reference.** Template tokens, type lists and `sg_create` field
  names are `search_sg_docs`.
- **Not dependency wiring.** We have no validated recipe for
  `upstream_published_files` graphs; confirm the fields with `sg_schema` and
  treat any procedure as unproven.
- **Not the review hand-off.** Creating the `Version`, attaching media and
  moving review status is a separate, still-unwritten workflow.
