"""logging_config.py — rotating-file logging for the fpt-mcp tool boundary.

Before this module the server had ZERO logging: ``logs/timings.jsonl`` stored
only an op name + duration + error bool, the client wrappers had no
``try/except``, and production incidents were undiagnosable after the fact
(no message, no traceback, no parameter context).

This module installs a single ``RotatingFileHandler`` on the root ``fpt_mcp``
logger so every child logger (``fpt_mcp.client``, ``fpt_mcp.server`` …) writes
to one rotating file. The client wrappers log each ShotGrid operation and, on
failure, the full traceback — which is what makes an incident reconstructable.

Design constraints:
  * Idempotent: ``configure_logging()`` is safe to call from multiple import
    sites (client import, server ``main()``) without duplicating handlers.
  * Best-effort: a read-only filesystem must never crash the server, so handler
    construction is wrapped — on failure we fall back to a ``NullHandler``.
  * Sanitized: parameters are scrubbed of credential-shaped keys and truncated
    before they hit disk (a log file is lower-trust than process memory).
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
from pathlib import Path
from typing import Any

_ROOT_LOGGER_NAME = "fpt_mcp"

# Default log directory mirrors the existing runtime log location
# (``src/fpt_mcp/logs/`` already holds timings.jsonl and is gitignored).
_DEFAULT_LOG_DIR = Path(__file__).parent / "logs"

# Rotation policy: 2 MB per file, 5 backups (~12 MB ceiling). Overridable via
# env for operators who want a longer trail.
_MAX_BYTES = int(os.getenv("FPT_MCP_LOG_MAX_BYTES", str(2 * 1024 * 1024)))
_BACKUP_COUNT = int(os.getenv("FPT_MCP_LOG_BACKUP_COUNT", "5"))

# Keys whose values must never be written verbatim to a log file.
_SECRET_KEY_RE = re.compile(r"(key|secret|token|password|api_key|script_key)", re.IGNORECASE)
_MAX_VALUE_LEN = 500

_configured = False


def _resolve_log_dir() -> Path:
    """Return the log directory, honoring ``FPT_MCP_LOG_DIR`` if set."""
    override = os.getenv("FPT_MCP_LOG_DIR", "").strip()
    return Path(override) if override else _DEFAULT_LOG_DIR


def configure_logging() -> logging.Logger:
    """Install the rotating file handler on the ``fpt_mcp`` logger (idempotent).

    Returns the configured root ``fpt_mcp`` logger. Safe to call repeatedly:
    subsequent calls are no-ops once a handler is attached.
    """
    global _configured
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    if _configured:
        return logger

    level_name = os.getenv("FPT_MCP_LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))
    # Do not propagate to the root logger; we own our handler tree so the
    # stdio MCP transport (which uses stdout for protocol frames) is untouched.
    logger.propagate = False

    handler: logging.Handler
    try:
        log_dir = _resolve_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_dir / "fpt_mcp.log",
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
    except Exception:
        # Read-only FS / sandbox: never let logging setup break the server.
        handler = logging.NullHandler()

    logger.addHandler(handler)
    _configured = True
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the configured ``fpt_mcp`` root."""
    configure_logging()
    return logging.getLogger(name)


def sanitize_for_log(value: Any, _depth: int = 0) -> Any:
    """Recursively redact credential-shaped keys and truncate long values.

    A log file is a lower-trust artifact than process memory, so this strips
    anything that looks like a key/secret/token and caps string length to keep
    the trail readable and bounded.
    """
    if _depth > 6:
        return "…"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                out[k] = "***redacted***"
            else:
                out[k] = sanitize_for_log(v, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [sanitize_for_log(v, _depth + 1) for v in value][:50]
    if isinstance(value, str) and len(value) > _MAX_VALUE_LEN:
        return value[:_MAX_VALUE_LEN] + "…(truncated)"
    return value
