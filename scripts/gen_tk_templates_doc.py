#!/usr/bin/env python3
"""Generate the Toolkit template reference block of docs/TK_API.md from the
REAL project configuration (``<config>/core/templates.yml``).

Why this exists
---------------
TK_API.md is RAG corpus: the model reads it to reason about which templates
exist and what they resolve to. It used to carry a hand-written "standard
tk-config-default2" list, and by Chat 108 only 34 of its 79 rows matched the
project's real ``templates.yml`` — 32 differed (the ``{name}_{Step}`` naming,
literal extensions) and 13 named templates the config does not have. A model
reading that list is being fed hallucinations. ``verify_templates.py`` never
noticed because it compares the doc against the TEST FIXTURE, not the config.

So the block is now generated, never edited by hand, and ``verify_templates``
check 8 fails when the committed block differs from what this script renders
from the real config.

Where the real config is
------------------------
The path is machine-specific, so it is never hard-coded. Resolution order:
``--templates PATH`` → ``FPT_MCP_TEMPLATES_YML`` in the environment → the same
key in the repo's (untracked) ``.env``. Without it, ``--write`` refuses and
check 8 skips with a notice (CI has no project config).

Usage
-----
    python scripts/gen_tk_templates_doc.py            # print the block
    python scripts/gen_tk_templates_doc.py --write    # rewrite TK_API.md
    python scripts/gen_tk_templates_doc.py --templates /path/to/templates.yml
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TK_API_DOC = REPO_ROOT / "src" / "fpt_mcp" / "docs" / "TK_API.md"
ENV_KEY = "FPT_MCP_TEMPLATES_YML"

BLOCK_START = "<!-- generated:tk_templates start — scripts/gen_tk_templates_doc.py, do not edit by hand -->"
BLOCK_END = "<!-- generated:tk_templates end -->"

# Group order and the definition prefixes that select each group.
_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("Asset templates", ("@asset_root", "assets/")),
    ("Shot templates", ("@shot_root", "sequences/{Sequence}/{Shot}")),
    ("Sequence templates", ("@sequence_step_root", "@sequence_root", "sequences/")),
]


def resolve_templates_path(cli_value: str | None = None) -> Path | None:
    """Return the real templates.yml path, or None when none is configured.

    Args:
        cli_value: an explicit ``--templates`` value, which wins.

    Returns:
        The path when one is configured (it may still not exist), else None.
    """
    if cli_value:
        return Path(cli_value).expanduser()
    value = os.environ.get(ENV_KEY, "").strip()
    if not value:
        env_file = REPO_ROOT / ".env"
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith(ENV_KEY + "="):
                    value = line.split("=", 1)[1].strip().strip("'\"")
                    break
    return Path(value).expanduser() if value else None


def _group_of(definition: str) -> str:
    """Name the group a template belongs to, from its definition prefix."""
    for group, prefixes in _GROUPS:
        if definition.startswith(prefixes):
            return group
    return "Project-level templates"


def render_block(templates_yml: Path) -> str:
    """Render the generated Markdown block (markers included) for a config.

    Args:
        templates_yml: path to the project's ``core/templates.yml``.

    Returns:
        The block text, starting with BLOCK_START and ending with BLOCK_END.
    """
    import yaml  # type: ignore[import-untyped]

    data = yaml.safe_load(templates_yml.read_text(encoding="utf-8")) or {}
    paths = data.get("paths") or {}
    keys = data.get("keys") or {}
    aliases = {k: v for k, v in paths.items() if isinstance(v, str)}
    templates = {
        k: v["definition"]
        for k, v in paths.items()
        if isinstance(v, dict) and "definition" in v
    }

    grouped: dict[str, list[tuple[str, str]]] = {}
    for name, definition in templates.items():
        grouped.setdefault(_group_of(definition), []).append((name, definition))

    out = [
        BLOCK_START,
        "",
        f"Generated from this project's `templates.yml`: **{len(templates)} templates, "
        f"{len(aliases)} aliases, {len(keys)} keys**. Only these names exist — a template not listed "
        "here does not exist in this pipeline, whatever stock Toolkit configs ship.",
        "",
        "### Aliases (path shortcuts)",
        "",
        "```yaml",
        *[f"{k}: {v}" for k, v in sorted(aliases.items())],
        "```",
        "",
        "Aliases expand at resolution time: `@asset_root/publish/...` becomes "
        "`assets/{sg_asset_type}/{Asset}/{Step}/publish/...`.",
        "",
        f"### Keys declared ({len(keys)})",
        "",
        ", ".join(f"`{{{k}}}`" for k in sorted(keys)),
    ]
    order = [g for g, _ in _GROUPS] + ["Project-level templates"]
    for group in order:
        rows = grouped.get(group)
        if not rows:
            continue
        out += ["", f"### {group} ({len(rows)})", ""]
        out += [f"- `{n}`: `{d}`" for n, d in sorted(rows)]
    out += ["", BLOCK_END]
    return "\n".join(out)


def extract_block(doc_text: str) -> str | None:
    """Return the generated block currently in the doc, markers included."""
    start = doc_text.find(BLOCK_START)
    end = doc_text.find(BLOCK_END)
    if start == -1 or end == -1 or end < start:
        return None
    return doc_text[start:end + len(BLOCK_END)]


def main() -> int:
    """CLI entry point: print or write the generated block."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--templates", help=f"templates.yml path (else ${ENV_KEY} / .env)")
    parser.add_argument("--write", action="store_true", help="rewrite the block in TK_API.md")
    args = parser.parse_args()

    path = resolve_templates_path(args.templates)
    if path is None or not path.is_file():
        print(f"[gen] no project templates.yml: set {ENV_KEY} or pass --templates "
              f"(got {path})", file=sys.stderr)
        return 2
    block = render_block(path)
    if not args.write:
        print(block)
        return 0
    doc = TK_API_DOC.read_text(encoding="utf-8")
    current = extract_block(doc)
    if current is None:
        print(f"[gen] markers not found in {TK_API_DOC}", file=sys.stderr)
        return 2
    TK_API_DOC.write_text(doc.replace(current, block), encoding="utf-8")
    print(f"[gen] wrote {TK_API_DOC.relative_to(REPO_ROOT)} from {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
