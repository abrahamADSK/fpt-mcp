"""AMI (Action Menu Item) HTTP handler for ShotGrid.

Serves the interactive console and routes natural language messages
through Claude Code CLI, which has fpt-mcp configured as an MCP server.
No API key needed — Claude Code handles authentication and tool calling.

Flow:  Browser  →  POST /chat {"message":"..."}
       Handler  →  claude -p "message" (Claude Code CLI)
       Claude   →  calls fpt-mcp tools as needed (via stdio MCP)
       Handler  →  Browser (Claude's response)

Usage:
  python -m fpt_mcp.ami.handler                  # port 8091
  python -m fpt_mcp.ami.handler --port 9091      # custom port
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import shutil
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

CONSOLE_HTML = os.path.join(os.path.dirname(__file__), "console.html")

# Find claude CLI
CLAUDE_BIN = shutil.which("claude") or os.path.expanduser("~/.npm-global/bin/claude")


class AMIHandler(BaseHTTPRequestHandler):
    """Serves console and routes chat through Claude Code."""

    def _serve_console(self):
        """Serve the console HTML."""
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

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

    def _handle_chat(self):
        """Route natural language through Claude Code CLI."""
        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length) if content_length else b"{}"
        try:
            req_data = json.loads(raw)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Invalid JSON"})
            return

        user_msg = req_data.get("message", "").strip()
        if not user_msg:
            self._send_json(400, {"error": "Empty message"})
            return

        # Add context if available
        context = req_data.get("context", {})
        if context:
            ctx_str = f" [Contexto ShotGrid: {json.dumps(context)}]"
            prompt = user_msg + ctx_str
        else:
            prompt = user_msg

        if not os.path.isfile(CLAUDE_BIN):
            self._send_json(500, {"error": f"Claude Code CLI not found. Install: npm install -g @anthropic-ai/claude-code"})
            return

        try:
            result = subprocess.run(
                [CLAUDE_BIN, "-p", prompt, "--output-format", "text"],
                capture_output=True,
                text=True,
                timeout=120,
                env={**os.environ, "CLAUDE_NO_TELEMETRY": "1"},
            )
            response = result.stdout.strip()
            if not response and result.stderr:
                response = f"Error: {result.stderr.strip()}"
            if not response:
                response = "Sin respuesta de Claude."

            self._send_json(200, {"text": response})

        except subprocess.TimeoutExpired:
            self._send_json(504, {"error": "Claude Code timeout (120s)"})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _send_json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/ami", "/console"):
            self._serve_console()
        elif parsed.path == "/health":
            self._send_json(200, {"status": "ok", "service": "fpt-ami-console", "claude": os.path.isfile(CLAUDE_BIN)})
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/ami", "/console"):
            self._serve_console()
        elif parsed.path == "/chat":
            self._handle_chat()
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        print(f"[AMI] {args[0]}")


def main():
    parser = argparse.ArgumentParser(description="FPT AMI Console Server")
    parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args()

    if not os.path.isfile(CLAUDE_BIN):
        print(f"⚠️  Claude Code CLI not found at {CLAUDE_BIN}")
        print("   Install: npm install -g @anthropic-ai/claude-code")

    server = HTTPServer(("127.0.0.1", args.port), AMIHandler)
    print(f"FPT AMI Console: http://127.0.0.1:{args.port}/ami")
    print(f"  → Chat via Claude Code CLI: {CLAUDE_BIN}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
