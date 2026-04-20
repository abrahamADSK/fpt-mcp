#!/usr/bin/env bash
# =============================================================================
# install.sh — fpt-mcp installer
# =============================================================================
# Automates the full installation of fpt-mcp from a clean clone.
# Safe to run multiple times (idempotent).
#
# What this script does:
#   1. Verifies Python 3.10+ is available
#   2. Creates a virtual environment in .venv/ (repo root) if not present
#   3. Installs the package and all dependencies via pip install -e .
#   4. Builds the RAG index via src/fpt_mcp/rag/build_index.py
#   5. Registers (or updates) the MCP server entry in ~/.claude.json
#   6. Pre-approves MCP tools in ~/.claude/settings.json
#   7. Prints an installation summary
#
# Usage:
#   chmod +x install.sh
#   ./install.sh
#
# Tested on: macOS (12+), Ubuntu 22.04 / Debian 12
# Requires:  Python 3.10+, bash 4+ (macOS ships bash 3 — uses /usr/bin/env bash)
# =============================================================================

set -euo pipefail

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { echo -e "${CYAN}[fpt-mcp]${RESET} $*"; }
success() { echo -e "${GREEN}[fpt-mcp] ✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}[fpt-mcp] ⚠${RESET} $*"; }
error()   { echo -e "${RED}[fpt-mcp] ✗${RESET} $*" >&2; }

# ── Track results for the final summary ──────────────────────────────────────
STEPS_OK=()
STEPS_WARN=()
STEPS_ERR=()

# ── Resolve repo root (works even if script is called from another directory) ─
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"
PYPROJECT="${REPO_ROOT}/pyproject.toml"
BUILD_INDEX_MODULE="fpt_mcp.rag.build_index"
RAG_INDEX_DIR="${REPO_ROOT}/src/fpt_mcp/rag/index"
RAG_CORPUS_JSON="${REPO_ROOT}/src/fpt_mcp/rag/corpus.json"
ENV_FILE="${REPO_ROOT}/.env"
ENV_EXAMPLE="${REPO_ROOT}/.env.example"
CLAUDE_JSON="${HOME}/.claude.json"

# =============================================================================
# DOCTOR — verify install completeness without re-running the installer
# =============================================================================
# Usage: ./install.sh --doctor
#
# Sweeps the install state in 5 independent checks. Each check prints PASS,
# FAIL, WARN, or SKIP with a concrete remediation sentence. Exit code is 0 if
# all checks pass, 1 otherwise — safe to chain in CI or pre-session hooks.
#
# The doctor is designed so that a future Claude Code session opening this
# repo can run `./install.sh --doctor` as a Phase 0 verification step BEFORE
# attempting any smoke test against ShotGrid. This is the lesson of Chat 41:
# spending an hour diagnosing symptoms of a broken install is a waste when a
# 2-second doctor sweep would have revealed the root cause immediately.
#
# Checks:
#   1. ~/.claude.json has mcpServers.fpt-mcp with valid cwd.
#   2. .env exists and does not contain placeholder values for
#      SHOTGRID_URL, SHOTGRID_SCRIPT_NAME, SHOTGRID_SCRIPT_KEY.
#   3. Venv importability — python -c "import fpt_mcp" succeeds.
#   4. ShotGrid connectivity — if .env has real values, attempt sg.info().
#      WARN if fails, SKIP if .env has placeholders.
#   5. Qt dependencies — PySide6 importable in the venv.
#      WARN (not FAIL) if missing, with remediation.
# =============================================================================

run_doctor() {
    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "${BOLD}  fpt-mcp — doctor${RESET}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo ""
    info "Repo root : ${REPO_ROOT}"

    local venv_python="${VENV_DIR}/bin/python"
    local exit_code=0

    if [[ ! -x "${venv_python}" ]]; then
        error "Venv is missing: ${venv_python}"
        error "  Run './install.sh' to create it."
        return 1
    fi

    "${venv_python}" - "${REPO_ROOT}" "${CLAUDE_JSON}" <<'PYEOF'
"""
Doctor implementation for fpt-mcp. Each check is a single function returning a
(status, message) tuple where status is one of 'PASS', 'FAIL', 'WARN', 'SKIP'.
Messages must include a remediation sentence on FAIL so a user (or a Claude
session) can act on the report without reading the source.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(sys.argv[1])
CLAUDE_JSON = Path(sys.argv[2])
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
ENV_FILE = REPO_ROOT / ".env"

RESET = "\033[0m"
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
BOLD = "\033[1m"


def _symbol(status: str) -> str:
    return {
        "PASS": f"{GREEN}✓{RESET}",
        "FAIL": f"{RED}✗{RESET}",
        "WARN": f"{YELLOW}⚠{RESET}",
        "SKIP": f"{CYAN}·{RESET}",
    }[status]


# -- Check 1: claude.json registration --
def check_claude_json() -> tuple[str, str]:
    if not CLAUDE_JSON.is_file():
        return (
            "FAIL",
            f"{CLAUDE_JSON} does not exist. "
            f"Run ./install.sh to create the MCP server entry.",
        )
    try:
        data = json.loads(CLAUDE_JSON.read_text())
    except json.JSONDecodeError as exc:
        return ("FAIL", f"{CLAUDE_JSON} is not valid JSON ({exc}). "
                        f"Restore from a backup or run ./install.sh.")
    entry = data.get("mcpServers", {}).get("fpt-mcp")
    if not entry:
        return (
            "FAIL",
            f"~/.claude.json has no mcpServers.fpt-mcp entry. "
            f"Run ./install.sh to register it.",
        )
    entry_cwd = entry.get("cwd", "")
    if Path(entry_cwd) != REPO_ROOT:
        return (
            "WARN",
            f"mcpServers.fpt-mcp.cwd = {entry_cwd!r} but repo root is "
            f"{str(REPO_ROOT)!r}. Another fpt-mcp clone may be active; "
            f"rerun ./install.sh from THIS repo if that is wrong.",
        )
    command = entry.get("command", "")
    if "/.venv/bin/python" not in command:
        return (
            "WARN",
            f"mcpServers.fpt-mcp.command = {command!r} does not point at a "
            f"venv python. Rerun ./install.sh to regenerate the entry.",
        )
    return ("PASS", f"mcpServers.fpt-mcp points at {entry_cwd}")


# -- Check 2: .env real values --
def check_env_file() -> tuple[str, str]:
    if not ENV_FILE.is_file():
        return (
            "FAIL",
            f".env not found at {ENV_FILE}. Copy .env.example -> .env and set "
            f"SHOTGRID_URL, SHOTGRID_SCRIPT_NAME, SHOTGRID_SCRIPT_KEY.",
        )
    content = ENV_FILE.read_text(errors="replace")

    # Check each required field for placeholder patterns
    placeholders_found = []
    placeholder_patterns = {
        "SHOTGRID_URL": [
            r"your[-_]?site",
            r"your[-_]?actual[-_]?site",
            r"YOUR_SITE",
            r"<your",
            r"https?://yoursite",
        ],
        "SHOTGRID_SCRIPT_NAME": [
            r"your[-_]?script[-_]?name",
            r"your[-_]?actual[-_]?script",
            r"<your",
        ],
        "SHOTGRID_SCRIPT_KEY": [
            r"your[-_]?(script[-_]?key|key|actual)",
            r"<your",
        ],
    }

    for field, patterns in placeholder_patterns.items():
        # Find the line for this field
        for line in content.splitlines():
            if line.startswith(f"{field}="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if not value:
                    placeholders_found.append(f"{field} is empty")
                    break
                for pat in patterns:
                    if re.search(pat, value, re.IGNORECASE):
                        placeholders_found.append(f"{field} contains placeholder '{value}'")
                        break
                break

    if placeholders_found:
        return (
            "FAIL",
            f".env contains placeholder values: {'; '.join(placeholders_found)}. "
            f"Edit {ENV_FILE} with your real ShotGrid credentials. "
            f"Until then every MCP call fails with SSL CERTIFICATE_VERIFY_FAILED.",
        )
    return ("PASS", f"{ENV_FILE} present, no placeholder markers found")


def _env_has_real_values() -> bool:
    """Quick check whether .env has real (non-placeholder) SG credentials."""
    status, _ = check_env_file()
    return status == "PASS"


# -- Check 3: venv importability --
def check_venv_import() -> tuple[str, str]:
    if not VENV_PYTHON.is_file():
        return (
            "FAIL",
            f"Venv python not found at {VENV_PYTHON}. Run ./install.sh to create it.",
        )
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), "-c", "import fpt_mcp; print('ok')"],
            capture_output=True, text=True, timeout=30,
            cwd=str(REPO_ROOT),
        )
        if result.returncode == 0 and "ok" in result.stdout:
            return ("PASS", "fpt_mcp is importable from the venv")
        return (
            "FAIL",
            f"'import fpt_mcp' failed (exit {result.returncode}): "
            f"{result.stderr.strip()[:200]}. "
            f"Run: cd {REPO_ROOT} && .venv/bin/pip install -e .",
        )
    except subprocess.TimeoutExpired:
        return ("FAIL", "Import test timed out after 30s. Check venv health.")
    except Exception as exc:
        return ("FAIL", f"Import test raised {type(exc).__name__}: {exc}")


# -- Check 4: ShotGrid connectivity --
def check_sg_connectivity() -> tuple[str, str]:
    if not _env_has_real_values():
        return (
            "SKIP",
            ".env has placeholder values — skipping ShotGrid connectivity test. "
            "Fill in real credentials and rerun the doctor.",
        )
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), "-c", """
import os, sys
sys.path.insert(0, os.path.join(sys.argv[1], 'src'))
from dotenv import load_dotenv
load_dotenv(os.path.join(sys.argv[1], '.env'))
import shotgun_api3
sg = shotgun_api3.Shotgun(
    os.environ['SHOTGRID_URL'],
    script_name=os.environ['SHOTGRID_SCRIPT_NAME'],
    api_key=os.environ.get('SHOTGRID_SCRIPT_KEY', os.environ.get('SHOTGRID_API_KEY', '')),
)
info = sg.info()
print(f"Connected: {info.get('title', 'unknown')} v{info.get('version', '?')}")
""", str(REPO_ROOT)],
            capture_output=True, text=True, timeout=15,
            cwd=str(REPO_ROOT),
        )
        if result.returncode == 0 and "Connected" in result.stdout:
            return ("PASS", result.stdout.strip())
        stderr = result.stderr.strip()[:200]
        return (
            "WARN",
            f"ShotGrid connection failed (exit {result.returncode}): {stderr}. "
            f"Check SHOTGRID_URL and credentials in .env.",
        )
    except subprocess.TimeoutExpired:
        return ("WARN", "ShotGrid connection timed out (15s). "
                        "Check network and SHOTGRID_URL in .env.")
    except Exception as exc:
        return ("WARN", f"ShotGrid connectivity test raised {type(exc).__name__}: {exc}")


# -- Check 5: Qt dependencies --
def check_qt_deps() -> tuple[str, str]:
    if not VENV_PYTHON.is_file():
        return ("SKIP", "Venv not found — skipping Qt check.")
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), "-c", "import PySide6; print(PySide6.__version__)"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            version = result.stdout.strip()
            return ("PASS", f"PySide6 {version} is importable")
    except Exception:
        pass

    # Try PyQt6 as fallback
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), "-c", "from PyQt6 import QtWidgets; print('ok')"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return ("PASS", "PyQt6 is importable (Qt console available)")
    except Exception:
        pass

    return (
        "WARN",
        "Neither PySide6 nor PyQt6 is importable. The Qt console (fpt-console) "
        "will not work. Install with: .venv/bin/pip install PySide6>=6.6",
    )


# -- Run all checks --
checks = [
    ("claude.json registration", check_claude_json),
    (".env credentials", check_env_file),
    ("Venv importability", check_venv_import),
    ("ShotGrid connectivity", check_sg_connectivity),
    ("Qt dependencies (PySide6)", check_qt_deps),
]

worst = "PASS"
severity = {"PASS": 0, "SKIP": 1, "WARN": 2, "FAIL": 3}

print()
for label, fn in checks:
    status, msg = fn()
    sym = _symbol(status)
    print(f"  {sym} {BOLD}{label}{RESET}: {msg}")
    if severity.get(status, 0) > severity.get(worst, 0):
        worst = status
print()

if worst == "PASS":
    print(f"  {GREEN}{BOLD}All checks passed.{RESET}")
elif worst == "WARN":
    print(f"  {YELLOW}{BOLD}Some warnings — review above.{RESET}")
elif worst == "FAIL":
    print(f"  {RED}{BOLD}One or more checks failed — fix before using fpt-mcp.{RESET}")
else:
    print(f"  {CYAN}{BOLD}Some checks skipped.{RESET}")

print()
sys.exit(1 if worst == "FAIL" else 0)
PYEOF
    return $?
}

# ── Argument parsing ─────────────────────────────────────────────────────────
if [[ $# -gt 0 ]]; then
    case "$1" in
        --doctor|-d)
            run_doctor
            exit $?
            ;;
        --help|-h)
            cat <<'HELPEOF'
Usage: ./install.sh [--doctor]

Commands:
  (no args)       Run the full 6-step installer.
  --doctor, -d    Sanity-check the install state without reinstalling.
                  5 checks: claude.json entry, .env contents, venv
                  importability, ShotGrid connectivity, Qt dependencies.
  --help, -h      Show this help.
HELPEOF
            exit 0
            ;;
        *)
            error "Unknown argument: $1"
            echo "Usage: ./install.sh [--doctor | --help]"
            exit 1
            ;;
    esac
fi

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}  fpt-mcp — installation${RESET}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""
info "Repo root : ${REPO_ROOT}"
info "Venv dir  : ${VENV_DIR}"
echo ""

# =============================================================================
# STEP 1 — Verify Python 3.10+
# =============================================================================
info "Step 1/6 — Checking Python version..."

# Try python3 first, fall back to python
PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        ver_ok=$("$candidate" -c "
import sys
ok = sys.version_info >= (3, 10)
print('ok' if ok else 'no')
")
        if [[ "$ver_ok" == "ok" ]]; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    error "Python 3.10 or newer is required but was not found."
    error "Install it via your package manager or from https://python.org"
    STEPS_ERR+=("Python 3.10+ not found — installation aborted")
    exit 1
fi

PY_VERSION=$("$PYTHON_BIN" --version 2>&1)
success "Found ${PY_VERSION} at $(command -v "$PYTHON_BIN")"
STEPS_OK+=("Python version check passed (${PY_VERSION})")

# ── Check Ollama (optional — for local/free inference) ───────────────────────
info "Checking Ollama (optional)..."
if command -v ollama &>/dev/null; then
    OLLAMA_VERSION=$(ollama --version 2>/dev/null | head -1)
    success "Ollama found: ${OLLAMA_VERSION}"
else
    warn "Ollama not found — skip if using Anthropic cloud models."
    warn "  macOS: brew install ollama && brew services start ollama"
    warn "  Linux: https://ollama.com/download/linux"
fi

# =============================================================================
# STEP 2 — Create virtual environment in .venv/ (if not already present)
# =============================================================================
info "Step 2/6 — Setting up virtual environment..."

if [[ -d "${VENV_DIR}" && -f "${VENV_DIR}/bin/python" ]]; then
    success "Virtual environment already exists at .venv/ — skipping creation"
    STEPS_OK+=("Venv already present — skipped creation")
else
    info "Creating virtual environment at ${VENV_DIR}..."
    "$PYTHON_BIN" -m venv "${VENV_DIR}"
    success "Virtual environment created"
    STEPS_OK+=("Venv created at .venv/")
fi

# Point to the venv's python/pip from here on
VENV_PYTHON="${VENV_DIR}/bin/python"
VENV_PIP="${VENV_DIR}/bin/pip"

# =============================================================================
# STEP 3 — Install package via pip install -e .
# =============================================================================
info "Step 3/6 — Installing fpt-mcp package and dependencies..."

if [[ ! -f "${PYPROJECT}" ]]; then
    error "pyproject.toml not found at ${PYPROJECT}"
    STEPS_ERR+=("pyproject.toml missing — package not installed")
    # Non-fatal: continue so the rest of the script runs
else
    # Upgrade pip silently first to avoid resolver warnings
    "${VENV_PIP}" install --quiet --upgrade pip

    # Editable install: installs fpt-mcp + all deps declared in pyproject.toml,
    # including RAG extras (chromadb, sentence-transformers, rank-bm25).
    # Using --quiet to reduce noise; errors still propagate via set -e.
    if "${VENV_PIP}" install --quiet -e "${REPO_ROOT}"; then
        success "Package installed (pip install -e .)"
        STEPS_OK+=("Package and dependencies installed from pyproject.toml")
    else
        error "pip install -e . failed"
        STEPS_ERR+=("Package installation failed — check pip output manually")
    fi
fi

# =============================================================================
# STEP 4 — Build the RAG index (if not already present)
# =============================================================================
info "Step 4/6 — Building RAG index..."

# Check if index already exists and appears complete:
#   - RAG_INDEX_DIR must exist and contain at least one file (ChromaDB data)
#   - RAG_CORPUS_JSON must exist (BM25 corpus)
if [[ -d "${RAG_INDEX_DIR}" && "$(ls -A "${RAG_INDEX_DIR}" 2>/dev/null)" ]] && \
   [[ -f "${RAG_CORPUS_JSON}" ]]; then
    success "RAG index already present — skipping rebuild"
    info "  (delete ${RAG_INDEX_DIR} to force a rebuild)"
    STEPS_OK+=("RAG index already present — skipped rebuild")
else
    info "Running build_index (first run downloads embedding model ~570 MB)..."
    info "This may take several minutes on first install."
    # Run from repo root so the editable install resolves fpt_mcp.* correctly
    if (cd "${REPO_ROOT}" && "${VENV_PYTHON}" -m "${BUILD_INDEX_MODULE}"); then
        success "RAG index built successfully"
        STEPS_OK+=("RAG index built (src/fpt_mcp/rag/index/)")
    else
        warn "RAG index build failed — server starts but search_sg_docs will return 'index not found'"
        warn "Re-run manually: cd ${REPO_ROOT} && .venv/bin/python -m ${BUILD_INDEX_MODULE}"
        STEPS_WARN+=("RAG index build failed — run manually after install")
    fi
fi

# =============================================================================
# STEP 5 — Register MCP server in ~/.claude.json
# =============================================================================
info "Step 5/6 — Registering MCP server in ~/.claude.json..."

# Full absolute paths for the server entry.
# The package is launched via `python -m fpt_mcp.server` (editable install).
# cwd = repo root so that load_dotenv() finds .env automatically.
MCP_COMMAND="${VENV_PYTHON}"
MCP_ARGS='["-m", "fpt_mcp.server"]'
MCP_CWD="${REPO_ROOT}"
SERVER_NAME="fpt-mcp"

# ── Helper: edit ~/.claude.json with jq (preferred) or python (fallback) ─────
register_with_jq() {
    # Read existing file or start with empty object
    local existing="{}"
    if [[ -f "${CLAUDE_JSON}" ]]; then
        existing="$(cat "${CLAUDE_JSON}")"
    fi

    # Build the new server entry and merge it in
    local new_entry
    new_entry=$(jq -n \
        --arg cmd "${MCP_COMMAND}" \
        --arg cwd "${MCP_CWD}" \
        '{command: $cmd, args: ["-m", "fpt_mcp.server"], cwd: $cwd}')

    # Merge: preserve existing keys, upsert mcpServers.<SERVER_NAME>
    echo "${existing}" | jq \
        --arg name "${SERVER_NAME}" \
        --argjson entry "${new_entry}" \
        '.mcpServers[$name] = $entry' \
        > "${CLAUDE_JSON}.tmp" && mv "${CLAUDE_JSON}.tmp" "${CLAUDE_JSON}"
}

register_with_python() {
    # Python fallback — reads/writes ~/.claude.json without jq
    "${VENV_PYTHON}" - <<PYEOF
import json, os, sys

path = os.path.expanduser("${CLAUDE_JSON}")
data = {}
if os.path.isfile(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except json.JSONDecodeError:
        # File exists but is invalid JSON — back it up and start fresh
        import shutil, time
        backup = path + ".bak." + str(int(time.time()))
        shutil.copy2(path, backup)
        print(f"[fpt-mcp] Warning: ~/.claude.json was invalid JSON — backed up to {backup}")
        data = {}

data.setdefault("mcpServers", {})
data["mcpServers"]["${SERVER_NAME}"] = {
    "command": "${MCP_COMMAND}",
    "args":    ["-m", "fpt_mcp.server"],
    "cwd":     "${MCP_CWD}",
}

tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
os.replace(tmp, path)
print("[fpt-mcp] ~/.claude.json updated successfully")
PYEOF
}

# Choose jq or python depending on availability
if command -v jq &>/dev/null; then
    info "Using jq to update ~/.claude.json..."
    if register_with_jq; then
        success "MCP server registered via jq"
        STEPS_OK+=("MCP server registered in ~/.claude.json (jq)")
    else
        warn "jq update failed — falling back to Python..."
        if register_with_python; then
            success "MCP server registered via Python fallback"
            STEPS_OK+=("MCP server registered in ~/.claude.json (python fallback)")
        else
            error "Failed to register MCP server in ~/.claude.json"
            STEPS_ERR+=("MCP server registration failed — add entry manually")
        fi
    fi
else
    info "jq not found — using Python to update ~/.claude.json..."
    if register_with_python; then
        success "MCP server registered via Python"
        STEPS_OK+=("MCP server registered in ~/.claude.json (python)")
    else
        error "Failed to register MCP server in ~/.claude.json"
        STEPS_ERR+=("MCP server registration failed — add entry manually")
    fi
fi

# =============================================================================
# STEP 6 — Pre-approve MCP tools in ~/.claude/settings.json
# =============================================================================
info "Step 6/6 — Pre-approving fpt-mcp tools in ~/.claude/settings.json..."

"${VENV_PYTHON}" - <<'PYEOF'
import json, os
from pathlib import Path

# concept:install_sh_tools_list start
TOOLS = [
    "sg_find", "sg_create", "sg_update", "sg_schema",
    "sg_upload", "sg_download",
    "fpt_bulk", "fpt_reporting",
    "tk_resolve_path", "tk_publish",
    "fpt_launch_app",
    "search_sg_docs", "learn_pattern", "session_stats",
]
# concept:install_sh_tools_list end
PREFIX = "mcp__fpt-mcp__"
new_tools = {PREFIX + t for t in TOOLS}

settings_path = Path.home() / ".claude" / "settings.json"
settings_path.parent.mkdir(parents=True, exist_ok=True)

settings = {}
if settings_path.exists():
    try:
        settings = json.loads(settings_path.read_text())
    except Exception:
        pass

settings.setdefault("permissions", {}).setdefault("allow", [])
existing = set(settings["permissions"]["allow"])
merged = sorted(existing | new_tools)
new_count = len(new_tools - existing)
settings["permissions"]["allow"] = merged

tmp = str(settings_path) + ".tmp"
with open(tmp, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
os.replace(tmp, str(settings_path))
print(f"[fpt-mcp] {new_count} new tools pre-approved ({len(merged)} total in ~/.claude/settings.json)")
PYEOF

if [[ $? -eq 0 ]]; then
    success "14 fpt-mcp tools pre-approved in ~/.claude/settings.json"
    STEPS_OK+=("MCP tools pre-approved in ~/.claude/settings.json (14 tools)")
else
    warn "Tool pre-approval failed — you may see permission prompts on first use"
    STEPS_WARN+=("MCP tool pre-approval failed — run manually or approve at first prompt")
fi

# =============================================================================
# SUMMARY
# =============================================================================
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}  Installation summary${RESET}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""

if [[ ${#STEPS_OK[@]} -gt 0 ]]; then
    for msg in "${STEPS_OK[@]}"; do
        echo -e "  ${GREEN}✓${RESET} ${msg}"
    done
fi

if [[ ${#STEPS_WARN[@]} -gt 0 ]]; then
    echo ""
    for msg in "${STEPS_WARN[@]}"; do
        echo -e "  ${YELLOW}⚠${RESET} ${msg}"
    done
fi

if [[ ${#STEPS_ERR[@]} -gt 0 ]]; then
    echo ""
    for msg in "${STEPS_ERR[@]}"; do
        echo -e "  ${RED}✗${RESET} ${msg}"
    done
fi

echo ""

# ── .env credentials check (non-blocking — checked independently of steps) ───
# Creates .env from template if missing, then validates content for
# placeholder values. Even a pre-existing .env is re-checked — the installer
# was run multiple times in Chat 39 with a stale template .env and the
# first real ShotGrid call failed with SSL cert error because the check
# only looked at file existence, not content.
ENV_HAS_PLACEHOLDERS=0
if [[ ! -f "${ENV_FILE}" ]]; then
    if [[ -f "${ENV_EXAMPLE}" ]]; then
        info "Creating .env from .env.example — fill in your ShotGrid credentials."
        cp "${ENV_EXAMPLE}" "${ENV_FILE}"
        ENV_HAS_PLACEHOLDERS=1
    else
        warn ".env not found and no .env.example to copy from — create it manually."
        ENV_HAS_PLACEHOLDERS=1
    fi
fi

if [[ -f "${ENV_FILE}" ]] && [[ ${ENV_HAS_PLACEHOLDERS} -eq 0 ]]; then
    # Check existing .env for placeholder fragments from .env.example.
    # Any hit means the user never filled in real credentials.
    if grep -qE '^SHOTGRID_URL=https?://(YOUR_SITE|yoursite\.shotgrid)' "${ENV_FILE}" 2>/dev/null; then
        ENV_HAS_PLACEHOLDERS=1
    fi
    if grep -qE '^SHOTGRID_SCRIPT_NAME=your_script_name' "${ENV_FILE}" 2>/dev/null; then
        ENV_HAS_PLACEHOLDERS=1
    fi
    if grep -qE '^SHOTGRID_SCRIPT_KEY=(your_script_key|your_key)' "${ENV_FILE}" 2>/dev/null; then
        ENV_HAS_PLACEHOLDERS=1
    fi
fi

if [[ ${ENV_HAS_PLACEHOLDERS} -eq 1 ]]; then
    echo ""
    echo -e "${RED}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "${RED}${BOLD}  ⚠  ShotGrid credentials NOT configured${RESET}"
    echo -e "${RED}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "${YELLOW}Your ${BOLD}.env${RESET}${YELLOW} still holds the template placeholder values from"
    echo -e ".env.example. The installation is NOT complete until you edit it${RESET}"
    echo -e "${YELLOW}with your real ShotGrid credentials. Until then every MCP call${RESET}"
    echo -e "${YELLOW}will fail with an SSL CERTIFICATE_VERIFY_FAILED error.${RESET}"
    echo ""
    echo -e "  ${BOLD}Edit:${RESET} ${CYAN}${ENV_FILE}${RESET}"
    echo -e "  ${BOLD}Required fields:${RESET}"
    echo -e "    ${CYAN}SHOTGRID_URL${RESET}         — https://<your-site>.shotgrid.autodesk.com"
    echo -e "    ${CYAN}SHOTGRID_SCRIPT_NAME${RESET} — API script name (ShotGrid Admin → Scripts)"
    echo -e "    ${CYAN}SHOTGRID_SCRIPT_KEY${RESET}  — application key of that script"
    echo -e "    ${CYAN}SHOTGRID_PROJECT_ID${RESET}  — integer project ID (0 = no default)"
    echo ""
fi

# ── Next steps hint ───────────────────────────────────────────────────────────
if [[ ${#STEPS_ERR[@]} -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}fpt-mcp is ready.${RESET}"
    echo ""
    echo -e "  ${BOLD}Next steps:${RESET}"
    echo -e "  1. Edit ${CYAN}.env${RESET} with your ShotGrid credentials:"
    echo -e "     ${CYAN}SHOTGRID_URL${RESET}         — https://yoursite.shotgrid.autodesk.com"
    echo -e "     ${CYAN}SHOTGRID_SCRIPT_NAME${RESET} — API script name (ShotGrid Admin → Scripts)"
    echo -e "     ${CYAN}SHOTGRID_SCRIPT_KEY${RESET}  — API script key"
    echo -e "     ${CYAN}SHOTGRID_PROJECT_ID${RESET}  — integer project ID (0 = no default)"
    echo ""
    echo -e "  2. Restart Claude Code (or run ${CYAN}claude${RESET}) — fpt-mcp will appear"
    echo -e "     in your MCP server list."
    echo ""
    echo -e "  ${BOLD}Verify the entry in ~/.claude.json:${RESET}"
    if command -v jq &>/dev/null; then
        jq ".mcpServers[\"${SERVER_NAME}\"]" "${CLAUDE_JSON}" 2>/dev/null || true
    else
        "${VENV_PYTHON}" -c "
import json, os
d = json.load(open(os.path.expanduser('${CLAUDE_JSON}')))
import pprint; pprint.pprint(d.get('mcpServers', {}).get('${SERVER_NAME}', {}))
" 2>/dev/null || true
    fi
    echo ""
else
    echo -e "${RED}${BOLD}Installation completed with errors.${RESET}"
    echo -e "Review the ✗ items above and fix them before using fpt-mcp."
    echo ""
    echo -e "  ${BOLD}Manual server registration (if needed):${RESET}"
    echo -e "  Add to ${CYAN}~/.claude.json${RESET} under ${CYAN}mcpServers${RESET}:"
    echo -e '  {'
    echo -e "    \"mcpServers\": {"
    echo -e "      \"${SERVER_NAME}\": {"
    echo -e "        \"command\": \"${MCP_COMMAND}\","
    echo -e "        \"args\": [\"-m\", \"fpt_mcp.server\"],"
    echo -e "        \"cwd\": \"${MCP_CWD}\""
    echo -e "      }"
    echo -e "    }"
    echo -e '  }'
    echo ""
fi

echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""
