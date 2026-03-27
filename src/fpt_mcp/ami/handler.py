"""AMI (Action Menu Item) HTTP handler for ShotGrid.

Serves the interactive console and handles AMI launch URLs from ShotGrid.
Runs as a lightweight HTTP server alongside the MCP HTTP server.

ShotGrid AMI setup:
  URL: http://localhost:8091/ami
  ShotGrid passes entity_type, selected_ids, project_id, etc. as query params.

Usage:
  python -m fpt_mcp.ami.handler                  # port 8091
  python -m fpt_mcp.ami.handler --port 9091      # custom port
  python -m fpt_mcp.ami.handler --mcp-port 8090  # point to MCP server
"""

from __future__ import annotations

import argparse
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode

CONSOLE_HTML = os.path.join(os.path.dirname(__file__), "console.html")
DEFAULT_MCP_PORT = 8090


class AMIHandler(SimpleHTTPRequestHandler):
    """Handles AMI requests from ShotGrid and serves the console."""

    mcp_port: int = DEFAULT_MCP_PORT

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path in ("/", "/ami", "/console"):
            # Read AMI params from ShotGrid
            qs = parse_qs(parsed.query)
            # Build console URL with server and context params
            console_params = {
                "server": f"http://127.0.0.1:{self.mcp_port}",
            }
            # Forward ShotGrid AMI params
            for key in ("entity_type", "selected_ids", "project_id", "project_name", "user_login"):
                if key in qs:
                    console_params[key] = qs[key][0]

            # Serve console.html with injected params
            try:
                with open(CONSOLE_HTML, "r") as f:
                    html = f.read()

                # Replace the MCP_URL default with the actual server URL
                server_url = f"http://127.0.0.1:{self.mcp_port}"
                html = html.replace(
                    "const MCP_URL = new URLSearchParams(window.location.search).get('server') || 'http://127.0.0.1:8090';",
                    f"const MCP_URL = new URLSearchParams(window.location.search).get('server') || '{server_url}';",
                )

                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(html.encode())
            except FileNotFoundError:
                self.send_error(404, "console.html not found")

        elif parsed.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","service":"fpt-ami-console"}')

        else:
            self.send_error(404, "Not found. Use /ami or /console")

    def log_message(self, format, *args):
        """Prefix log messages."""
        print(f"[AMI] {args[0]}")


def main():
    parser = argparse.ArgumentParser(description="FPT AMI Console Server")
    parser.add_argument("--port", type=int, default=8091, help="Console HTTP port (default: 8091)")
    parser.add_argument("--mcp-port", type=int, default=8090, help="FPT MCP HTTP port (default: 8090)")
    args = parser.parse_args()

    AMIHandler.mcp_port = args.mcp_port

    server = HTTPServer(("127.0.0.1", args.port), AMIHandler)
    print(f"FPT AMI Console running on http://127.0.0.1:{args.port}")
    print(f"  → MCP server expected at http://127.0.0.1:{args.mcp_port}")
    print(f"  → ShotGrid AMI URL: http://YOUR_IP:{args.port}/ami")
    print(f"  → Direct console:   http://127.0.0.1:{args.port}/console")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down AMI console.")
        server.shutdown()


if __name__ == "__main__":
    main()
