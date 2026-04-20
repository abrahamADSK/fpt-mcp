"""Regression guard for Bucket F — server.py line budget.

The Bucket F refactor (Phase 2a–2e) brought server.py from 1677 lines
down to under 700. New code should go into the focused subject modules
(models, filters, shotgrid, reporting, toolkit_tools, launcher,
rag_tools) — not back into server.py.

If this test fails, the right fix is almost always to extract the new
code into the matching subject module and import back into server.py,
NOT to raise the budget. If the budget really must grow (e.g. a new
architectural concern), raise the threshold in this file with a commit
message explaining why.
"""

from __future__ import annotations

from pathlib import Path


def test_server_py_under_line_budget():
    """server.py must stay under the line budget set by Bucket F Phase 2f."""
    BUDGET = 700  # lines — raise only with a commit explaining why.
    server_py = Path(__file__).resolve().parent.parent / "src" / "fpt_mcp" / "server.py"
    line_count = sum(1 for _ in server_py.open(encoding="utf-8"))
    assert line_count < BUDGET, (
        f"server.py has {line_count} lines, budget is {BUDGET}. "
        "Extract new code into the matching subject module "
        "(models / filters / shotgrid / reporting / toolkit_tools / "
        "launcher / rag_tools) instead of growing server.py."
    )
