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


def detect_recent_project(user_login: str) -> dict | None:
    """Return ``{"id": int, "name": str}`` of the user's most recent activity
    project, or ``None``.

    Reads ShotGrid creds from the environment (loaded from ``.env`` by the
    console). Queries the user's recent ``EventLogEntry`` rows newest-first and
    returns the first whose ``project`` is set. Never raises — any failure maps
    to ``None``.
    """
    if not user_login:
        return None
    url = os.getenv("SHOTGRID_URL", "")
    script_name = os.getenv("SHOTGRID_SCRIPT_NAME", "")
    script_key = os.getenv("SHOTGRID_SCRIPT_KEY", "")
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
