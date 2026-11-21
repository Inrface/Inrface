"""How one project on this install is being used, worked out from its own database for its receivers.

Nothing here leaves the server: there is no reporting home. It counts ideas and actions, not people's
names, so it says how the project is going and not who is doing what.
"""
from datetime import datetime
from statistics import median

from . import db
from .config import Settings
from .service import ServiceError

CHANNELS = (("web", "Web app"), ("mcp", "AI app"), ("slack", "Slack"), ("api", "Other systems"))
SOURCE_CHANNEL = {"typed": "web", "chat": "web", "mcp": "mcp", "slack": "slack", "intake": "api"}
# What people do. Each is worked out from the activity log by _action.
ACTIONS = (
    ("sent", "Ideas sent"),
    ("asked", "Questions asked"),
    ("replied", "Replies and notes"),
    ("filed", "Filed"),
    ("scoped", "Scoped"),
    ("built", "Builds started"),
)
_KIND_ACTION = {"created": "sent", "comment": "replied", "answered": "replied", "scoped": "scoped", "build": "built"}
_RESPONSE = ("comment", "answered", "status", "scoped", "build", "merged")
DONE = "Done / Live"


def _hours(start: str, end: str) -> float:
    at = lambda t: datetime.fromisoformat(t.replace("Z", "+00:00"))
    return (at(end) - at(start)).total_seconds() / 3600


def _typical(values: list[float]) -> dict:
    values = [v for v in values if v >= 0]
    return {"hours": round(median(values), 1) if values else None, "count": len(values)}


def _action(kind: str, detail: str) -> str | None:
    """Asking the author a question is logged as a move to Needs info, so it is told apart from filing here."""
    if kind == "status":
        return "asked" if detail == "Needs info" else "filed"
    return _KIND_ACTION.get(kind)


def _scoped(card) -> bool:
    """Someone has written down what is in and out, or how to tell when it is done."""
    return bool(card["scope"].strip()) or card["acceptance"] not in ("", "[]")


def _person(actor: str) -> bool:
    return actor != "github" and not actor.startswith("source:")


def usage(s: Settings, user: dict, project_id: str, days: int = 30) -> dict:
    if user["projects"].get(project_id) != "receiver":
        raise ServiceError(404, "Project not found")
    project = s.projects[project_id]
    since, before = db.ago(days * 86400), db.ago(2 * days * 86400)
    with db.connect(s) as c:
        cards = c.execute(
            "SELECT id, source, author_email, status, scope, acceptance, created_at, build_started_at, merged_into FROM cards WHERE project_id=? AND created_at>=?",
            (project_id, since)).fetchall()
        previous = c.execute("SELECT COUNT(*) FROM cards WHERE project_id=? AND created_at>=? AND created_at<?", (project_id, before, since)).fetchone()[0]
        acts = c.execute(
            "SELECT card_id, actor_email, kind, detail, via, created_at FROM activity WHERE project_id=? AND created_at>=? ORDER BY created_at",
            (project_id, since)).fetchall()

    by_source = {k: 0 for k, _ in CHANNELS}
    for card in cards:
        by_source[SOURCE_CHANNEL.get(card["source"], "web")] += 1

    live = [card for card in cards if not card["merged_into"]]
    progress = [
        ("received", "Received", len(cards)),
        ("scoped", "Scoped", sum(_scoped(card) for card in live)),
        ("built", "Sent to be built", sum(bool(card["build_started_at"]) for card in live)),
        ("done", "Done", sum(card["status"] == DONE for card in live)),
    ]

    # The first thing someone other than the idea's author did with it, and when it was marked done.
    author = {card["id"]: card["author_email"] for card in cards}
    first, done = {}, {}
    for a in acts:
        cid = a["card_id"]
        if cid not in author:
            continue
        if cid not in first and a["kind"] in _RESPONSE and _person(a["actor_email"]) and a["actor_email"] != author[cid]:
            first[cid] = a["created_at"]
        if cid not in done and a["kind"] == "status" and a["detail"] == DONE:
            done[cid] = a["created_at"]
    times = {
        "first_response": _typical([_hours(card["created_at"], first[card["id"]]) for card in cards if card["id"] in first]),
        "to_build": _typical([_hours(card["created_at"], card["build_started_at"]) for card in live if card["build_started_at"]]),
        "build_to_done": _typical([_hours(card["build_started_at"], done[card["id"]]) for card in live if card["build_started_at"] and card["id"] in done]),
    }

    # Where people did each kind of thing. A build sent to three places is one build.
    where = [k for k, _ in CHANNELS if k != "api"]
    counts = {key: {ch: set() for ch in where} for key, _ in ACTIONS}
    unrecorded, givers, receivers = 0, set(), set()
    for i, a in enumerate(acts):
        actor = a["actor_email"]
        if not _person(actor):
            continue
        (receivers if actor in project.receivers else givers if actor in project.givers else set()).add(actor)
        row = _action(a["kind"], a["detail"])
        if not row:
            continue
        if a["via"] not in where:
            unrecorded += 1
            continue
        counts[row][a["via"]].add(a["card_id"] if row == "built" else i)

    return {
        "days": days,
        "since": since,
        "ideas": {"total": len(cards), "previous": previous,
                  "by_source": [{"key": k, "label": label, "count": by_source[k]} for k, label in CHANNELS]},
        "progress": [{"key": k, "label": label, "count": n} for k, label, n in progress],
        "rejected": sum(card["status"] == "Rejected" for card in live),
        "times": times,
        "actions": {
            "channels": [{"key": k, "label": label} for k, label in CHANNELS if k in where],
            "rows": [{"key": key, "label": label, "counts": {ch: len(counts[key][ch]) for ch in where}} for key, label in ACTIONS],
            "unrecorded": unrecorded,
        },
        "people": {"givers": len(givers), "receivers": len(receivers)},
    }
