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
    try:
        from fpt_mcp import auth

        sg = auth.sg_connection()
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

    Connects as the signed-in human via :func:`fpt_mcp.auth.sg_connection`. Queries the user's recent ``EventLogEntry`` rows
    newest-first and returns the first whose ``project`` is set. Never raises —
    any failure maps to ``None``.
    """
    if not user_login:
        return None
    try:
        from fpt_mcp import auth

        sg = auth.sg_connection()
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
