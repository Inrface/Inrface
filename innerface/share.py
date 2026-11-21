"""A read-only page that shows a project's roadmap to someone without a login, such as a client or an investor.

The link holds a long random secret, and only its hash is stored. The page shows titles and, if the receiver
chose so, one-line summaries of ideas that have been filed on the roadmap, are being built or have shipped.
It never shows who an idea came from, anyone's address, the conversation, the scope, the code, or links to
the repo. Ideas that haven't been filed yet, and rejected ones, are left out.
"""
import hashlib
import html
import re
import secrets
from datetime import datetime

from . import db, plans, service
from .config import Settings
from .service import ServiceError

# What the page loads, served under the same path as the page itself, so an install that keeps a sign-in in front of
# everything else has one path to open and no more.
FILES = {"style.css": "share.css", "logo.png": "brand/logo-horizontal.png", "logo-dark.png": "brand/logo-horizontal-dark.png", "favicon.ico": "favicon.ico"}
MAX_LINKS = 20          # working links per project
EXPIRES = (7, 30, 90, 0)  # days; 0 is until it is switched off
SHIPPED_DAYS = 90
MAX_PER_COLUMN = 40
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{30,80}$")

COLUMNS = [
    ("Being built", ("In dev for testing",)),
    ("Up next", ("Immediate roadmap",)),
    ("Planned", ("6-month roadmap",)),
    ("Later", ("Far down the line",)),
]


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _row(r) -> dict:
    working = not r["revoked_at"] and not (r["expires_at"] and r["expires_at"] <= db.now())
    return {"id": r["id"], "label": r["label"], "summaries": bool(r["summaries"]), "created_at": r["created_at"], "expires_at": r["expires_at"],
            "working": working, "opens": r["opens"], "last_opened_at": r["last_opened_at"]}


def links(s: Settings, user: dict, project_id: str) -> list[dict]:
    p = service._receiver_project(s, user, project_id)
    plans.need(s, "share_links")
    with db.connect(s) as c:
        rows = c.execute("SELECT * FROM share_links WHERE project_id=? AND revoked_at IS NULL ORDER BY created_at DESC", (p.id,)).fetchall()
    return [_row(r) for r in rows]


def create(s: Settings, user: dict, project_id: str, label: str = "", days: int = 30, summaries: bool = False) -> dict:
    p = service._receiver_project(s, user, project_id)
    plans.need(s, "share_links")
    if days not in EXPIRES:
        raise ServiceError(422, "Choose how long the link works: 7, 30 or 90 days, or 0 for until you switch it off")
    label = " ".join(str(label or "").split())[:60]
    token = secrets.token_urlsafe(32)
    with db.connect(s) as c:
        n = c.execute("SELECT COUNT(*) AS n FROM share_links WHERE project_id=? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)", (p.id, db.now())).fetchone()["n"]
        room = plans.limit(s, "share_links")
        if room is not None and n >= room:
            raise ServiceError(402, plans.full(s, "share_links") + " Switch one off first.")
        if n >= MAX_LINKS:
            raise ServiceError(422, f"A project can have {MAX_LINKS} working links. Switch one off first.")
        lid = db.new_id()
        c.execute("INSERT INTO share_links(id, project_id, token_hash, label, summaries, created_by, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
                  (lid, p.id, _hash(token), label, int(bool(summaries)), user["email"], db.now(), db.ago(-days * 86400) if days else None))
        row = c.execute("SELECT * FROM share_links WHERE id=?", (lid,)).fetchone()
    service.log.info("share link for %s made by %s", p.id, user["email"])
    # The only time the link itself is given out.
    return {**_row(row), "path": "/share/" + token}


def revoke(s: Settings, user: dict, project_id: str, link_id: str) -> None:
    p = service._receiver_project(s, user, project_id)
    with db.connect(s) as c:
        done = c.execute("UPDATE share_links SET revoked_at=? WHERE id=? AND project_id=? AND revoked_at IS NULL", (db.now(), link_id, p.id)).rowcount
    if not done:
        raise ServiceError(404, "No such link")


def board(s: Settings, token: str) -> dict | None:
    """What the page shows, or None when the link is unknown, switched off or run out. Those look the same from outside."""
    if not _TOKEN.match(token or "") or not plans.has(s, "share_links"):
        return None
    with db.connect(s) as c:
        link = c.execute("SELECT * FROM share_links WHERE token_hash=?", (_hash(token),)).fetchone()
        if not link or link["revoked_at"] or (link["expires_at"] and link["expires_at"] <= db.now()):
            return None
        project = s.projects.get(link["project_id"])
        if not project:
            return None
        c.execute("UPDATE share_links SET opens=opens+1, last_opened_at=? WHERE id=?", (db.now(), link["id"]))
        rows = c.execute("""SELECT title, summary, status, flagged, shipped_at, updated_at FROM cards
                            WHERE project_id=? AND merged_into IS NULL AND erased_at IS NULL
                              AND status IN ('In dev for testing', 'Immediate roadmap', '6-month roadmap', 'Far down the line', 'Done / Live')
                            ORDER BY updated_at DESC""", (project.id,)).fetchall()

    def item(r, when: str = "") -> dict:
        # Text that had personal details taken out by machine may still hold some, so only its title is shown.
        summary = service._one_line(r["summary"])[:240] if link["summaries"] and not r["flagged"] else ""
        return {"title": service._one_line(r["title"])[:160], "summary": summary, "when": when}

    columns = [{"title": title, "items": [item(r) for r in rows if r["status"] in statuses][:MAX_PER_COLUMN]} for title, statuses in COLUMNS]
    since = db.ago(SHIPPED_DAYS * 86400)
    live = sorted((r for r in rows if r["status"] == "Done / Live" and (r["shipped_at"] or r["updated_at"]) > since),
                  key=lambda r: r["shipped_at"] or r["updated_at"], reverse=True)
    columns.append({"title": "Shipped", "items": [item(r, (r["shipped_at"] or r["updated_at"])[:10]) for r in live][:MAX_PER_COLUMN]})
    return {"project": project.name, "as_of": db.now()[:10], "columns": columns}


def _date(day: str) -> str:
    d = datetime.fromisoformat(day)
    return f"{d.day} {d:%b %Y}"


def page(s: Settings, token: str) -> str | None:
    data = board(s, token)
    if data is None:
        return None
    e = html.escape
    cols = []
    for col in data["columns"]:
        cards = "".join(
            f'<li><h3>{e(i["title"])}</h3>' + (f'<p>{e(i["summary"])}</p>' if i["summary"] else "")
            + (f'<p class="when">{e(_date(i["when"]))}</p>' if i["when"] else "") + "</li>"
            for i in col["items"]) or '<li class="none">Nothing here right now</li>'
        cols.append(f'<section><h2>{e(col["title"])} <span>{len(col["items"])}</span></h2><ul>{cards}</ul></section>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="robots" content="noindex, nofollow">'
            f'<title>{e(data["project"])} roadmap</title><link rel="icon" href="/share/-/favicon.ico" sizes="any"><link rel="stylesheet" href="/share/-/style.css"></head>'
            '<body><main><header><img class="logo light" src="/share/-/logo.png" alt="Inrface"><img class="logo dark" src="/share/-/logo-dark.png" alt="">'
            f'<h1>{e(data["project"])}</h1><p>What is being built, what is coming and what has shipped. Read-only, as of {e(_date(data["as_of"]))}.</p></header>'
            f'<div class="cols">{"".join(cols)}</div></main></body></html>')


NOT_FOUND = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
             '<meta name="robots" content="noindex, nofollow"><title>Link not working</title><link rel="stylesheet" href="/share/-/style.css"></head>'
             '<body><main><header><h1>This link isn\'t working</h1><p>It may have run out or been switched off. Ask whoever sent it for a new one.</p></header></main></body></html>')
