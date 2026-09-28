# Claude Code skills owned by fpt-mcp

Procedural knowledge for *driving ShotGrid / Flow Production Tracking through this
server* — the order of operations and what fails silently. Distinct from the three
other layers:

| Layer | Holds | Where |
|---|---|---|
| **Tool** | Side effects, enforcement, I/O | `src/fpt_mcp/server.py`, `safety.py`, `paths.py` |
| **Skill** | The recipe: which tools, in what order, the traps | here |
| **RAG** | Reference manual: entities, fields, filters, templates | `src/fpt_mcp/docs/*.md` |
| **Memory** | Cross-session state and behavioural feedback | `~/.claude/.../memory/` |

A skill costs its `description` in **every** session and loads its body only when
the description matches the user's intent. Keep descriptions tight and triggery.

## Why they live here and not in `.claude/skills/`

`.gitignore` excludes `.claude/` wholesale in this repo, so a skill placed there
would be silently untracked. Keeping them under `docs/` makes them version
controlled and subject to the atomic-docs rule: **a skill ships in the same commit
as the code it describes.**

They are *activated* by symlinking into the user-level skills directory, which is
what makes them fire from any working directory — not only when the cwd is this
repo. Skill discovery follows symlinked directories (verified).

## Activating them on a fresh clone

```bash
for s in fpt-query fpt-publish; do
  ln -s "$PWD/docs/skills/$s" ~/.claude/skills/"$s"
done
```

Remove with `rm ~/.claude/skills/<name>` — that deletes the symlink, never the
tracked content.

## Current skills

| Skill | Fires on |
|---|---|
| `fpt-query` | Any read-only analysis: counts, rollups, breakdowns, note threads, activity. The risk here is not damage but token exhaustion — `summarize` over `find`, explicit `fields`, `sg_schema` before an unfamiliar filter |
| `fpt-publish` | The publish chain: Step→Task resolution, the `{name}`-from-Step contract, publish types, the `%04d` rejection, and why a published path is version-upped rather than rewritten |

`fpt-review` was deliberately **not** written: the Version + media + status half is
validated, but the playlist / review-session / Note-reply half would be invented.
Write it only when that half has actually been exercised.

## Skill vs `safety.py`

Where `safety.py` refuses something, the skill's job is to say what to do
**instead** — never to restate the prohibition. The regexes fire without a trigger
word; a skill does not. Duplicating a rule into both creates exactly the drift the
atomic-docs invariant exists to prevent.

This is why the server `instructions` block is now five items rather than seven:
entity-link format and Toolkit token case are enforced at `safety.py:35`, `:106`
and `:137`, so the prose repeating them was redundant always-on cost.

## Editing

Edit the file in this repo — the symlink means the change is live immediately, with
no reinstall and no MCP restart. That is the main reason procedure belongs in a
skill rather than in a tool docstring or the server `instructions` block, both of
which need a release and a full Claude Code restart to take effect.
