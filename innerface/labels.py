"""Board labels: the names a team gives the places an idea can sit, in the order they want them.

A label is only a name. Each one counts as one of a few core meanings, and that is what decides what the app does
with the idea: where it is looked for, whether it is finished, whether a build can start. A team that works in
sprints can add "Next sprint" as a label that counts as Roadmap, and "QA" as one that counts as Testing. Nothing
about the labels changes what Start build or Done / Live mean.

Only the web app changes them.
"""
import json
import logging

from . import db
from .config import Project, Settings

log = logging.getLogger("innerface")

MAX_LABELS = 14
MAX_NAME = 30

# The core meanings, each with the built-in status an idea gets when it is put under a label that counts as it. These are
# the only things the app acts on. Everything else a team wants to call a place is a label.
KINDS = [
    ("New", "New"),
    ("Needs info", "Needs info"),
    ("Roadmap", "Immediate roadmap"),
    ("Someday", "Far down the line"),            # possible, but a long way off the current roadmap
    ("Active development", "In dev for testing"),
    ("Testing", "In dev for testing"),
    ("In production", "Done / Live"),
    ("Rejected", "Rejected"),
]
KIND_STATUS = dict(KINDS)
# Which meaning an idea's own status has, for finding its column.
STATUS_KIND = {"New": "New", "Answered": "New", "Needs info": "Needs info", "Immediate roadmap": "Roadmap", "6-month roadmap": "Roadmap",
               "Far down the line": "Someday", "In dev for testing": "Active development", "Done / Live": "In production", "Rejected": "Rejected"}
BUILDING = ("Active development", "Testing")

# What a project has until its team changes it. The names are what the app has always shown.
DEFAULT = [
    {"name": "New", "kind": "New"},
    {"name": "Under Review", "kind": "New"},
    {"name": "Needs info", "kind": "Needs info"},
    {"name": "Far down the line", "kind": "Someday"},
    {"name": "6-month roadmap", "kind": "Roadmap"},
    {"name": "Immediate roadmap", "kind": "Roadmap"},
    {"name": "Being built", "kind": "Active development"},
    {"name": "In Testing", "kind": "Testing"},
    {"name": "Done / Live", "kind": "In production"},
    {"name": "Rejected", "kind": "Rejected"},
]
# An idea in any of these has to have somewhere to sit, so a team can't take every label for them away.
NEEDED = ("New", "Needs info", "Active development", "In production", "Rejected")


def _error(status: int, message: str):
    from .service import ServiceError
    return ServiceError(status, message)


def _full(rows: list[dict]) -> list[dict]:
    return [{"name": x["name"], "kind": x["kind"], "status": KIND_STATUS[x["kind"]]} for x in rows]


def get(s: Settings, project_id: str) -> list[dict]:
    """The project's labels, each as {"name", "kind", "status"}."""
    with db.connect(s) as c:
        row = c.execute("SELECT labels FROM project_labels WHERE project_id=?", (project_id,)).fetchone()
    try:
        rows = json.loads(row["labels"]) if row else []
    except ValueError:
        rows = []
    out = []
    for x in rows:
        if not isinstance(x, dict) or not x.get("name"):
            continue
        # Labels saved before the meanings were slimmed down carried a built-in status instead.
        kind = x.get("kind") or STATUS_KIND.get(str(x.get("status")))
        if kind in KIND_STATUS:
            out.append({"name": str(x["name"]), "kind": kind})
    return _full(out or DEFAULT)


def resolve(s: Settings, project_id: str, name: str) -> dict | None:
    """The label of that name in this project, as {"name", "kind", "status"}, or None."""
    return next((x for x in get(s, project_id) if x["name"].lower() == name.strip().lower()), None)


def save(s: Settings, user: dict, project: Project, rows: list[dict]) -> list[dict]:
    clean, seen = [], set()
    for x in rows:
        name = " ".join(str(x.get("name", "")).split())
        kind = str(x.get("kind", ""))
        if not name or len(name) > MAX_NAME or any(ord(ch) < 32 for ch in name):
            raise _error(422, f"Each label needs a name of up to {MAX_NAME} characters")
        if kind not in KIND_STATUS:
            raise _error(422, f"{name} has to count as one of: " + ", ".join(KIND_STATUS))
        if name.lower() in seen:
            raise _error(422, f"Two labels are called {name}")
        seen.add(name.lower())
        clean.append({"name": name, "kind": kind})
    if len(clean) > MAX_LABELS:
        raise _error(422, f"A board can have up to {MAX_LABELS} labels")
    missing = [n for n in NEEDED if n not in {x["kind"] for x in clean}]
    if missing:
        raise _error(422, "Ideas need somewhere to sit when they are " + ", ".join(missing) + ". Add a label that counts as each.")
    with db.connect(s) as c:
        c.execute("INSERT INTO project_labels(project_id, labels, updated_by, updated_at) VALUES (?,?,?,?) "
                  "ON CONFLICT(project_id) DO UPDATE SET labels=excluded.labels, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                  (project.id, json.dumps(clean), user["email"], db.now()))
        # An idea whose label was taken away goes back to the first label that counts as its status.
        for r in c.execute("SELECT DISTINCT label FROM cards WHERE project_id=? AND label!=''", (project.id,)).fetchall():
            if r["label"].lower() not in seen:
                c.execute("UPDATE cards SET label='' WHERE project_id=? AND label=?", (project.id, r["label"]))
    log.info("board labels for %s changed by %s: %d labels", project.id, user["email"], len(clean))
    return _full(clean)


def reset(s: Settings, user: dict, project: Project) -> list[dict]:
    with db.connect(s) as c:
        c.execute("DELETE FROM project_labels WHERE project_id=?", (project.id,))
        c.execute("UPDATE cards SET label='' WHERE project_id=?", (project.id,))
    log.info("board labels for %s put back by %s", project.id, user["email"])
    return get(s, project.id)
