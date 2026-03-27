"""FPT-MCP Qt Console — entry point and protocol handler.

Launches a native chat window. Can be invoked:
  1. Directly:     fpt-console
  2. With args:    fpt-console --entity-type Asset --entity-id 123
  3. Via protocol:  fpt-mcp://chat?entity_type=Asset&selected_ids=123&project_id=456

The protocol handler (fpt-mcp://) is registered by setup_venv.sh which
generates a minimal macOS .app bundle pointing to this script.
"""

from __future__ import annotations

import argparse
import sys
from urllib.parse import parse_qs, urlparse

from PySide6.QtWidgets import QApplication

from .chat_window import ChatWindow


def _parse_protocol_url(url: str) -> dict:
    """Parse an fpt-mcp:// URL into context params.

    Expected format:
      fpt-mcp://chat?entity_type=Asset&selected_ids=123&project_id=456&project_name=MyProject&user_login=user
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    result = {}
    if "entity_type" in qs:
        result["entity_type"] = qs["entity_type"][0]
    if "selected_ids" in qs:
        try:
            result["entity_id"] = int(qs["selected_ids"][0])
        except (ValueError, IndexError):
            pass
    if "project_id" in qs:
        try:
            result["project_id"] = int(qs["project_id"][0])
        except (ValueError, IndexError):
            pass
    if "project_name" in qs:
        result["project_name"] = qs["project_name"][0]
    if "user_login" in qs:
        result["user_login"] = qs["user_login"][0]

    return result


def main():
    """Entry point for the Qt console."""
    parser = argparse.ArgumentParser(description="FPT-MCP Qt Console")
    parser.add_argument("--entity-type", type=str, default=None)
    parser.add_argument("--entity-id", type=int, default=None)
    parser.add_argument("--project-id", type=int, default=None)
    parser.add_argument("--project-name", type=str, default=None)
    parser.add_argument("--user-login", type=str, default=None)
    parser.add_argument("url", nargs="?", default=None,
                        help="fpt-mcp:// protocol URL (passed by macOS)")

    args, unknown = parser.parse_known_args()

    # Context from protocol URL takes priority
    ctx = {}
    # Check all args (including unknown) for a protocol URL
    all_args = [args.url] + unknown if args.url else unknown
    for arg in all_args:
        if arg and arg.startswith("fpt-mcp://"):
            ctx = _parse_protocol_url(arg)
            break

    # Fall back to explicit CLI arguments
    if not ctx:
        if args.entity_type:
            ctx["entity_type"] = args.entity_type
        if args.entity_id:
            ctx["entity_id"] = args.entity_id
        if args.project_id:
            ctx["project_id"] = args.project_id
        if args.project_name:
            ctx["project_name"] = args.project_name
        if args.user_login:
            ctx["user_login"] = args.user_login

    app = QApplication(sys.argv)
    app.setApplicationName("FPT-MCP Console")
    app.setOrganizationName("fpt-mcp")

    window = ChatWindow(
        entity_type=ctx.get("entity_type"),
        entity_id=ctx.get("entity_id"),
        project_id=ctx.get("project_id"),
        project_name=ctx.get("project_name"),
        user_login=ctx.get("user_login"),
    )
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
