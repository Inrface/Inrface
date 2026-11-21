"""The intake API: other systems send feature ideas and per-tool feedback totals into a project.

Ideas only. Bugs, support requests and individual reviews stay in the sending system.
The sender strips personal data; `scrub` here is a backstop, not the main defence.
Reference: docs/intake-api.md
"""
import hashlib
import hmac
import re
import secrets
import threading
import time
from datetime import date, datetime, timedelta, timezone

from . import db
from .config import Settings
from .service import ServiceError, _receiver_project, fallback_title, log_activity, notify

KEY_PREFIX = "inr_"
RATE_PER_MINUTE = 60
MAX_IDEA_TEXT = 5000
EXTERNAL_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
TOOL = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")

# Generic on purpose: a sender may show these to its own users.
PUBLIC_STATUS = {
    "New": "received", "Answered": "received", "Needs info": "received",
    "Immediate roadmap": "planned", "6-month roadmap": "planned", "Far down the line": "later",
    "In dev for testing": "building", "Done / Live": "shipped", "Rejected": "declined",
}
ERASED_TEXT = "[Removed at the sender's request]"


# ---------- keys ----------

def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def create_source(s: Settings, user: dict, project_id: str, name: str) -> dict:
    """Create a source and return its key. The key is shown once and only its hash is kept."""
    project = _receiver_project(s, user, project_id)
    name = (name or "").strip()
    if not name or len(name) > 60:
        raise ServiceError(422, "Give the source a name, up to 60 characters")
    sid, secret = secrets.token_hex(4), secrets.token_urlsafe(32)
    key = f"{KEY_PREFIX}{sid}.{secret}"
    with db.connect(s) as c:
        c.execute("INSERT INTO sources(id, project_id, name, key_hash, key_hint, created_by, created_at) VALUES (?,?,?,?,?,?,?)",
                  (sid, project.id, name, _hash(secret), key[-4:], user["email"], db.now()))
    return {**_source_out(get_source(s, sid)), "key": key}


def get_source(s: Settings, source_id: str):
    with db.connect(s) as c:
        return c.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()


def _source_out(r) -> dict:
    return {"id": r["id"], "project_id": r["project_id"], "name": r["name"], "key_hint": r["key_hint"],
            "created_at": r["created_at"], "last_used_at": r["last_used_at"], "revoked": bool(r["revoked_at"])}


def list_sources(s: Settings, user: dict, project_id: str) -> list[dict]:
    project = _receiver_project(s, user, project_id)
    with db.connect(s) as c:
        rows = c.execute("SELECT * FROM sources WHERE project_id=? ORDER BY created_at", (project.id,)).fetchall()
    return [_source_out(r) for r in rows]


def revoke_source(s: Settings, user: dict, project_id: str, source_id: str) -> dict:
    project = _receiver_project(s, user, project_id)
    with db.connect(s) as c:
        n = c.execute("UPDATE sources SET revoked_at=COALESCE(revoked_at, ?) WHERE id=? AND project_id=?", (db.now(), source_id, project.id)).rowcount
    if not n:
        raise ServiceError(404, "Source not found")
    return _source_out(get_source(s, source_id))


class IntakeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


_rate: dict[str, list[float]] = {}
_rate_lock = threading.Lock()


def _allow(source_id: str) -> bool:
    now = time.monotonic()
    with _rate_lock:
        hits = [t for t in _rate.get(source_id, []) if now - t < 60]
        if len(hits) >= RATE_PER_MINUTE:
            _rate[source_id] = hits
            return False
        hits.append(now)
        _rate[source_id] = hits
        return True


def authenticate(s: Settings, authorization: str | None):
    """Return the source row for a Bearer key, or raise IntakeError."""
    m = re.fullmatch(r"Bearer\s+inr_([0-9a-f]{8})\.([A-Za-z0-9_-]{20,100})", (authorization or "").strip())
    if not m:
        raise IntakeError(401, "Missing or malformed key. Send Authorization: Bearer inr_...")
    src = get_source(s, m.group(1))
    if not src or not hmac.compare_digest(src["key_hash"], _hash(m.group(2))):
        raise IntakeError(401, "Unknown key")
    if src["revoked_at"]:
        raise IntakeError(403, "This key was revoked")
    if src["project_id"] not in s.projects:
        raise IntakeError(403, "This key's project no longer exists")
    if not _allow(src["id"]):
        raise IntakeError(429, f"Over {RATE_PER_MINUTE} requests a minute. Wait and retry.")
    with db.connect(s) as c:
        c.execute("UPDATE sources SET last_used_at=? WHERE id=?", (db.now(), src["id"]))
    return src


# ---------- scrubbing ----------

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"\b(?:https?://|www\.)([^\s/?#<>\"']+)[^\s<>\"']*", re.I)
# Seven or more digits, allowing the separators people put in phone, ID and card numbers.
_NUMBER = re.compile(r"\+?\d(?:[\s().-]?\d){6,}")


def scrub(text: str) -> tuple[str, int]:
    """Replace emails, links and long numbers. Returns (text, how many replacements)."""
    text = text.translate(_DIGITS)
    count = 0

    def sub(pattern, repl, t):
        nonlocal count
        t, n = pattern.subn(repl, t)
        count += n
        return t

    text = sub(_EMAIL, "[email]", text)
    text = sub(_URL, lambda m: f"[link: {m.group(1).lower()}]", text)
    text = sub(_NUMBER, "[number]", text)
    return text, count


# ---------- ideas ----------

def _public(card_status: str, target_status: str | None, erased: bool) -> str:
    if erased:
        return "deleted"
    return PUBLIC_STATUS.get(target_status or card_status, "received")


def _idea_out(r) -> dict:
    target = r["t_status"] if "t_status" in r.keys() else None
    t_upd = r["t_updated"] if "t_updated" in r.keys() else None
    return {
        "external_id": r["external_id"], "card_id": r["id"],
        "status": _public(r["status"], target, bool(r["erased_at"])),
        "updated_at": max(r["updated_at"], t_upd or ""),
    }


_IDEA_SELECT = """SELECT c.*, t.status AS t_status, t.updated_at AS t_updated
FROM cards c LEFT JOIN cards t ON t.id = c.merged_into"""


def submit_idea(s: Settings, src, body: dict) -> tuple[dict, bool]:
    """Create a card from an idea. Returns (response, created)."""
    project = s.projects[src["project_id"]]
    with db.connect(s) as c:
        dup = c.execute(_IDEA_SELECT + " WHERE c.source_id=? AND c.external_id=?", (src["id"], body["external_id"])).fetchone()
        if dup:
            return {**_idea_out(dup), "redactions": dup["redactions"], "flagged": bool(dup["flagged"]), "duplicate": True}, False
    text, n_text = scrub(body.get("text") or "")
    title, n_title = scrub(body.get("title") or "")
    # The docs ask for a keyed hash here. If a sender puts an address or a number in anyway, it is scrubbed too.
    author_ref, n_ref = scrub(body.get("author_ref") or "")
    redactions = n_text + n_title + n_ref
    url = body.get("url") or ""
    if not text:
        text = "This idea's text stays in " + src["name"] + ". Open the link to read it."
    title = (title.strip() or fallback_title(text))[:200]
    cid, t = db.new_id(), db.now()
    try:
        with db.connect(s) as c:
            c.execute(
                """INSERT INTO cards(id, title, original_text, source, author_email, project_id, created_at, updated_at,
                   source_id, source_name, external_id, author_ref, source_url, tool, flagged, redactions)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, title, text, "intake", f"source:{src['id']}", project.id, t, t,
                 src["id"], src["name"], body["external_id"], author_ref[:128], url, body.get("tool") or "",
                 int(redactions > 0), redactions),
            )
            log_activity(c, {"id": cid}, project.id, f"source:{src['id']}", "created", title)
    except db.sqlite3.IntegrityError:
        # Two copies of the same idea arrived at once; the other one won.
        return submit_idea(s, src, body)
    notify(s, project.receivers, f"New idea from {src['name']}", title, cid)
    return {"external_id": body["external_id"], "card_id": cid, "status": "received", "updated_at": t,
            "redactions": redactions, "flagged": redactions > 0, "duplicate": False}, True


def get_idea(s: Settings, src, external_id: str) -> dict:
    with db.connect(s) as c:
        r = c.execute(_IDEA_SELECT + " WHERE c.source_id=? AND c.external_id=?", (src["id"], external_id)).fetchone()
    if not r:
        raise IntakeError(404, "No idea with that external_id from this source")
    return _idea_out(r)


def list_ideas(s: Settings, src, updated_since: str | None, limit: int) -> dict:
    since = ""
    if updated_since:
        try:
            dt = datetime.fromisoformat(updated_since.replace("Z", "+00:00"))
        except ValueError:
            raise IntakeError(422, "updated_since must be an ISO-8601 timestamp")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        since = dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    with db.connect(s) as c:
        rows = c.execute(
            "SELECT * FROM (" + _IDEA_SELECT + " WHERE c.source_id=?) WHERE max(updated_at, COALESCE(t_updated, '')) > ? "
            "ORDER BY max(updated_at, COALESCE(t_updated, '')), id LIMIT ?",
            (src["id"], since, limit)).fetchall()
    ideas = [_idea_out(r) for r in rows]
    return {"ideas": ideas, "next_since": ideas[-1]["updated_at"] if ideas else (since or None)}


def erase_idea(s: Settings, src, external_id: str) -> None:
    """Remove what the sender gave us. Notes and scope written in Inrface stay only if a build already started."""
    with db.connect(s) as c:
        r = c.execute("SELECT * FROM cards WHERE source_id=? AND external_id=?", (src["id"], external_id)).fetchone()
        if not r:
            raise IntakeError(404, "No idea with that external_id from this source")
        if r["erased_at"]:
            return
        fields = {"original_text": ERASED_TEXT, "transcript": "[]", "author_ref": "", "source_url": "", "erased_at": db.now(), "updated_at": db.now()}
        if not r["build_started_at"]:
            fields.update(title="Removed idea", summary="", who="", problem="", scope="", open_questions="[]", acceptance="[]",
                          note="", code_check="", status="Rejected" if r["status"] != "Done / Live" else r["status"])
            c.execute("DELETE FROM comments WHERE card_id=?", (r["id"],))
            # Comments that moved to another card when this idea was merged into it.
            c.execute("DELETE FROM comments WHERE moved_from=? AND type!='merged'", (r["id"],))
            c.execute("DELETE FROM conversation_messages WHERE conversation_id IN (SELECT id FROM conversations WHERE card_id=?)", (r["id"],))
        cols = ", ".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE cards SET {cols} WHERE id=?", (*fields.values(), r["id"]))
        c.execute("UPDATE activity SET detail='' WHERE card_id=?", (r["id"],))
        # If the idea was merged into another card, its title and text were copied into a note there. The note
        # is marked with the idea it came from; notes from before that mark existed are found by their wording,
        # following the trail, since that card may itself have been merged on.
        gone = "Merged in an idea that its sender later removed."
        c.execute("UPDATE comments SET text=? WHERE type='merged' AND moved_from=?", (gone, r["id"]))
        copied, at, seen = f'Merged in "{r["title"]}": ', r["merged_into"], set()
        while at and at not in seen:
            seen.add(at)
            for n in c.execute("SELECT id, text FROM comments WHERE card_id=? AND type='note'", (at,)).fetchall():
                if n["text"].startswith(copied):
                    c.execute("UPDATE comments SET text=? WHERE id=?", (gone, n["id"]))
            c.execute("UPDATE activity SET detail='' WHERE card_id=? AND kind='merged' AND detail=?", (at, r["title"][:200]))
            nxt = c.execute("SELECT merged_into FROM cards WHERE id=?", (at,)).fetchone()
            at = nxt["merged_into"] if nxt else None
        log_activity(c, {"id": r["id"]}, r["project_id"], f"source:{src['id']}", "erased", "")


# ---------- signals ----------

def save_signals(s: Settings, src, signals: list[dict]) -> int:
    today = datetime.now(timezone.utc).date()
    rows = []
    for i, g in enumerate(signals):
        try:
            d = date.fromisoformat(g["date"])
        except ValueError:
            raise IntakeError(422, f"signals[{i}].date must be YYYY-MM-DD")
        if d > today + timedelta(days=1) or d < today - timedelta(days=800):
            raise IntakeError(422, f"signals[{i}].date is out of range")
        uses = g.get("uses")
        for f in ("failures", "reruns"):
            if g.get(f) is not None and (uses is None or g[f] > uses):
                raise IntakeError(422, f"signals[{i}].{f}: needs uses, and can't be more than uses")
        rows.append((src["id"], src["project_id"], g["tool"], d.isoformat(), g["positive"], g["negative"], g["neutral"],
                     g["rating_count"], g["rating_sum"], g["comments"], scrub(g.get("tool_label") or "")[0][:80],
                     uses, g.get("failures"), g.get("reruns"), db.now()))
    with db.connect(s) as c:
        c.executemany(
            """INSERT INTO signals(source_id, project_id, tool, day, positive, negative, neutral, rating_count, rating_sum, comments, tool_label,
                                   uses, failures, reruns, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_id, tool, day) DO UPDATE SET project_id=excluded.project_id, positive=excluded.positive,
               negative=excluded.negative, neutral=excluded.neutral, rating_count=excluded.rating_count, rating_sum=excluded.rating_sum,
               comments=excluded.comments, tool_label=excluded.tool_label,
               uses=excluded.uses, failures=excluded.failures, reruns=excluded.reruns, updated_at=excluded.updated_at""", rows)
    return len(rows)


def _rate_of(part, whole):
    return round(part / whole, 3) if part is not None and whole else None


def tool_health(s: Settings, user: dict, project_id: str, days: int = 30) -> list[dict]:
    """Per tool: this period against the one before, plus how many ideas mention it."""
    if user["projects"].get(project_id) != "receiver":
        raise ServiceError(404, "Project not found")
    today = datetime.now(timezone.utc).date()
    start, prev = (today - timedelta(days=days - 1)).isoformat(), (today - timedelta(days=2 * days - 1)).isoformat()
    with db.connect(s) as c:
        rows = c.execute(
            """SELECT tool, MAX(tool_label) AS label,
                 SUM(CASE WHEN day >= :start THEN positive ELSE 0 END) AS pos,
                 SUM(CASE WHEN day >= :start THEN negative ELSE 0 END) AS neg,
                 SUM(CASE WHEN day >= :start THEN neutral ELSE 0 END) AS neu,
                 SUM(CASE WHEN day >= :start THEN rating_count ELSE 0 END) AS rc,
                 SUM(CASE WHEN day >= :start THEN rating_sum ELSE 0 END) AS rs,
                 SUM(CASE WHEN day >= :start THEN comments ELSE 0 END) AS com,
                 SUM(CASE WHEN day < :start THEN positive ELSE 0 END) AS ppos,
                 SUM(CASE WHEN day < :start THEN negative ELSE 0 END) AS pneg,
                 SUM(CASE WHEN day >= :start THEN uses END) AS uses,
                 SUM(CASE WHEN day >= :start AND failures IS NOT NULL THEN failures END) AS fail,
                 SUM(CASE WHEN day >= :start AND failures IS NOT NULL THEN uses END) AS fail_of,
                 SUM(CASE WHEN day >= :start AND reruns IS NOT NULL THEN reruns END) AS rerun,
                 SUM(CASE WHEN day >= :start AND reruns IS NOT NULL THEN uses END) AS rerun_of,
                 SUM(CASE WHEN day < :start AND failures IS NOT NULL THEN failures END) AS pfail,
                 SUM(CASE WHEN day < :start AND failures IS NOT NULL THEN uses END) AS pfail_of,
                 SUM(CASE WHEN day < :start AND reruns IS NOT NULL THEN reruns END) AS prerun,
                 SUM(CASE WHEN day < :start AND reruns IS NOT NULL THEN uses END) AS prerun_of
               FROM signals WHERE project_id=:pid AND day >= :prev GROUP BY tool""",
            {"start": start, "prev": prev, "pid": project_id}).fetchall()
        ideas = dict(c.execute("SELECT tool, COUNT(*) FROM cards WHERE project_id=? AND tool!='' AND merged_into IS NULL AND erased_at IS NULL GROUP BY tool", (project_id,)).fetchall())
    out = []
    for r in rows:
        votes, pvotes = r["pos"] + r["neg"], r["ppos"] + r["pneg"]
        out.append({
            "tool": r["tool"], "label": r["label"] or r["tool"], "days": days,
            "positive": r["pos"], "negative": r["neg"], "neutral": r["neu"], "comments": r["com"],
            "positive_share": round(r["pos"] / votes, 3) if votes else None,
            "previous_positive_share": round(r["ppos"] / pvotes, 3) if pvotes else None,
            "average_rating": round(r["rs"] / r["rc"], 2) if r["rc"] else None,
            "uses": r["uses"],
            "failure_rate": _rate_of(r["fail"], r["fail_of"]), "previous_failure_rate": _rate_of(r["pfail"], r["pfail_of"]),
            "rerun_rate": _rate_of(r["rerun"], r["rerun_of"]), "previous_rerun_rate": _rate_of(r["prerun"], r["prerun_of"]),
            "ideas": ideas.pop(r["tool"], 0),
        })
    out += [{"tool": t, "label": t, "days": days, "positive": 0, "negative": 0, "neutral": 0, "comments": 0,
             "positive_share": None, "previous_positive_share": None, "average_rating": None, "uses": None,
             "failure_rate": None, "previous_failure_rate": None, "rerun_rate": None, "previous_rerun_rate": None, "ideas": n} for t, n in ideas.items()]
    out.sort(key=lambda x: (x["positive_share"] if x["positive_share"] is not None else 2, -x["ideas"]))
    return out
