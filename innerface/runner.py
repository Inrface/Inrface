"""Build runner settings: what the small script that picks up build issues asks Inrface for.

The script (tools/runner/inrface_runner.py) runs on the machine that holds the code. Inrface gives it only when to
check, how many builds at once, which issue label to look for and whether it is switched on. What runs, where, with
which tools and tokens stays in a file on that machine and is never set from here. The runner's key can read these
settings and nothing else: no card text, no build phrase, no tokens.
Reference: docs/build-runner.md
"""
import hashlib
import hmac
import re
import secrets

from . import db
from .config import Settings
from .service import ServiceError, _receiver_project, github_project

KEY_PREFIX = "inrr_"
MIN_INTERVAL, MAX_INTERVAL, DEFAULT_INTERVAL = 1, 1440, 5
MAX_BUILDS = 5


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _row(s: Settings, project_id: str):
    with db.connect(s) as c:
        return c.execute("SELECT * FROM project_runner WHERE project_id=?", (project_id,)).fetchone()


def _out(r) -> dict:
    return {"enabled": bool(r["enabled"]) if r else False,
            "interval_minutes": r["interval_minutes"] if r else DEFAULT_INTERVAL,
            "max_builds": r["max_builds"] if r else 1,
            "key_set": bool(r and r["key_hash"]), "key_hint": (r["key_hint"] if r else ""),
            "last_seen_at": (r["last_seen_at"] if r else "") or None,
            "limits": {"interval_minutes": [MIN_INTERVAL, MAX_INTERVAL], "max_builds": [1, MAX_BUILDS]}}


def view(s: Settings, user: dict, project_id: str) -> dict:
    project = _receiver_project(s, user, project_id)
    return {"project_id": project.id, **_out(_row(s, project.id))}


def save(s: Settings, user: dict, project_id: str, enabled: bool | None, interval_minutes: int | None, max_builds: int | None) -> dict:
    project = _receiver_project(s, user, project_id)
    cur = _out(_row(s, project.id))
    enabled = cur["enabled"] if enabled is None else enabled
    interval = cur["interval_minutes"] if interval_minutes is None else interval_minutes
    builds = cur["max_builds"] if max_builds is None else max_builds
    if not MIN_INTERVAL <= interval <= MAX_INTERVAL:
        raise ServiceError(422, f"Check between every {MIN_INTERVAL} and {MAX_INTERVAL} minutes")
    if not 1 <= builds <= MAX_BUILDS:
        raise ServiceError(422, f"Run between 1 and {MAX_BUILDS} builds at a time")
    with db.connect(s) as c:
        c.execute("INSERT INTO project_runner(project_id, enabled, interval_minutes, max_builds, updated_by, updated_at) VALUES (?,?,?,?,?,?) "
                  "ON CONFLICT(project_id) DO UPDATE SET enabled=excluded.enabled, interval_minutes=excluded.interval_minutes, "
                  "max_builds=excluded.max_builds, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                  (project.id, int(enabled), interval, builds, user["email"], db.now()))
    return view(s, user, project.id)


def new_key(s: Settings, user: dict, project_id: str) -> dict:
    """Make the project's runner key, replacing any earlier one. Shown once; only its hash is kept."""
    project = _receiver_project(s, user, project_id)
    kid, secret = secrets.token_hex(4), secrets.token_urlsafe(32)
    key = f"{KEY_PREFIX}{kid}.{secret}"
    with db.connect(s) as c:
        c.execute("INSERT INTO project_runner(project_id, key_id, key_hash, key_hint, updated_by, updated_at) VALUES (?,?,?,?,?,?) "
                  "ON CONFLICT(project_id) DO UPDATE SET key_id=excluded.key_id, key_hash=excluded.key_hash, key_hint=excluded.key_hint, "
                  "last_seen_at='', updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                  (project.id, kid, _hash(secret), key[-4:], user["email"], db.now()))
    return {**view(s, user, project.id), "key": key}


def remove_key(s: Settings, user: dict, project_id: str) -> dict:
    project = _receiver_project(s, user, project_id)
    with db.connect(s) as c:
        c.execute("UPDATE project_runner SET key_id='', key_hash='', key_hint='', updated_by=?, updated_at=? WHERE project_id=?",
                  (user["email"], db.now(), project.id))
    return view(s, user, project.id)


def config_for_key(s: Settings, authorization: str | None) -> dict:
    """What the runner may know, for the project its key belongs to."""
    m = re.fullmatch(r"Bearer\s+inrr_([0-9a-f]{8})\.([A-Za-z0-9_-]{20,100})", (authorization or "").strip())
    if not m:
        raise ServiceError(401, "Missing or malformed key. Send Authorization: Bearer inrr_...")
    with db.connect(s) as c:
        r = c.execute("SELECT * FROM project_runner WHERE key_id=? AND key_id!=''", (m.group(1),)).fetchone()
    if not r or not hmac.compare_digest(r["key_hash"], _hash(m.group(2))):
        raise ServiceError(401, "Unknown key")
    if r["project_id"] not in s.projects:
        raise ServiceError(403, "This key's project no longer exists")
    with db.connect(s) as c:
        c.execute("UPDATE project_runner SET last_seen_at=? WHERE project_id=?", (db.now(), r["project_id"]))
    p = github_project(s, s.projects[r["project_id"]])
    out = _out(r)
    return {"project": p.id, "enabled": out["enabled"], "interval_minutes": out["interval_minutes"],
            "max_builds": out["max_builds"], "repo": p.repo, "build_label": p.build_label}
