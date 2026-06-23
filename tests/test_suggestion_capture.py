"""Unit tests for the read-only console's improvement-suggestion capture.

The fpt-mcp Qt console spawns a `claude` subprocess with every file-mutation
tool denied (``DISALLOWED_TOOLS``), so it cannot edit the repo. Instead it is
instructed to emit ``@@SUGGESTION@@ ...`` lines, which the trusted worker pulls
out of the reply and appends to a local backlog file (the only writer of it).
These tests cover that pure capture / strip / append logic — no subprocess and
no live MCP needed.
"""

from fpt_mcp.qt.claude_worker import DISALLOWED_TOOLS, capture_suggestions


def test_captures_and_strips_single_suggestion(tmp_path):
    dest = tmp_path / "CONSOLE_IMPROVEMENTS.md"
    text = (
        "Done. Created the master Cut.\n"
        "@@SUGGESTION@@ coerce float on create :: Cut.fps fails on int 25\n"
        "Anything else?"
    )
    clean, n = capture_suggestions(text, dest=dest)
    assert n == 1
    # The marker is stripped from what the user sees, surrounding text kept.
    assert "@@SUGGESTION@@" not in clean
    assert "Done. Created the master Cut." in clean
    assert "Anything else?" in clean
    # The suggestion is persisted under a header.
    body = dest.read_text(encoding="utf-8")
    assert "coerce float on create :: Cut.fps fails on int 25" in body
    assert body.startswith("# Console improvement backlog")


def test_no_marker_leaves_text_and_writes_nothing(tmp_path):
    dest = tmp_path / "CONSOLE_IMPROVEMENTS.md"
    clean, n = capture_suggestions("Just a normal reply.", dest=dest)
    assert n == 0
    assert clean == "Just a normal reply."
    assert not dest.exists()  # nothing to log → no file created


def test_multiple_calls_append_header_only_once(tmp_path):
    dest = tmp_path / "CONSOLE_IMPROVEMENTS.md"
    capture_suggestions("@@SUGGESTION@@ one :: first idea", dest=dest)
    _, n = capture_suggestions("@@SUGGESTION@@ two :: second idea", dest=dest)
    assert n == 1
    body = dest.read_text(encoding="utf-8")
    assert body.count("# Console improvement backlog") == 1
    assert "one :: first idea" in body
    assert "two :: second idea" in body


def test_inline_marker_in_bulleted_line(tmp_path):
    dest = tmp_path / "CONSOLE_IMPROVEMENTS.md"
    clean, n = capture_suggestions(
        "- @@SUGGESTION@@ add retry :: network calls should retry", dest=dest
    )
    assert n == 1
    assert clean == ""  # the whole line was the marker → nothing left to show
    assert "add retry :: network calls should retry" in dest.read_text(encoding="utf-8")


def test_disallowed_tools_block_every_mutation_vector():
    # The lockdown must deny every file-write / shell vector the agent could
    # use to modify the repo.
    for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit", "Bash"):
        assert tool in DISALLOWED_TOOLS
