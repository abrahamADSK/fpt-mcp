#!/usr/bin/env python3
"""Verify Toolkit pipeline templates declared in docs/TK_API.md against the real
config (tests/fixtures/templates.yml) and the code that uses them.

What is checked
---------------
1. **fixture_tokens_declared** — Every ``{token}`` used inside the fixture's
   own ``paths`` definitions must be declared in the fixture's ``keys``
   section (or be a standard KEY_FORMATS key in tk_config.py).  Missing keys
   mean a template that cannot be resolved will silently produce an error.

2. **bad_token_names** — Tokens listed in TK_API.md's INCORRECT section
   (``{shot_name}``, ``{asset_name}``, etc.) must NOT appear anywhere in the
   fixture templates' ``paths`` definitions.  Guards against docs being copied
   verbatim into templates.

3. **alias_no_at_residue** — After alias expansion every resolved template
   string must contain no residual ``@`` character (unresolved alias).

4. **duplicate_definitions** — No template name appears more than once in the
   fixture file text.  YAML silently keeps the last value on duplicate keys;
   this check surfaces the silent collision.

5. **candidate_templates_match** — For the four core DCC/entity combinations
   that ``toolkit_tools.py`` resolves via the naming convention
   ``{ptype}_{entity}_publish``, at least one matching template must exist
   in the fixture.

6. **fixture_templates_documented** — Every template in the fixture must be
   mentioned in TK_API.md.  Catches fixture templates that were added without
   updating the RAG-indexed documentation.

7. **doc_section_core_templates_present** — The core templates that the tests
   rely on (maya_asset_work, maya_asset_publish, maya_shot_work,
   maya_shot_publish, nuke_shot_work, nuke_shot_publish, asset_alembic_cache,
   flame_shot_batch) must all appear in TK_API.md.

Exit codes
----------
0  all checks passed (or --strict not set + failures found)
1  at least one check failed AND --strict is set
2  invalid CLI usage

Usage
-----
    python scripts/verify_templates.py
    python scripts/verify_templates.py --verbose
    python scripts/verify_templates.py --strict
    python scripts/verify_templates.py --help
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

# Hard-coded paths relative to REPO_ROOT
FIXTURE_TEMPLATES = REPO_ROOT / "tests" / "fixtures" / "templates.yml"
TK_API_DOC = REPO_ROOT / "src" / "fpt_mcp" / "docs" / "TK_API.md"

# Keys defined in tk_config.py::KEY_FORMATS that are always valid even if not
# declared in a specific templates.yml (they are engine-level constants).
# We include them so that templates using these tokens pass check 1.
KEY_FORMATS_KEYS: frozenset[str] = frozenset({
    "version",
    "version_four",
    "SEQ",
    "flame.frame",
    "vred.frame",
    "iteration",
    "width",
    "height",
})

# Tokens that TK_API.md explicitly flags as INCORRECT hallucinations.
# If any appear in a fixture template path definition it is a documentation bug.
INCORRECT_TOKENS: frozenset[str] = frozenset({
    "shot_name",
    "asset_name",
    "project_name",
    "step",          # lowercase — correct form is {Step}
    "frame",
    "ext",
    "task",
    "sequence",      # lowercase — correct form is {Sequence}
    "v",
    "ver",
    "shot_code",
    "asset_code",
    "pipeline_step",
})

# Core templates that the test suite specifically exercises and that must be
# documented in TK_API.md (check 7).
CORE_TEMPLATES: frozenset[str] = frozenset({
    "maya_asset_work",
    "maya_asset_publish",
    "maya_shot_work",
    "maya_shot_publish",
    "nuke_shot_work",
    "nuke_shot_publish",
    "asset_alembic_cache",
    "flame_shot_batch",
    "flame_shot_render_exr",
    "houdini_shot_work",
    "hiero_project_work",
    "hiero_project_publish",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    """Load and return a YAML file.  Exits with code 2 on import error."""
    try:
        import yaml  # type: ignore
    except ImportError:
        print(
            "[templates] PyYAML not installed. Run: pip install pyyaml",
            file=sys.stderr,
        )
        sys.exit(2)
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _extract_template_tokens(definition: str) -> set[str]:
    """Return the set of ``{token}`` names found in a template definition string."""
    return set(re.findall(r"\{(\w[\w.]*)\}", definition))


def _get_fixture_templates(data: dict) -> dict[str, str]:
    """Return {name: raw_definition} for every true template in the fixture."""
    paths = data.get("paths", {})
    return {
        name: val["definition"]
        for name, val in paths.items()
        if isinstance(val, dict) and "definition" in val
    }


def _get_fixture_aliases(data: dict) -> dict[str, str]:
    """Return {name: value} for every alias (plain string) in paths."""
    paths = data.get("paths", {})
    return {name: val for name, val in paths.items() if isinstance(val, str)}


def _get_declared_keys(data: dict) -> set[str]:
    """Return the set of key names declared in the ``keys`` section.

    Also includes aliases declared on individual keys (e.g.
    ``nuke.output`` declares ``alias: output``).
    """
    keys_section = data.get("keys", {})
    result: set[str] = set()
    for name, meta in keys_section.items():
        result.add(name)
        if isinstance(meta, dict) and meta.get("alias"):
            result.add(str(meta["alias"]))
    return result


def _resolve_alias(definition: str, aliases: dict[str, str]) -> str:
    """Expand ``@alias`` references in *definition*, one level deep."""
    for alias_name, alias_value in aliases.items():
        definition = definition.replace(f"@{alias_name}", alias_value)
    return definition


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

Check = tuple[bool, str]  # (passed, message)


def check_fixture_tokens_declared(
    templates: dict[str, str],
    declared_keys: set[str],
) -> list[Check]:
    """Check 1: every token used in the fixture's own templates is declared in keys."""
    all_valid_keys = declared_keys | KEY_FORMATS_KEYS
    results: list[Check] = []

    for name, definition in sorted(templates.items()):
        tokens = _extract_template_tokens(definition)
        missing = tokens - all_valid_keys
        if missing:
            for t in sorted(missing):
                results.append((
                    False,
                    f"fixture_tokens_declared: template '{name}' uses token {{{t}}} "
                    f"which is not declared in the keys section"
                ))
        else:
            results.append((
                True,
                f"fixture_tokens_declared: '{name}' — all {len(tokens)} tokens declared"
            ))
    return results


def check_bad_token_names(
    templates: dict[str, str],
    aliases: dict[str, str],
) -> list[Check]:
    """Check 2: incorrect tokens must not appear in fixture path definitions."""
    all_resolved = [_resolve_alias(defn, aliases) for defn in templates.values()]
    all_combined = " ".join(all_resolved)

    found_bad: set[str] = set()
    for token in INCORRECT_TOKENS:
        if re.search(r"\{" + re.escape(token) + r"\}", all_combined):
            found_bad.add(token)

    if not found_bad:
        return [(True,
            f"bad_token_names: no incorrect tokens found in fixture definitions "
            f"(checked {len(INCORRECT_TOKENS)} patterns)")]

    return [
        (False,
         f"bad_token_names: INCORRECT token {{{token}}} found in fixture path "
         f"definitions (TK_API.md lists this as a hallucination)")
        for token in sorted(found_bad)
    ]


def check_alias_no_at_residue(
    templates: dict[str, str],
    aliases: dict[str, str],
) -> list[Check]:
    """Check 3: after alias expansion no '@' should remain in any template string."""
    bad: list[str] = []
    for name, defn in templates.items():
        resolved = _resolve_alias(defn, aliases)
        if "@" in resolved:
            bad.append(f"  {name}: {resolved!r}")

    if not bad:
        return [(True,
            f"alias_no_at_residue: all {len(templates)} templates resolve cleanly "
            f"(no residual '@')")]

    return [(False,
        f"alias_no_at_residue: {len(bad)} template(s) have unresolved '@' after "
        f"alias expansion:\n" + "\n".join(bad))]


def check_duplicate_definitions(fixture_path: Path) -> list[Check]:
    """Check 4: no template name appears more than once in the fixture file text.

    Scans only within the ``paths:`` block, looking for top-level path keys
    (indented exactly 4 spaces, not sub-keys like ``definition:``).
    """
    if not fixture_path.exists():
        return [(False,
            f"duplicate_definitions: fixture file not found: {fixture_path}")]

    text = fixture_path.read_text(encoding="utf-8")

    # Extract the paths: section text only (between 'paths:' and the next
    # top-level key or end of file).
    paths_match = re.search(r"^paths\s*:\s*\n(.*?)(?=^\S|\Z)", text, re.MULTILINE | re.DOTALL)
    if not paths_match:
        return [(True, "duplicate_definitions: no 'paths:' section found — nothing to check")]

    paths_text = paths_match.group(1)

    # In the paths section, template keys are indented exactly 2 or 4 spaces
    # and contain only alphanumeric + underscore characters (snake_case names).
    # Sub-keys like `definition`, `type`, `default`, `choices`, `format_spec`,
    # `filter_by`, `alias`, `root_name` are excluded by requiring the name to
    # look like a Toolkit template name: contains at least one underscore or
    # starts with a letter followed by several chars.
    YAML_STRUCT_KEYS = frozenset({
        "definition", "type", "default", "choices", "format_spec",
        "filter_by", "alias", "root_name", "ma", "mb", "hip", "hipnc",
        "hiplc", "nk", "psd", "aep", "wire", "vpb",
    })

    # Match lines that look like template/alias names: 2-4 space indent,
    # snake_case identifier with at least one underscore (template names always
    # have underscore separators), followed by colon.
    name_pattern = re.compile(r"^[ ]{2,4}([a-z][a-z0-9_]+):", re.MULTILINE)
    counts: dict[str, int] = {}
    for m in name_pattern.finditer(paths_text):
        name = m.group(1)
        if name in YAML_STRUCT_KEYS:
            continue
        # Only count names that look like template identifiers
        # (contain at least one underscore — aliases and templates all do)
        if "_" not in name and name not in ("iteration", "version", "output"):
            continue
        counts[name] = counts.get(name, 0) + 1

    duplicates = {name: cnt for name, cnt in counts.items() if cnt > 1}
    if not duplicates:
        return [(True,
            "duplicate_definitions: no duplicate template names found in fixture")]

    return [
        (False,
         f"duplicate_definitions: '{name}' appears {cnt} times in fixture "
         f"(YAML silently keeps last value)")
        for name, cnt in sorted(duplicates.items())
    ]


def check_candidate_templates_match(
    templates: dict[str, str],
) -> list[Check]:
    """Check 5: publish candidate templates used by toolkit_tools.py convention exist.

    toolkit_tools.py generates candidates in order:
      1. {ptype}_{entity}_publish   (e.g. maya_asset_publish)
      2. {entity}_{ptype}_publish   (e.g. asset_maya_publish)
      3. {ptype}_{entity}           (e.g. maya_asset)
    """
    STANDARD_TYPES = [
        ("maya", "asset"),
        ("maya", "shot"),
        ("nuke", "asset"),
        ("nuke", "shot"),
    ]

    results: list[Check] = []
    template_names = set(templates.keys())

    for ptype, entity in STANDARD_TYPES:
        candidates = [
            f"{ptype}_{entity}_publish",
            f"{entity}_{ptype}_publish",
            f"{ptype}_{entity}",
        ]
        matched = [c for c in candidates if c in template_names]
        if matched:
            results.append((True,
                f"candidate_templates: publish_type='{ptype}' entity='{entity}' "
                f"→ found: {matched}"))
        else:
            results.append((False,
                f"candidate_templates: no template found for publish_type='{ptype}' "
                f"entity='{entity}' (tried: {candidates})"))
    return results


def check_fixture_templates_documented(
    templates: dict[str, str],
    doc_text: str,
) -> list[Check]:
    """Check 6: every template in the fixture is mentioned in TK_API.md.

    The fixture is a representative subset; every template in it should be
    documented so the RAG index can guide the LLM to correct names.
    """
    results: list[Check] = []
    for name in sorted(templates.keys()):
        # Allow backtick form (`name`) or bullet form (- name:)
        pattern = r"(?:^|\b)`?" + re.escape(name) + r"`?"
        if re.search(pattern, doc_text, re.MULTILINE):
            results.append((True,
                f"fixture_templates_documented: '{name}' found in TK_API.md"))
        else:
            results.append((False,
                f"fixture_templates_documented: '{name}' is in the fixture "
                f"but NOT mentioned in TK_API.md — add it to the doc"))
    return results


def check_doc_section_core_templates_present(
    doc_text: str,
) -> list[Check]:
    """Check 7: the core Toolkit templates relied on by tests are documented."""
    results: list[Check] = []
    for name in sorted(CORE_TEMPLATES):
        if re.search(r"`" + re.escape(name) + r"`", doc_text):
            results.append((True,
                f"core_templates_documented: '{name}' present in TK_API.md"))
        else:
            results.append((False,
                f"core_templates_documented: core template '{name}' is missing "
                f"from TK_API.md — it must be documented for the RAG index"))
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify Toolkit pipeline templates: fixture keys, doc tokens, "
            "alias resolution, duplicate names, and publish-type naming convention."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print passing checks too (default: only failures).",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit 1 on any failure (default: report but exit 0).",
    )
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # Load sources
    # ------------------------------------------------------------------
    if not FIXTURE_TEMPLATES.exists():
        print(
            f"[templates] fixture not found: {FIXTURE_TEMPLATES}\n"
            f"            Tests require tests/fixtures/templates.yml",
            file=sys.stderr,
        )
        return 1

    if not TK_API_DOC.exists():
        print(
            f"[templates] TK_API doc not found: {TK_API_DOC}",
            file=sys.stderr,
        )
        return 1

    fixture_data = _load_yaml(FIXTURE_TEMPLATES)
    doc_text = TK_API_DOC.read_text(encoding="utf-8")

    templates = _get_fixture_templates(fixture_data)
    aliases = _get_fixture_aliases(fixture_data)
    declared_keys = _get_declared_keys(fixture_data)

    if not templates:
        print("[templates] no templates found in fixture — nothing to check", file=sys.stderr)
        return 0

    # ------------------------------------------------------------------
    # Run all checks
    # ------------------------------------------------------------------
    all_results: list[tuple[str, bool, str]] = []

    checks: list[tuple[str, list[Check]]] = [
        ("1_fixture_tokens_declared",        check_fixture_tokens_declared(templates, declared_keys)),
        ("2_bad_token_names",                check_bad_token_names(templates, aliases)),
        ("3_alias_no_at_residue",            check_alias_no_at_residue(templates, aliases)),
        ("4_duplicate_definitions",          check_duplicate_definitions(FIXTURE_TEMPLATES)),
        ("5_candidate_templates_match",      check_candidate_templates_match(templates)),
        ("6_fixture_templates_documented",   check_fixture_templates_documented(templates, doc_text)),
        ("7_core_templates_documented",      check_doc_section_core_templates_present(doc_text)),
    ]

    for check_id, sub_results in checks:
        for passed, msg in sub_results:
            all_results.append((check_id, passed, msg))

    failures = [(cid, msg) for cid, passed, msg in all_results if not passed]
    total = len(all_results)
    passed_count = total - len(failures)

    # Print results
    for check_id, passed, msg in all_results:
        if passed and not args.verbose:
            continue
        mark = "✓" if passed else "✗"
        print(f"  {mark} [{check_id}] {msg}")

    print(
        f"[templates] {passed_count}/{total} checks passed",
        file=sys.stderr,
    )

    if not failures:
        return 0

    if args.strict:
        print(
            f"[templates] STRICT: {len(failures)} failure(s) — blocking.",
            file=sys.stderr,
        )
        return 1

    print(
        f"[templates] {len(failures)} failure(s) reported (pass --strict to block commits).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
