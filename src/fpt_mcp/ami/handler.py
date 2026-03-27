"""AMI (Action Menu Item) HTTP handler for ShotGrid.

Serves the interactive console and proxies MCP tool calls to the FPT MCP server.
The proxy eliminates CORS issues — the browser only talks to this server.

Flow:  Browser  →  POST /mcp (JSON-RPC tool call)
       Handler  →  FPT MCP server (localhost:8090)
       Handler  →  Browser (JSON result)

For natural language access, use Claude Code / Claude Desktop which connects
to fpt-mcp via stdio and interprets commands automatically.

Usage:
  python -m fpt_mcp.ami.handler                  # port 8091
  python -m fpt_mcp.ami.handler --port 9091      # custom port
  python -m fpt_mcp.ami.handler --mcp-port 8090  # point to MCP server
"""

from __future__ import annotations

import argparse
import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen
from urllib.error import URLError

CONSOLE_HTML = os.path.join(os.path.dirname(__file__), "console.html")
DEFAULT_MCP_PORT = 8090


class AMIHandler(BaseHTTPRequestHandler):
    """Handles AMI requests, serves console, and proxies MCP calls."""

    mcp_port: int = DEFAULT_MCP_PORT

    def _serve_console(self):
        """Serve the console HTML."""
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        # If POST, also read form-encoded body (ShotGrid AMIs send POST)
        if self.command == "POST" and parsed.path in ("/", "/ami", "/console"):
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length:
                body = self.rfile.read(content_length).decode("utf-8")
                qs.update(parse_qs(body))

        try:
            with open(CONSOLE_HTML, "r") as f:
                html = f.read()

            # Inject AMI context if present
            ami_params = {}
            for key in ("entity_type", "selected_ids", "project_id", "project_name", "user_login"):
                if key in qs:
                    ami_params[key] = qs[key][0]
            if ami_params:
                inject = f"const AMI_CONTEXT = {json.dumps(ami_params)};"
                html = html.replace(
                    "const params = new URLSearchParams(window.location.search);",
                    f"{inject}\nconst params = new URLSearchParams(window.location.search);",
                )

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode())
        except FileNotFoundError:
            self.send_error(404, "console.html not found")

    def _proxy_mcp(self):
        """Proxy a JSON-RPC request to the MCP HTTP server."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b""

        mcp_url = f"http://127.0.0.1:{self.mcp_port}/mcp"
        try:
            req = Request(
                mcp_url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                method="POST",
            )
            with urlopen(req, timeout=30) as resp:
                resp_body = resp.read().decode("utf-8")

                # Parse SSE or plain JSON
                json_result = None
                for line in resp_body.splitlines():
                    if line.startswith("data: "):
                        data = line[6:].strip()
                        if data:
                            try:
                                json_result = json.loads(data)
                            except json.JSONDecodeError:
                                continue

                if json_result:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(json.dumps(json_result).encode())
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(resp_body.encode())

        except URLError as e:
            self._send_json_error(502, f"MCP server not reachable: {e}")
        except Exception as e:
            self._send_json_error(500, str(e))

    def _send_json_error(self, status, message):
        resp = json.dumps({
            "jsonrpc": "2.0", "id": None,
            "error": {"code": -32000, "message": message}
        })
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(resp.encode())

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/ami", "/console"):
            self._serve_console()
        elif parsed.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","service":"fpt-ami-console"}')
        else:
            self.send_error(404, "Not found. Use /ami or /console")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/ami", "/console"):
            self._serve_console()
        elif parsed.path == "/mcp":
            self._proxy_mcp()
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        print(f"[AMI] {args[0]}")


def main():
    parser = argparse.ArgumentParser(description="FPT AMI Console Server")
    parser.add_argument("--port", type=int, default=8091, help="Console HTTP port (default: 8091)")
    parser.add_argument("--mcp-port", type=int, default=8090, help="FPT MCP HTTP port (default: 8090)")
    args = parser.parse_args()

    AMIHandler.mcp_port = args.mcp_port

    server = HTTPServer(("127.0.0.1", args.port), AMIHandler)
    print(f"FPT AMI Console running on http://127.0.0.1:{args.port}")
    print(f"  → MCP proxy at /mcp → http://127.0.0.1:{args.mcp_port}/mcp")
    print(f"  → Console: http://127.0.0.1:{args.port}/ami")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
