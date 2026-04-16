"""
test_telemetry.py
=================
Bucket C — Telemetry coverage tests for fpt-mcp server.

Uses ast.parse on server.py to verify that every @mcp.tool function
properly increments _stats["exec_calls"] and tracks tokens (tokens_in
or tokens_out).

Approach: parse the AST of server.py, find all functions decorated with
@mcp.tool, and check their bodies (and the bodies of any sub-handlers
they dispatch to) for the required telemetry patterns.
"""

import ast
import pathlib

import pytest


# Path to server.py — resolved relative to this test file
_SERVER_PY = (
    pathlib.Path(__file__).parent.parent / "src" / "fpt_mcp" / "server.py"
)


def _parse_server() -> ast.Module:
    """Parse server.py and return its AST."""
    source = _SERVER_PY.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(_SERVER_PY))


def _get_tool_functions(tree: ast.Module) -> dict[str, ast.AsyncFunctionDef]:
    """Extract all @mcp.tool-decorated functions from the AST.

    Returns a dict mapping tool_name -> AST function node.
    The tool_name is extracted from @mcp.tool(name="...").
    """
    tools = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            # Match @mcp.tool(name="...")
            if isinstance(decorator, ast.Call):
                func = decorator.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "tool"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "mcp"
                ):
                    # Extract the name= keyword argument
                    tool_name = None
                    for kw in decorator.keywords:
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                            tool_name = kw.value.value
                    if tool_name is None:
                        tool_name = node.name
                    tools[tool_name] = node
    return tools


def _get_all_functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Extract all top-level function definitions from the AST.

    Returns a dict mapping function_name -> AST function node.
    """
    functions = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = node
    return functions


def _body_contains_stats_increment(
    node: ast.AST, stat_key: str
) -> bool:
    """Check if the AST node's subtree contains _stats["<stat_key>"] += ...

    Looks for augmented assignment: _stats["exec_calls"] += 1
    """
    for child in ast.walk(node):
        if isinstance(child, ast.AugAssign) and isinstance(child.op, ast.Add):
            target = child.target
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "_stats"
            ):
                # Check the subscript key
                sli = target.slice
                if isinstance(sli, ast.Constant) and sli.value == stat_key:
                    return True
    return False


def _find_dispatched_handler_names(node: ast.AST) -> list[str]:
    """Find function names that a dispatcher tool calls via await handler(...).

    Detects patterns like:
      dispatch = { Key: _do_something, ... }
      handler = dispatch[params.action]
      return await handler(...)

    Returns the list of function name strings referenced in the dispatch dict.
    """
    handler_names = []
    for child in ast.walk(node):
        # Look for dict literals containing function references
        if isinstance(child, ast.Dict):
            for val in child.values:
                if isinstance(val, ast.Name):
                    handler_names.append(val.id)
    return handler_names


# ---------------------------------------------------------------------------
# The tools that use dispatchers (fpt_bulk, fpt_reporting) implement
# telemetry in their sub-handler functions, not in the dispatcher itself.
# We track which tools are dispatchers so we can check their handlers.
# ---------------------------------------------------------------------------

_DISPATCHER_TOOLS = {"fpt_bulk", "fpt_reporting"}

# RAG tools have different telemetry patterns (rag_calls, tokens_saved)
# rather than exec_calls/tokens_in/tokens_out.
_RAG_TOOLS = {"search_sg_docs", "learn_pattern", "session_stats"}


class TestTelemetryExecCalls:
    """Every @mcp.tool function must increment _stats['exec_calls']."""

    @pytest.fixture(scope="class")
    def parsed(self):
        tree = _parse_server()
        tools = _get_tool_functions(tree)
        all_funcs = _get_all_functions(tree)
        return tree, tools, all_funcs

    def test_server_py_exists(self):
        """Sanity: server.py must be readable."""
        assert _SERVER_PY.exists(), f"server.py not found at {_SERVER_PY}"

    def test_found_mcp_tools(self, parsed):
        """We should find at least 10 @mcp.tool registrations."""
        _, tools, _ = parsed
        assert len(tools) >= 10, (
            f"Expected at least 10 tools, found {len(tools)}: "
            f"{sorted(tools.keys())}"
        )

    def test_exec_calls_coverage(self, parsed):
        """Check that each tool (or its dispatched handlers) increments
        _stats['exec_calls'].

        Reports missing tools rather than failing on first miss.
        """
        _, tools, all_funcs = parsed
        missing = []

        for tool_name, tool_node in tools.items():
            if tool_name in _RAG_TOOLS:
                # RAG tools track differently; skip exec_calls check
                continue

            # Check the tool function itself
            if _body_contains_stats_increment(tool_node, "exec_calls"):
                continue

            # For dispatchers, check their handler functions
            if tool_name in _DISPATCHER_TOOLS:
                handler_names = _find_dispatched_handler_names(tool_node)
                handlers_ok = True
                for hname in handler_names:
                    if hname in all_funcs:
                        if not _body_contains_stats_increment(
                            all_funcs[hname], "exec_calls"
                        ):
                            handlers_ok = False
                            missing.append(
                                f"{tool_name} -> {hname} (handler)"
                            )
                if handlers_ok and handler_names:
                    continue

            missing.append(tool_name)

        # Report findings
        if missing:
            pytest.fail(
                f"Tools missing _stats['exec_calls'] += 1:\n"
                + "\n".join(f"  - {m}" for m in missing)
            )


class TestTelemetryTokenTracking:
    """Every @mcp.tool function must track tokens (tokens_in or tokens_out)."""

    @pytest.fixture(scope="class")
    def parsed(self):
        tree = _parse_server()
        tools = _get_tool_functions(tree)
        all_funcs = _get_all_functions(tree)
        return tree, tools, all_funcs

    def test_token_tracking_coverage(self, parsed):
        """Check that each tool (or its dispatched handlers) tracks
        tokens_in or tokens_out.

        Reports missing tools rather than failing on first miss.
        """
        _, tools, all_funcs = parsed
        missing_in = []
        missing_out = []

        for tool_name, tool_node in tools.items():
            if tool_name in _RAG_TOOLS:
                # RAG tools track tokens_saved, not in/out
                continue

            has_in = _body_contains_stats_increment(tool_node, "tokens_in")
            has_out = _body_contains_stats_increment(tool_node, "tokens_out")

            # For dispatchers, check their handler functions too
            if tool_name in _DISPATCHER_TOOLS:
                handler_names = _find_dispatched_handler_names(tool_node)
                for hname in handler_names:
                    if hname in all_funcs:
                        if _body_contains_stats_increment(
                            all_funcs[hname], "tokens_in"
                        ):
                            has_in = True
                        if _body_contains_stats_increment(
                            all_funcs[hname], "tokens_out"
                        ):
                            has_out = True

            if not has_in:
                missing_in.append(tool_name)
            if not has_out:
                missing_out.append(tool_name)

        # Build report
        lines = []
        if missing_in:
            lines.append("Tools missing _stats['tokens_in'] tracking:")
            lines.extend(f"  - {m}" for m in missing_in)
        if missing_out:
            lines.append("Tools missing _stats['tokens_out'] tracking:")
            lines.extend(f"  - {m}" for m in missing_out)

        if lines:
            pytest.fail("\n".join(lines))


class TestTelemetryReport:
    """Generate a full telemetry audit report (informational, always passes)."""

    def test_telemetry_audit_report(self, capsys):
        """Print a detailed report of telemetry coverage per tool.

        This test always passes — it's informational output captured
        via capsys for inspection when run with -s.
        """
        tree = _parse_server()
        tools = _get_tool_functions(tree)
        all_funcs = _get_all_functions(tree)

        print("\n" + "=" * 60)
        print("TELEMETRY AUDIT REPORT")
        print("=" * 60)

        for tool_name in sorted(tools.keys()):
            tool_node = tools[tool_name]

            has_exec = _body_contains_stats_increment(tool_node, "exec_calls")
            has_in = _body_contains_stats_increment(tool_node, "tokens_in")
            has_out = _body_contains_stats_increment(tool_node, "tokens_out")
            has_rag = _body_contains_stats_increment(tool_node, "rag_calls")
            has_saved = _body_contains_stats_increment(tool_node, "tokens_saved")

            # Check dispatched handlers
            handler_info = []
            if tool_name in _DISPATCHER_TOOLS:
                handler_names = _find_dispatched_handler_names(tool_node)
                for hname in handler_names:
                    if hname in all_funcs:
                        h_exec = _body_contains_stats_increment(
                            all_funcs[hname], "exec_calls"
                        )
                        h_in = _body_contains_stats_increment(
                            all_funcs[hname], "tokens_in"
                        )
                        h_out = _body_contains_stats_increment(
                            all_funcs[hname], "tokens_out"
                        )
                        handler_info.append(
                            f"    -> {hname}: exec={h_exec} "
                            f"in={h_in} out={h_out}"
                        )
                        has_exec = has_exec or h_exec
                        has_in = has_in or h_in
                        has_out = has_out or h_out

            status = "OK" if (has_exec and has_in and has_out) else "MISSING"
            if tool_name in _RAG_TOOLS:
                status = "RAG" if (has_rag or has_saved) else "MISSING"

            print(f"\n  {tool_name} [{status}]")
            print(f"    exec_calls: {has_exec}")
            print(f"    tokens_in:  {has_in}")
            print(f"    tokens_out: {has_out}")
            if has_rag:
                print(f"    rag_calls:  {has_rag}")
            if has_saved:
                print(f"    tokens_saved: {has_saved}")
            for h in handler_info:
                print(h)

        print("\n" + "=" * 60)

        # Count totals
        total = len(tools)
        covered = 0
        for tool_name, tool_node in tools.items():
            has_exec = _body_contains_stats_increment(tool_node, "exec_calls")
            if tool_name in _DISPATCHER_TOOLS:
                handler_names = _find_dispatched_handler_names(tool_node)
                for hname in handler_names:
                    if hname in all_funcs and _body_contains_stats_increment(
                        all_funcs[hname], "exec_calls"
                    ):
                        has_exec = True
            if tool_name in _RAG_TOOLS:
                has_exec = True  # RAG tracks differently
            if has_exec:
                covered += 1

        print(f"Total tools: {total}")
        print(f"With exec_calls tracking: {covered}")
        print(f"Coverage: {covered}/{total} ({covered*100//total}%)")
        print("=" * 60 + "\n")
