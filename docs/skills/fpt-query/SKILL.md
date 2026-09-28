---
name: fpt-query
description: Query, count or report on ShotGrid/FPT data — rollups by status, sequence or assignee, "how many", "which shots are", note threads, activity history. Fire BEFORE any broad sg_find — summarize-vs-find, field discipline, and why a one-entity check is not a state check. E.g. "¿cuántos shots quedan en review?", "dame un resumen del estado de la secuencia", "¿está limpio ShotGrid?", "which tasks are overdue".
---

# Querying and reporting on ShotGrid

Applies to **any** FPT site and entity type, not one project's pipeline. This is
the read side: it cannot damage the database, so the failure modes are wrong
answers and exhausted context, not lost work.

For filter operators, method signatures and pagination syntax use
`search_sg_docs` — that is the reference layer and it covers all three APIs.
This skill is **which question deserves which tool, and what a plausible answer
hides**.

## The failure that looks like an answer

### A single-entity check is not a state check

Asking "is ShotGrid clean?" and querying only `Version` returned *clean* in-vivo
while `PublishedFile` rows from the same operation were still there. The answer
was confidently wrong.

**Enumerate every entity type the operation writes before declaring state.** A
publish touches `PublishedFile`, usually `Version`, sometimes `Attachment` and
the `Task` status. A cleanup check that covers one of them is not a check.

Same rule for "did it work?" — verify the entity you *created*, not a neighbour
that happens to exist.

## Aggregate on the server

`fpt_reporting(action="summarize")` does count / sum / avg / min / max with
`grouping`, server-side. Reach for it whenever the question is a **number or a
breakdown**:

- "how many shots per status" → `summarize` + `grouping`, one small payload.
- `sg_find` then counting rows in your head → pulls every row into context to
  throw them away. For a few hundred shots this is the difference between a
  200-token answer and a 40,000-token one.

Use `sg_find` when the user needs the **rows themselves**, not a statistic.

## Field discipline

Every field you name is paid once per row. Name only the fields you will
actually read or show; never pass a field list harvested from `sg_schema`
wholesale. Set a real `limit` — an exploratory query is 5–20 rows, not the
default.

If you do not know the result size, `summarize` the count first, then decide
whether to fetch rows at all.

## Schema before filter

Fields are per-project. Custom fields carry the `sg_` prefix and pipelines add
and remove them freely — this includes the legal values of `sg_status_list`.

Run `sg_schema` on the entity before filtering on any field you have not
personally seen on this site. Guessing a field name produces an empty result
set, and an empty result set reads exactly like "there is nothing there".

## History questions

- `fpt_reporting(action="activity")` — what happened to this entity, in order.
- `fpt_reporting(action="note_thread")` — the full reply chain of a Note.

Both beat reconstructing a timeline from `created_at` / `updated_at` fields, and
neither needs a broad `find`.

## When the safety module refuses

`safety.py` blocks unfiltered-and-unlimited searches, hallucinated operators
(`matches`, `regex`, `like`, `is_exactly` do not exist) and invented status
words (`review`, `complete`, `in_progress` — the codes are short: `rev`,
`cmpt`, `ip`). It tells you the legal set; it will not tell you the intent.

**When it fires, do not retry with a narrower guess.** Run `sg_schema` for the
real field or status codes on this project, then re-query once.

## What this skill is not

- **Not publishing.** Registering a `PublishedFile`, version-ups and publish
  types are `fpt-publish`.
- **Not an API reference.** Operators, filter grammar, pagination and method
  signatures are `search_sg_docs`.
- **Not bulk mutation.** `fpt_bulk` (delete / revive / batch / editorial)
  carries its own contracts in its docstring; nothing here authorises a write.
