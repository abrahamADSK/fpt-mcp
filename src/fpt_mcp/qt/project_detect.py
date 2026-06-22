"""Console-side detection of the user's most-recent-activity ShotGrid project.

When the Qt console is launched WITHOUT a project context (e.g. from the global
ShotGrid *user* menu, which carries `user_login` but no `project_id`), the
console resolves a smart default for the session here, at launch: the project of
the user's most recent ``EventLogEntry`` — which on this site includes
``Shotgun_Attachment_View`` (opening media), so it tracks "where you were last
working", not only edits.

It runs OFF the Qt main thread (see ``chat_window._ProjectDetector``) and is
strictly best-effort: ANY problem (missing creds, network, no events) returns
``None`` so the system-prompt gate's ask-the-user path still applies. The result
is a *suggested* default the assistant must confirm — it is inferred from the
last LOGGED action and may be stale (Chat 69).
"""
from __future__ import annotations

import os


def _resolve_creds() -> dict:
    """ShotGrid creds for the detector: the repo-root ``.env`` (parsed directly),
    with ``os.environ`` taking precedence.

    The Qt console parses ``.env`` into a private dict — NOT into ``os.environ``
    (see ``qt/app.py``) — so the detector cannot rely on ``os.getenv`` alone; it
    re-reads the same ``.env`` (and still lets an exported env var win). Chat 69
    fix: without this the detector silently found no creds and never detected.
    """
    creds: dict = {}
    try:
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        env_path = os.path.join(repo_root, ".env")
        if os.path.isfile(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        creds[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    for key in ("SHOTGRID_URL", "SHOTGRID_SCRIPT_NAME", "SHOTGRID_SCRIPT_KEY"):
        v = os.environ.get(key)
        if v:
            creds[key] = v
    return creds


def resolve_page_project(page_id) -> dict | None:
    """Return ``{"id": int, "name": str}`` of the project a ShotGrid Page belongs
    to, or ``None``.

    The AMI URL fired from a project page carries ``page_id`` (a saved Page),
    NOT ``project_id``. The Page entity's ``project`` field IS the project the
    user is *currently viewing* — so this is AUTHORITATIVE (the open project),
    unlike the activity heuristic. Best-effort; never raises. (Chat 69.)
    """
    if not page_id:
        return None
    creds = _resolve_creds()
    url = creds.get("SHOTGRID_URL", "")
    script_name = creds.get("SHOTGRID_SCRIPT_NAME", "")
    script_key = creds.get("SHOTGRID_SCRIPT_KEY", "")
    if not (url and script_name and script_key):
        return None
    try:
        import shotgun_api3

        sg = shotgun_api3.Shotgun(url, script_name=script_name, api_key=script_key)
        page = sg.find_one("Page", [["id", "is", int(page_id)]], ["project"])
        proj = page.get("project") if page else None
        if proj and proj.get("id"):
            return {"id": int(proj["id"]), "name": proj.get("name", "") or ""}
    except Exception:
        pass
    return None


def detect_recent_project(user_login: str) -> dict | None:
    """Return ``{"id": int, "name": str}`` of the user's most recent activity
    project, or ``None``.

    Resolves ShotGrid creds via :func:`_resolve_creds` (the console keeps them
    out of ``os.environ``). Queries the user's recent ``EventLogEntry`` rows
    newest-first and returns the first whose ``project`` is set. Never raises —
    any failure maps to ``None``.
    """
    if not user_login:
        return None
    creds = _resolve_creds()
    url = creds.get("SHOTGRID_URL", "")
    script_name = creds.get("SHOTGRID_SCRIPT_NAME", "")
    script_key = creds.get("SHOTGRID_SCRIPT_KEY", "")
    if not (url and script_name and script_key):
        return None
    try:
        import shotgun_api3

        sg = shotgun_api3.Shotgun(url, script_name=script_name, api_key=script_key)
        user = sg.find_one("HumanUser", [["login", "is", user_login]], ["id"])
        if not user:
            return None
        events = sg.find(
            "EventLogEntry",
            [["user", "is", user]],
            ["project"],
            order=[{"field_name": "created_at", "direction": "desc"}],
            limit=25,
        )
        for ev in events or []:
            proj = ev.get("project")
            if proj and proj.get("id"):
                return {"id": int(proj["id"]), "name": proj.get("name", "") or ""}
        return None
    except Exception:
        # Best-effort: never let detection break console startup.
        return None
