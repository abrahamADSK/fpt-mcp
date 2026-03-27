"""AMI (Action Menu Item) HTTP handler for ShotGrid.

Receives AMI requests from ShotGrid and launches the native Qt console
directly via subprocess. No redirects, no browser intermediary.

Flow:  ShotGrid AMI click
       → GET http://localhost:8091/ami?entity_type=Asset&selected_ids=123
       → handler launches Qt console with context args
       → responds with minimal confirmation (ShotGrid closes the popup)

Usage:
  python -m fpt_mcp.ami.handler                  # port 8091
  python -m fpt_mcp.ami.handler --port 9091      # custom port
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs


# Locate the venv python (same venv as this process)
_VENV_PYTHON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    )))),
    ".venv", "bin", "python3",
)
if not os.path.isfile(_VENV_PYTHON):
    _VENV_PYTHON = sys.executable


CLOSE_HTML = """<!DOCTYPE html>
<html><head><title>FPT-MCP</title>
<style>body{background:#1a1a2e;color:#e0e0e0;display:flex;justify-content:center;
align-items:center;height:100vh;font-family:sans-serif;}</style>
</head><body><p>Consola abierta.</p>
<script>setTimeout(()=>window.close(),500);</script>
</body></html>"""


class AMIHandler(BaseHTTPRequestHandler):
    """Receives AMI requests and launches the Qt console."""

    def _launch_console(self, params: dict):
        """Launch the Qt console app with entity context."""
        cmd = [_VENV_PYTHON, "-m", "fpt_mcp.qt.app"]

        if params.get("entity_type"):
            cmd += ["--entity-type", params["entity_type"]]
        if params.get("selected_ids"):
            try:
                cmd += ["--entity-id", str(int(params["selected_ids"]))]
            except ValueError:
                pass
        if params.get("project_id"):
            try:
                cmd += ["--project-id", str(int(params["project_id"]))]
            except ValueError:
                pass
        if params.get("project_name"):
            cmd += ["--project-name", params["project_name"]]
        if params.get("user_login"):
            cmd += ["--user-login", params["user_login"]]

        # Launch detached — don't block the HTTP response
        subprocess.Popen(
            cmd,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)
            ))),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def _extract_params(self, parsed) -> dict:
        """Extract AMI params from query string."""
        qs = parse_qs(parsed.query)
        params = {}
        for key in ("entity_type", "selected_ids", "project_id",
                    "project_name", "user_login"):
            if key in qs:
                params[key] = qs[key][0]
        return params

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self._send_json(200, {"status": "ok", "service": "fpt-ami"})
            return

        if parsed.path in ("/", "/ami", "/console"):
            params = self._extract_params(parsed)
            self._launch_console(params)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(CLOSE_HTML.encode())
            return

        self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/ami", "/console"):
            # Read POST body (ShotGrid light payload)
            content_length = int(self.headers.get("Content-Length", 0))
            body_qs = {}
            if content_length:
                body = self.rfile.read(content_length).decode("utf-8")
                body_qs = parse_qs(body)

            # Merge query string + POST body
            params = self._extract_params(parsed)
            for key in ("entity_type", "selected_ids", "project_id",
                        "project_name", "user_login"):
                if key in body_qs and key not in params:
                    params[key] = body_qs[key][0]

            self._launch_console(params)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(CLOSE_HTML.encode())
            return

        self.send_error(404)

    def _send_json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[AMI] {args[0]}")


def main():
    parser = argparse.ArgumentParser(description="FPT AMI Handler")
    parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), AMIHandler)
    print(f"FPT AMI Handler: http://127.0.0.1:{args.port}/ami")
    print(f"  → Launches Qt console on AMI requests")
    print(f"  → Python: {_VENV_PYTHON}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
