"""What happened lately, for people who weren't watching: a catch-up to be read out by an AI app, and a
week-by-week list of what shipped.

Both go through service.shown_to, so each person gets only what they may see.
"""
from datetime import date, datetime, timedelta

from . import db, plans, service
from .config import Settings

DEFAULT_DAYS = 7      # how far back a first catch-up looks
MAX_DAYS = 30
SAME_TALK = 600       # asking again within this many seconds gets the same answer, not an empty one
MAX_WEEKS = 26


def _day(stamp: str) -> date:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).date()


def _week_of(stamp: str) -> date:
    d = _day(stamp)
    return d - timedelta(days=d.weekday())


def _spoken(d: date) -> str:
    return f"{d.day} {d:%B %Y}"


def _one_line(text: str, limit: int = 240) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


# ---------- what shipped ----------

def shipped(s: Settings, user: dict, project_id: str | None = None, weeks: int = 8) -> dict:
    """Ideas that went live, newest week first. Weeks run Monday to Sunday."""
    plans.need(s, "changelog")
    weeks = max(1, min(int(weeks), MAX_WEEKS))
    start = _week_of(db.now()) - timedelta(weeks=weeks - 1)
    cards = [service.shown_to(user, c) for c in service.list_cards(s, user, project_id, ["Done / Live"])]
    links = {pid: service.live_url(s, pid) for pid in {c["project_id"] for c in cards}}
    by_week: dict[date, list] = {}
    for c in cards:
        at = c.get("shipped_at") or c["updated_at"]
        if _day(at) < start:
            continue
        item = {"id": c["id"], "title": c["title"], "summary": _one_line(c["summary"]), "from": c["author_name"], "yours": c["mine"],
                "shipped_at": at, "project_id": c["project_id"], "project_name": c["project_name"],
                "try_url": c.get("try_url") or links.get(c["project_id"], "")}
        by_week.setdefault(_week_of(at), []).append(item)
    out = [{"week_of": w.isoformat(), "items": sorted(items, key=lambda i: i["shipped_at"], reverse=True)}
           for w, items in sorted(by_week.items(), reverse=True)]
    return {"weeks": out, "count": sum(len(w["items"]) for w in out), "looked_back_weeks": weeks,
            "text": as_text(out, len({c["project_id"] for c in cards}) > 1)}


def as_text(weeks: list[dict], name_projects: bool = False) -> str:
    """The same list as plain text, for pasting into a message."""
    if not weeks:
        return "Nothing has shipped in this time."
    parts = []
    for w in weeks:
        lines = [f"Week of {_spoken(date.fromisoformat(w['week_of']))}"]
        for i in w["items"]:
            line = f"- {i['title']}"
            if name_projects:
                line += f" ({i['project_name']})"
            if i["summary"]:
                line += f": {i['summary']}"
            if i["try_url"]:
                line += f" Try it: {i['try_url']}"
            lines.append(line)
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


# ---------- catching up ----------

def _since(s: Settings, email: str, days: int) -> tuple[str, str]:
    """Where the catch-up starts, and how to say so."""
    if days:
        days = max(1, min(int(days), MAX_DAYS))
        return db.ago(days * 86400), f"the last {days} day{'s' if days != 1 else ''}"
    with db.connect(s) as c:
        r = c.execute("SELECT caught_up_at, caught_up_from FROM users WHERE email=?", (email,)).fetchone()
        last, start = (r["caught_up_at"], r["caught_up_from"]) if r else (None, None)
        if last and start and last > db.ago(SAME_TALK):
            since = start  # the same conversation asking again
        else:
            since = max(last or "", db.ago((MAX_DAYS if last else DEFAULT_DAYS) * 86400))
        c.execute("INSERT INTO users(email, caught_up_at, caught_up_from) VALUES (?,?,?) ON CONFLICT(email) DO UPDATE SET caught_up_at=excluded.caught_up_at, caught_up_from=excluded.caught_up_from",
                  (email, db.now(), since))
    return since, "since you last asked" if last else f"the last {DEFAULT_DAYS} days"


def catch_up(s: Settings, user: dict, days: int = 0) -> dict:
    """What this person would want to hear first: what waits on them, what is new, what is being built and what
    shipped. Lists hold cards as service.shown_to gives them."""
    since, label = _since(s, user["email"], days)
    cards = [service.shown_to(user, c) for c in service.list_cards(s, user)]
    mine = lambda c: service.is_receiver(user, c["project_id"])  # noqa: E731
    needs = [c for c in cards if (mine(c) and (c["status"] in ("New", "Answered") or c.get("build_ask")))
             or (c["mine"] and c["status"] == "Needs info")]
    new = [c for c in cards if c["created_at"] > since and not c["mine"] and c["status"] not in service.CLOSED]
    building = [c for c in cards if c["status"] == "In dev for testing"]
    live = [c for c in cards if c["status"] == "Done / Live" and (c.get("shipped_at") or "") > since]
    with db.connect(s) as c:
        rows = c.execute(
            f"""SELECT card_id, detail, MAX(created_at) AS at FROM activity WHERE kind='status' AND created_at > ? AND actor_email != ?
                AND project_id IN ({','.join('?' * len(user['projects']))}) GROUP BY card_id""",
            (since, user["email"], *user["projects"])).fetchall()
    moved_to = {r["card_id"]: r["detail"] for r in rows}
    moved = [c for c in cards if c["mine"] and c["id"] in moved_to and c["status"] == moved_to[c["id"]] and c["status"] not in ("Done / Live", "Needs info")]
    links = {pid: service.live_url(s, pid) for pid in {c["project_id"] for c in live}}
    for c in live:
        c["try_link"] = c.get("try_url") or links.get(c["project_id"], "")
    return {"since": since, "looked_back": label, "needs_you": needs, "new_ideas": new, "being_built": building, "shipped": live, "your_ideas_moved": moved}
