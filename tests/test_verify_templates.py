"""
test_verify_templates.py
========================
Unit tests for scripts/verify_templates.py.

Tests the six check functions in isolation plus the main() CLI entry point.
All file I/O is done via tmp_path or in-memory data — no live ShotGrid, no
real Toolkit installation required.

Test structure mirrors test_toolkit_paths.py: class-per-check-function,
plain synchronous helpers.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Make sure scripts/ is importable (not a package, so we patch sys.path).
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import verify_templates as vt  # noqa: E402 (after sys.path manipulation)


# ---------------------------------------------------------------------------
# Shared YAML fixture helpers
# ---------------------------------------------------------------------------

MINIMAL_FIXTURE_YAML = textwrap.dedent("""\
    keys:
      Shot:
        type: str
      Asset:
        type: str
      Step:
        type: str
      sg_asset_type:
        type: str
      name:
        type: str
      version:
        type: int
        format_spec: "03"
      maya_extension:
        type: str
        choices:
          ma: Maya Ascii (.ma)
          mb: Maya Binary (.mb)
        default: ma
      Sequence:
        type: str
      segment_name:
        type: str
      flame.frame:
        type: sequence
        format_spec: "08"

    paths:
      shot_root: sequences/{Sequence}/{Shot}/{Step}
      asset_root: assets/{sg_asset_type}/{Asset}/{Step}

      maya_asset_work:
        definition: '@asset_root/work/maya/{name}.v{version}.{maya_extension}'
      maya_asset_publish:
        definition: '@asset_root/publish/maya/{name}.v{version}.{maya_extension}'
      maya_shot_work:
        definition: '@shot_root/work/maya/{name}.v{version}.{maya_extension}'
      maya_shot_publish:
        definition: '@shot_root/publish/maya/{name}.v{version}.{maya_extension}'
      nuke_shot_work:
        definition: '@shot_root/work/nuke/{name}.v{version}.nk'
      nuke_shot_publish:
        definition: '@shot_root/publish/nuke/{name}.v{version}.nk'
      nuke_asset_publish:
        definition: '@asset_root/publish/nuke/{name}.v{version}.nk'
      asset_alembic_cache:
        definition: '@asset_root/publish/caches/{name}.v{version}.abc'
      flame_shot_batch:
        definition: 'sequences/{Sequence}/{Shot}/finishing/batch/{Shot}.v{version}.batch'
      flame_shot_render_exr:
        definition: 'sequences/{Sequence}/{Shot}/finishing/renders/{segment_name}_v{version}/{Shot}_{segment_name}_v{version}.{flame.frame}.exr'
""")

MINIMAL_TK_API_TEXT = textwrap.dedent("""\
    # Toolkit (sgtk) — Reference

    ## Standard templates

    - `maya_asset_work`: `@asset_root/work/maya/{name}.v{version}.{maya_extension}`
    - `maya_asset_publish`: `@asset_root/publish/maya/{name}.v{version}.{maya_extension}`
    - `maya_shot_work`: `@shot_root/work/maya/{name}.v{version}.{maya_extension}`
    - `maya_shot_publish`: `@shot_root/publish/maya/{name}.v{version}.{maya_extension}`
    - `nuke_shot_work`: `@shot_root/work/nuke/{name}.v{version}.nk`
    - `nuke_shot_publish`: `@shot_root/publish/nuke/{name}.v{version}.nk`
    - `nuke_asset_publish`: `@asset_root/publish/nuke/{name}.v{version}.nk`
    - `asset_alembic_cache`: `@asset_root/publish/caches/{name}.v{version}.abc`
    - `flame_shot_batch`: inline path
    - `flame_shot_render_exr`: render sequence path

    ## Token reference

    CORRECT tokens: {Shot}, {Asset}, {Step}, {Sequence}, {name}, {version}

    ### INCORRECT tokens (common hallucinations)
    - `{shot_name}` — WRONG, use `{Shot}`
    - `{asset_name}` — WRONG, use `{Asset}`
    - `{step}` — WRONG, use `{Step}`
""")


def _parse_fixture(yaml_text: str) -> dict:
    return yaml.safe_load(yaml_text) or {}


def _make_fixture_file(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "templates.yml"
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. TestFixtureTokensDeclared
# ---------------------------------------------------------------------------

class TestFixtureTokensDeclared:
    """check_fixture_tokens_declared: every {token} used in fixture paths is in keys."""

    def test_all_tokens_declared_passes(self):
        data = _parse_fixture(MINIMAL_FIXTURE_YAML)
        templates = vt._get_fixture_templates(data)
        declared_keys = vt._get_declared_keys(data)
        results = vt.check_fixture_tokens_declared(templates, declared_keys)
        failures = [msg for passed, msg in results if not passed]
        assert failures == [], f"Unexpected failures: {failures}"

    def test_missing_key_fails(self):
        # Remove 'maya_extension' from keys but keep it used in a template
        modified = textwrap.dedent("""\
            keys:
              Shot:
                type: str
              Asset:
                type: str
              Step:
                type: str
              sg_asset_type:
                type: str
              name:
                type: str
              version:
                type: int
              Sequence:
                type: str
            paths:
              asset_root: assets/{sg_asset_type}/{Asset}/{Step}
              maya_asset_work:
                definition: '@asset_root/work/maya/{name}.v{version}.{maya_extension}'
        """)
        data = _parse_fixture(modified)
        templates = vt._get_fixture_templates(data)
        declared_keys = vt._get_declared_keys(data)
        results = vt.check_fixture_tokens_declared(templates, declared_keys)
        failures = [msg for passed, msg in results if not passed]
        assert any("maya_extension" in msg for msg in failures), (
            "Expected failure for undeclared maya_extension"
        )

    def test_key_formats_keys_are_always_valid(self):
        """Tokens from KEY_FORMATS_KEYS (version, SEQ, etc.) pass without
        being in the YAML keys section."""
        yaml_text = textwrap.dedent("""\
            keys:
              Shot:
                type: str
              Sequence:
                type: str
              name:
                type: str
            paths:
              seq_root: sequences/{Sequence}/{Shot}
              shot_work_seq:
                definition: 'sequences/{Sequence}/{Shot}/renders/{name}.v{version}.{SEQ}.exr'
        """)
        data = _parse_fixture(yaml_text)
        templates = vt._get_fixture_templates(data)
        declared_keys = vt._get_declared_keys(data)
        # version and SEQ are not in the YAML keys but are in KEY_FORMATS_KEYS
        assert "version" not in declared_keys
        assert "SEQ" not in declared_keys
        results = vt.check_fixture_tokens_declared(templates, declared_keys)
        failures = [msg for passed, msg in results if not passed]
        assert failures == [], f"KEY_FORMATS_KEYS tokens should always pass: {failures}"

    def test_alias_key_accepted(self):
        """A key declaring 'alias: output' makes 'output' valid as a token."""
        yaml_text = textwrap.dedent("""\
            keys:
              Shot:
                type: str
              Sequence:
                type: str
              name:
                type: str
              nuke.output:
                alias: output
                type: str
            paths:
              shot_root: sequences/{Sequence}/{Shot}
              nuke_render:
                definition: 'sequences/{Sequence}/{Shot}/renders/{name}.{output}.exr'
        """)
        data = _parse_fixture(yaml_text)
        templates = vt._get_fixture_templates(data)
        declared_keys = vt._get_declared_keys(data)
        assert "output" in declared_keys, "alias 'output' should be extracted"
        results = vt.check_fixture_tokens_declared(templates, declared_keys)
        failures = [msg for passed, msg in results if not passed]
        assert failures == [], f"Alias token should be valid: {failures}"

    def test_empty_template_definition_passes(self):
        """Templates with no tokens (area paths) pass trivially."""
        yaml_text = textwrap.dedent("""\
            keys:
              Shot:
                type: str
              Sequence:
                type: str
            paths:
              shot_root: sequences/{Sequence}/{Shot}
              work_area:
                definition: '@shot_root/work/maya'
        """)
        data = _parse_fixture(yaml_text)
        templates = vt._get_fixture_templates(data)
        declared_keys = vt._get_declared_keys(data)
        results = vt.check_fixture_tokens_declared(templates, declared_keys)
        failures = [msg for passed, msg in results if not passed]
        assert failures == [], f"Token-free templates should pass: {failures}"


# ---------------------------------------------------------------------------
# 2. TestBadTokenNames
# ---------------------------------------------------------------------------

class TestBadTokenNames:
    """check_bad_token_names: INCORRECT tokens must not appear in fixture definitions."""

    def test_clean_fixture_passes(self):
        data = _parse_fixture(MINIMAL_FIXTURE_YAML)
        templates = vt._get_fixture_templates(data)
        aliases = vt._get_fixture_aliases(data)
        results = vt.check_bad_token_names(templates, aliases)
        assert all(passed for passed, _ in results)

    def test_lowercase_step_fails(self):
        yaml_text = textwrap.dedent("""\
            keys:
              Asset:
                type: str
              step:
                type: str
              name:
                type: str
              sg_asset_type:
                type: str
            paths:
              asset_root: assets/{sg_asset_type}/{Asset}/{step}
              bad_template:
                definition: '@asset_root/work/maya/{name}.ma'
        """)
        data = _parse_fixture(yaml_text)
        templates = vt._get_fixture_templates(data)
        aliases = vt._get_fixture_aliases(data)
        results = vt.check_bad_token_names(templates, aliases)
        failures = [msg for passed, msg in results if not passed]
        assert any("step" in msg for msg in failures), (
            "Expected failure for {step} (should be {Step})"
        )

    def test_shot_name_fails(self):
        yaml_text = textwrap.dedent("""\
            keys:
              shot_name:
                type: str
            paths:
              bad_alias: sequences/{shot_name}
              bad_tpl:
                definition: 'sequences/{shot_name}/work.ma'
        """)
        data = _parse_fixture(yaml_text)
        templates = vt._get_fixture_templates(data)
        aliases = vt._get_fixture_aliases(data)
        results = vt.check_bad_token_names(templates, aliases)
        failures = [msg for passed, msg in results if not passed]
        assert any("shot_name" in msg for msg in failures)

    @pytest.mark.parametrize("token", [
        "asset_name", "project_name", "frame", "ext", "shot_code", "asset_code",
    ])
    def test_each_incorrect_token_fails(self, token):
        yaml_text = textwrap.dedent(f"""\
            keys:
              {token}:
                type: str
            paths:
              bad_tpl:
                definition: 'work/{{{token}}}/file.ma'
        """)
        data = _parse_fixture(yaml_text)
        templates = vt._get_fixture_templates(data)
        aliases = vt._get_fixture_aliases(data)
        results = vt.check_bad_token_names(templates, aliases)
        failures = [msg for passed, msg in results if not passed]
        assert any(token in msg for msg in failures), (
            f"Expected failure for token {{{token}}}"
        )


# ---------------------------------------------------------------------------
# 3. TestAliasNoAtResidue
# ---------------------------------------------------------------------------

class TestAliasNoAtResidue:
    """check_alias_no_at_residue: after alias expansion no '@' must remain."""

    def test_clean_fixture_passes(self):
        data = _parse_fixture(MINIMAL_FIXTURE_YAML)
        templates = vt._get_fixture_templates(data)
        aliases = vt._get_fixture_aliases(data)
        results = vt.check_alias_no_at_residue(templates, aliases)
        assert all(passed for passed, _ in results)

    def test_unresolved_alias_fails(self):
        yaml_text = textwrap.dedent("""\
            keys:
              Asset:
                type: str
              name:
                type: str
            paths:
              # Note: 'asset_root' alias is NOT defined, so @asset_root stays
              broken_template:
                definition: '@asset_root/work/{name}.ma'
        """)
        data = _parse_fixture(yaml_text)
        templates = vt._get_fixture_templates(data)
        aliases = vt._get_fixture_aliases(data)
        results = vt.check_alias_no_at_residue(templates, aliases)
        failures = [msg for passed, msg in results if not passed]
        assert failures, "Expected failure for unresolved @asset_root"
        assert "@" in failures[0] or "broken_template" in failures[0]

    def test_all_aliases_resolved_passes(self):
        """Even complex multi-level aliases pass if fully expanded."""
        yaml_text = textwrap.dedent("""\
            keys:
              Shot:
                type: str
              Sequence:
                type: str
              name:
                type: str
            paths:
              shot_root: sequences/{Sequence}/{Shot}
              work_template:
                definition: '@shot_root/work/maya/{name}.ma'
        """)
        data = _parse_fixture(yaml_text)
        templates = vt._get_fixture_templates(data)
        aliases = vt._get_fixture_aliases(data)
        results = vt.check_alias_no_at_residue(templates, aliases)
        failures = [msg for passed, msg in results if not passed]
        assert failures == []


# ---------------------------------------------------------------------------
# 4. TestDuplicateDefinitions
# ---------------------------------------------------------------------------

class TestDuplicateDefinitions:
    """check_duplicate_definitions: no template name repeated in file text."""

    def test_clean_file_passes(self, tmp_path):
        fpath = _make_fixture_file(tmp_path, MINIMAL_FIXTURE_YAML)
        results = vt.check_duplicate_definitions(fpath)
        failures = [msg for passed, msg in results if not passed]
        assert failures == []

    def test_duplicate_key_detected(self, tmp_path):
        # Add the duplicate at correct 2-space indent inside paths: section
        duplicated = MINIMAL_FIXTURE_YAML.rstrip() + "\n  maya_asset_work:\n    definition: '@asset_root/work/maya/DUPLICATE.ma'\n"
        fpath = _make_fixture_file(tmp_path, duplicated)
        results = vt.check_duplicate_definitions(fpath)
        failures = [msg for passed, msg in results if not passed]
        assert any("maya_asset_work" in msg for msg in failures), (
            "Duplicate maya_asset_work should be detected"
        )

    def test_missing_file_fails(self, tmp_path):
        nonexistent = tmp_path / "nonexistent.yml"
        results = vt.check_duplicate_definitions(nonexistent)
        failures = [msg for passed, msg in results if not passed]
        assert failures, "Should fail when fixture file is missing"

    def test_two_separate_unique_keys_pass(self, tmp_path):
        yaml_text = textwrap.dedent("""\
            paths:
              template_a:
                definition: 'path/a'
              template_b:
                definition: 'path/b'
        """)
        fpath = _make_fixture_file(tmp_path, yaml_text)
        results = vt.check_duplicate_definitions(fpath)
        failures = [msg for passed, msg in results if not passed]
        assert failures == []


# ---------------------------------------------------------------------------
# 5. TestCandidateTemplatesMatch
# ---------------------------------------------------------------------------

class TestCandidateTemplatesMatch:
    """check_candidate_templates_match: {ptype}_{entity}_publish convention."""

    def test_standard_templates_present_passes(self):
        data = _parse_fixture(MINIMAL_FIXTURE_YAML)
        templates = vt._get_fixture_templates(data)
        results = vt.check_candidate_templates_match(templates)
        failures = [msg for passed, msg in results if not passed]
        assert failures == [], f"All standard templates should be found: {failures}"

    def test_missing_maya_asset_publish_fails(self):
        yaml_text = textwrap.dedent("""\
            keys:
              name:
                type: str
            paths:
              # maya_asset_publish intentionally absent
              maya_shot_publish:
                definition: 'shot/{name}.ma'
              nuke_asset_publish:
                definition: 'nuke/{name}.nk'
              nuke_shot_publish:
                definition: 'nuke/shot/{name}.nk'
        """)
        data = _parse_fixture(yaml_text)
        templates = vt._get_fixture_templates(data)
        results = vt.check_candidate_templates_match(templates)
        failures = [msg for passed, msg in results if not passed]
        assert any("maya" in msg and "asset" in msg for msg in failures), (
            "Expected failure for missing maya asset publish template"
        )

    def test_alternative_naming_convention_accepted(self):
        """asset_maya_publish (entity_ptype_publish) should satisfy the check."""
        yaml_text = textwrap.dedent("""\
            keys:
              name:
                type: str
            paths:
              # asset_maya_publish is the alt-convention form
              asset_maya_publish:
                definition: 'asset/{name}.ma'
              asset_maya_shot_publish:
                definition: 'shot/{name}.ma'
              nuke_asset_publish:
                definition: 'nuke/{name}.nk'
              nuke_shot_publish:
                definition: 'nuke/shot/{name}.nk'
        """)
        data = _parse_fixture(yaml_text)
        templates = vt._get_fixture_templates(data)
        results = vt.check_candidate_templates_match(templates)
        # maya entity should be satisfied by asset_maya_publish
        for passed, msg in results:
            if "maya" in msg and "asset" in msg:
                assert passed, f"Alternative name should satisfy check: {msg}"


# ---------------------------------------------------------------------------
# 6. TestFixtureTemplatesDocumented
# ---------------------------------------------------------------------------

class TestFixtureTemplatesDocumented:
    """check_fixture_templates_documented: every fixture template is in TK_API.md."""

    def test_all_in_minimal_doc_passes(self):
        data = _parse_fixture(MINIMAL_FIXTURE_YAML)
        templates = vt._get_fixture_templates(data)
        results = vt.check_fixture_templates_documented(templates, MINIMAL_TK_API_TEXT)
        failures = [msg for passed, msg in results if not passed]
        assert failures == [], f"All minimal fixture templates should be documented: {failures}"

    def test_undocumented_template_fails(self):
        yaml_text = textwrap.dedent("""\
            keys:
              name:
                type: str
            paths:
              secret_template:
                definition: 'secret/{name}.ma'
        """)
        data = _parse_fixture(yaml_text)
        templates = vt._get_fixture_templates(data)
        results = vt.check_fixture_templates_documented(templates, MINIMAL_TK_API_TEXT)
        failures = [msg for passed, msg in results if not passed]
        assert any("secret_template" in msg for msg in failures), (
            "Template absent from TK_API.md should fail"
        )

    def test_template_mentioned_in_prose_passes(self):
        """Even plain mention (not in bullet) counts as documented."""
        doc_text = "See also maya_asset_work for work file paths."
        yaml_text = textwrap.dedent("""\
            keys:
              name:
                type: str
            paths:
              maya_asset_work:
                definition: 'work/{name}.ma'
        """)
        data = _parse_fixture(yaml_text)
        templates = vt._get_fixture_templates(data)
        results = vt.check_fixture_templates_documented(templates, doc_text)
        failures = [msg for passed, msg in results if not passed]
        assert failures == []


# ---------------------------------------------------------------------------
# 7. TestCoreTemplatesDocumented
# ---------------------------------------------------------------------------

class TestCoreTemplatesDocumented:
    """check_doc_section_core_templates_present: core templates in TK_API.md."""

    def test_minimal_doc_has_all_core_templates(self):
        results = vt.check_doc_section_core_templates_present(MINIMAL_TK_API_TEXT)
        # All core templates referenced in MINIMAL_TK_API_TEXT should pass
        # (some may fail if CORE_TEMPLATES has entries not in the minimal doc)
        present = [msg for passed, msg in results if passed]
        # At least the explicitly listed ones should pass
        for name in ("maya_asset_work", "maya_asset_publish", "maya_shot_work",
                     "nuke_shot_publish", "asset_alembic_cache", "flame_shot_batch"):
            assert any(name in msg for msg in present), (
                f"'{name}' should be found in minimal TK_API doc"
            )

    def test_empty_doc_fails_all_core(self):
        results = vt.check_doc_section_core_templates_present("")
        failures = [msg for passed, msg in results if not passed]
        # All core templates should fail with empty doc
        assert len(failures) == len(vt.CORE_TEMPLATES), (
            f"Expected {len(vt.CORE_TEMPLATES)} failures, got {len(failures)}"
        )

    def test_doc_with_just_one_core_template(self):
        doc = "The `maya_asset_work` template resolves to the Maya work path."
        results = vt.check_doc_section_core_templates_present(doc)
        passed = {msg for p, msg in results if p}
        failed = {msg for p, msg in results if not p}
        # maya_asset_work should pass, all others should fail
        assert any("maya_asset_work" in m for m in passed)
        assert len(failed) == len(vt.CORE_TEMPLATES) - 1


# ---------------------------------------------------------------------------
# 8. Integration: main() CLI
# ---------------------------------------------------------------------------

class TestMainCLI:
    """Integration tests for the main() entry point."""

    def test_help_exits_zero(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            sys.argv = ["verify_templates.py", "--help"]
            vt.main()
        assert exc_info.value.code == 0

    def test_missing_fixture_exits_one(self, tmp_path, monkeypatch):
        """When the fixture does not exist, main exits 1."""
        monkeypatch.setattr(vt, "FIXTURE_TEMPLATES", tmp_path / "nonexistent.yml")
        monkeypatch.setattr(vt, "TK_API_DOC", REPO_ROOT / "src" / "fpt_mcp" / "docs" / "TK_API.md")
        sys.argv = ["verify_templates.py"]
        result = vt.main()
        assert result == 1

    def test_missing_doc_exits_one(self, tmp_path, monkeypatch):
        """When TK_API.md does not exist, main exits 1."""
        fixture = tmp_path / "templates.yml"
        fixture.write_text(MINIMAL_FIXTURE_YAML, encoding="utf-8")
        monkeypatch.setattr(vt, "FIXTURE_TEMPLATES", fixture)
        monkeypatch.setattr(vt, "TK_API_DOC", tmp_path / "nonexistent.md")
        sys.argv = ["verify_templates.py"]
        result = vt.main()
        assert result == 1

    def test_clean_fixture_and_doc_exits_zero(self, tmp_path, monkeypatch):
        """With a fully consistent fixture and doc, main returns 0."""
        fixture = tmp_path / "templates.yml"
        fixture.write_text(MINIMAL_FIXTURE_YAML, encoding="utf-8")
        doc = tmp_path / "TK_API.md"
        doc.write_text(MINIMAL_TK_API_TEXT, encoding="utf-8")

        monkeypatch.setattr(vt, "FIXTURE_TEMPLATES", fixture)
        monkeypatch.setattr(vt, "TK_API_DOC", doc)
        # Override CORE_TEMPLATES to only include what's in our minimal doc
        monkeypatch.setattr(
            vt, "CORE_TEMPLATES",
            frozenset({
                "maya_asset_work", "maya_asset_publish",
                "maya_shot_work", "maya_shot_publish",
                "nuke_shot_work", "nuke_shot_publish",
                "asset_alembic_cache", "flame_shot_batch",
                "flame_shot_render_exr",
            })
        )
        sys.argv = ["verify_templates.py"]
        result = vt.main()
        assert result == 0

    def test_strict_flag_returns_one_on_failures(self, tmp_path, monkeypatch):
        """With --strict, any failure causes exit code 1."""
        fixture = tmp_path / "templates.yml"
        # Introduce an undocumented template — must be inside paths: at 2-space indent
        bad_yaml = MINIMAL_FIXTURE_YAML.rstrip() + "\n  undocumented_tpl:\n    definition: '@asset_root/work/undocumented.ma'\n"
        fixture.write_text(bad_yaml, encoding="utf-8")
        doc = tmp_path / "TK_API.md"
        doc.write_text(MINIMAL_TK_API_TEXT, encoding="utf-8")

        monkeypatch.setattr(vt, "FIXTURE_TEMPLATES", fixture)
        monkeypatch.setattr(vt, "TK_API_DOC", doc)
        # Override CORE_TEMPLATES to avoid spurious failures unrelated to test goal
        monkeypatch.setattr(
            vt, "CORE_TEMPLATES",
            frozenset({
                "maya_asset_work", "maya_asset_publish",
                "maya_shot_work", "maya_shot_publish",
                "nuke_shot_work", "nuke_shot_publish",
                "asset_alembic_cache", "flame_shot_batch",
                "flame_shot_render_exr",
            })
        )
        sys.argv = ["verify_templates.py", "--strict"]
        result = vt.main()
        assert result == 1, "Strict mode with undocumented template should exit 1"

    def test_no_strict_returns_zero_on_failures(self, tmp_path, monkeypatch):
        """Without --strict, failures are reported but exit code is 0."""
        fixture = tmp_path / "templates.yml"
        # Introduce an undocumented template — must be inside paths: at 2-space indent
        bad_yaml = MINIMAL_FIXTURE_YAML.rstrip() + "\n  undocumented_tpl:\n    definition: '@asset_root/work/undocumented.ma'\n"
        fixture.write_text(bad_yaml, encoding="utf-8")
        doc = tmp_path / "TK_API.md"
        doc.write_text(MINIMAL_TK_API_TEXT, encoding="utf-8")

        monkeypatch.setattr(vt, "FIXTURE_TEMPLATES", fixture)
        monkeypatch.setattr(vt, "TK_API_DOC", doc)
        monkeypatch.setattr(
            vt, "CORE_TEMPLATES",
            frozenset({
                "maya_asset_work", "maya_asset_publish",
                "maya_shot_work", "maya_shot_publish",
                "nuke_shot_work", "nuke_shot_publish",
                "asset_alembic_cache", "flame_shot_batch",
                "flame_shot_render_exr",
            })
        )
        sys.argv = ["verify_templates.py"]
        result = vt.main()
        assert result == 0, "Without --strict, failures should not block (exit 0)"

    def test_empty_fixture_exits_zero_with_message(self, tmp_path, monkeypatch, capsys):
        """An empty fixture skips checks and exits 0."""
        fixture = tmp_path / "templates.yml"
        fixture.write_text("paths: {}\nkeys: {}\n", encoding="utf-8")
        doc = tmp_path / "TK_API.md"
        doc.write_text("# empty\n", encoding="utf-8")

        monkeypatch.setattr(vt, "FIXTURE_TEMPLATES", fixture)
        monkeypatch.setattr(vt, "TK_API_DOC", doc)
        sys.argv = ["verify_templates.py"]
        result = vt.main()
        assert result == 0


# ---------------------------------------------------------------------------
# 9. Unit tests for helper functions
# ---------------------------------------------------------------------------

class TestHelpers:
    """Pure-unit tests for the smaller helper functions."""

    def test_get_fixture_templates_excludes_aliases(self):
        data = _parse_fixture(MINIMAL_FIXTURE_YAML)
        templates = vt._get_fixture_templates(data)
        # shot_root and asset_root are aliases (plain strings), not templates
        assert "shot_root" not in templates
        assert "asset_root" not in templates
        # Real templates are included
        assert "maya_asset_work" in templates

    def test_get_fixture_aliases(self):
        data = _parse_fixture(MINIMAL_FIXTURE_YAML)
        aliases = vt._get_fixture_aliases(data)
        assert "shot_root" in aliases
        assert "asset_root" in aliases
        assert aliases["shot_root"] == "sequences/{Sequence}/{Shot}/{Step}"

    def test_get_declared_keys_includes_alias_key(self):
        yaml_text = textwrap.dedent("""\
            keys:
              nuke.output:
                alias: output
                type: str
              name:
                type: str
        """)
        data = _parse_fixture(yaml_text)
        keys = vt._get_declared_keys(data)
        assert "nuke.output" in keys
        assert "output" in keys  # the alias value
        assert "name" in keys

    def test_extract_template_tokens(self):
        result = vt._extract_template_tokens(
            "sequences/{Sequence}/{Shot}/{Step}/work/{name}.v{version}.ma"
        )
        assert result == {"Sequence", "Shot", "Step", "name", "version"}

    def test_extract_template_tokens_dotted(self):
        result = vt._extract_template_tokens(
            "renders/{Shot}_{segment_name}_v{version}.{flame.frame}.exr"
        )
        assert "flame.frame" in result

    def test_resolve_alias_expands_correctly(self):
        aliases = {"shot_root": "sequences/{Sequence}/{Shot}/{Step}"}
        result = vt._resolve_alias("@shot_root/work/maya/{name}.ma", aliases)
        assert result == "sequences/{Sequence}/{Shot}/{Step}/work/maya/{name}.ma"
        assert "@" not in result

    def test_resolve_alias_no_match_unchanged(self):
        aliases = {"shot_root": "sequences/{Sequence}/{Shot}/{Step}"}
        defn = "@nonexistent/work/{name}.ma"
        result = vt._resolve_alias(defn, aliases)
        assert result == defn  # unchanged — @nonexistent is not a known alias


# ---------------------------------------------------------------------------
# 10. Real fixture / real doc integration (skipped if files absent)
# ---------------------------------------------------------------------------

REAL_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "templates.yml"
REAL_TK_API = REPO_ROOT / "src" / "fpt_mcp" / "docs" / "TK_API.md"


@pytest.mark.skipif(
    not REAL_FIXTURE.exists() or not REAL_TK_API.exists(),
    reason="Real fixture or TK_API.md not available",
)
class TestRealFiles:
    """Smoke tests against the real committed files.

    These tests exercise the actual drift state of the repo.  They do NOT
    assert that all checks pass — the repo may have legitimate undocumented
    fixture templates (check 6) or other known drift.  They assert that the
    script can load and parse the real files without crashing, and that the
    core integrity checks pass.
    """

    def _load_real(self):
        data = vt._load_yaml(REAL_FIXTURE)
        doc_text = REAL_TK_API.read_text(encoding="utf-8")
        templates = vt._get_fixture_templates(data)
        aliases = vt._get_fixture_aliases(data)
        declared_keys = vt._get_declared_keys(data)
        return templates, aliases, declared_keys, doc_text

    def test_check1_no_crash(self):
        templates, _, declared_keys, _ = self._load_real()
        results = vt.check_fixture_tokens_declared(templates, declared_keys)
        assert isinstance(results, list)
        assert all(isinstance(r, tuple) and len(r) == 2 for r in results)

    def test_check1_all_pass_on_real_fixture(self):
        """All tokens used in the real fixture are declared in its keys section."""
        templates, _, declared_keys, _ = self._load_real()
        results = vt.check_fixture_tokens_declared(templates, declared_keys)
        failures = [msg for passed, msg in results if not passed]
        assert failures == [], (
            "Real fixture has undeclared token(s) — fix templates.yml or keys section:\n"
            + "\n".join(failures)
        )

    def test_check2_no_incorrect_tokens_in_real_fixture(self):
        """No hallucinated token names in the real fixture."""
        templates, aliases, _, _ = self._load_real()
        results = vt.check_bad_token_names(templates, aliases)
        failures = [msg for passed, msg in results if not passed]
        assert failures == [], (
            "Real fixture contains incorrect token names:\n" + "\n".join(failures)
        )

    def test_check3_no_at_residue_in_real_fixture(self):
        """All aliases resolve cleanly in the real fixture."""
        templates, aliases, _, _ = self._load_real()
        results = vt.check_alias_no_at_residue(templates, aliases)
        failures = [msg for passed, msg in results if not passed]
        assert failures == [], (
            "Real fixture has unresolved '@' after alias expansion:\n"
            + "\n".join(failures)
        )

    def test_check4_no_duplicates_in_real_fixture(self):
        """No duplicate template names in the real fixture file."""
        results = vt.check_duplicate_definitions(REAL_FIXTURE)
        failures = [msg for passed, msg in results if not passed]
        assert failures == [], (
            "Real fixture has duplicate template names:\n" + "\n".join(failures)
        )

    def test_check5_core_publish_candidates_present(self):
        """Core maya/nuke publish templates satisfy the candidate convention."""
        templates, _, _, _ = self._load_real()
        results = vt.check_candidate_templates_match(templates)
        failures = [msg for passed, msg in results if not passed]
        assert failures == [], (
            "Core publish templates missing from real fixture:\n"
            + "\n".join(failures)
        )

    def test_real_fixture_loads_without_exception(self):
        """Parsing the real fixture does not raise."""
        data = vt._load_yaml(REAL_FIXTURE)
        assert "paths" in data
        assert "keys" in data

    def test_real_tk_api_loads_without_exception(self):
        """Reading TK_API.md does not raise."""
        text = REAL_TK_API.read_text(encoding="utf-8")
        assert len(text) > 100
