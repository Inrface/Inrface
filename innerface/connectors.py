"""Connectors: one shared way to link a project to an outside system.

Each kind of connector (a webhook, Linear, Slack...) describes its settings as a list of fields
and supplies a test. A build destination also supplies a way to send a build request; a place
where ideas are discussed supplies a way to post there. Everything else (saving, permissions,
the Settings screen, keeping secrets out of responses) is shared.

Secrets go in and never come back out: the API returns only whether one is set and its last
four characters. Reference: docs/connectors.md
"""
import json
import logging
import re
import secrets as pysecrets
from dataclasses import dataclass
from typing import Callable

import httpx

from . import db, plans, vault
from .config import Settings
from .service import ServiceError, _receiver_project

log = logging.getLogger("innerface.connectors")

MAX_PER_PROJECT = 12


class ConnectorError(Exception):
    """The message is safe to show."""


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    secret: bool = False
    required: bool = True
    generated: bool = False  # made by Inrface and shown once, instead of typed in
    help: str = ""
    placeholder: str = ""
    pattern: str = ""
    max: int = 300


@dataclass(frozen=True)
class Conn:
    """A saved connector with its secrets readable. Never return one of these from the API."""
    id: str
    project_id: str
    kind: str
    name: str
    values: dict
    secrets: dict
    enabled: bool


@dataclass(frozen=True)
class Sent:
    """What a destination says it made from a build request."""
    label: str
    url: str = ""
    external_id: str = ""


@dataclass(frozen=True)
class Kind:
    id: str
    label: str
    summary: str
    fields: tuple
    test: Callable  # (Settings, Conn) -> a short line saying what worked
    send_build: Callable | None = None  # (Settings, Conn, build: dict) -> Sent
    clean: Callable | None = None  # (Settings, values: dict) -> values, for checks a pattern can't do
    docs: str = ""
    to_thread: Callable | None = None  # (Settings, Conn, link: dict, event: dict): say something where an idea is discussed
    notes: Callable | None = None  # (Settings, connector id) -> [{"label", "value", "show"}], things to copy into the outside system
    group: str = ""  # "tracker" or "chat" when a plan can limit how many of this sort a project has


# Which plan limit each group of connectors counts against.
GROUPS = {"tracker": "trackers", "chat": "chat_channels"}

KINDS: dict[str, Kind] = {}


def register(kind: Kind) -> None:
    KINDS[kind.id] = kind


# ---------- reading ----------

def _conn(s: Settings, r) -> Conn:
    try:
        secret_values = json.loads(vault.unseal(s, r["secrets"]) or "{}")
    except vault.VaultError as e:
        raise ConnectorError(f"{e} Enter this connector's secrets again in Settings.")
    return Conn(id=r["id"], project_id=r["project_id"], kind=r["kind"], name=r["name"],
                values=json.loads(r["config"]), secrets=secret_values, enabled=bool(r["enabled"]))


def _kind_out(k: Kind) -> dict:
    return {"id": k.id, "label": k.label, "summary": k.summary, "docs": k.docs, "sends_builds": bool(k.send_build),
            "fields": [{"key": f.key, "label": f.label, "secret": f.secret, "required": f.required, "generated": f.generated,
                        "help": f.help, "placeholder": f.placeholder} for f in k.fields]}


def _out(s: Settings, r) -> dict:
    kind = KINDS.get(r["kind"])
    try:
        saved, unreadable = _conn(s, r).secrets, False
    except ConnectorError:
        saved, unreadable = {}, True
    return {
        "id": r["id"], "project_id": r["project_id"], "kind": r["kind"], "kind_label": kind.label if kind else r["kind"],
        "name": r["name"], "enabled": bool(r["enabled"]), "values": json.loads(r["config"]),
        "secrets": {f.key: {"set": bool(saved.get(f.key)), "hint": saved[f.key][-4:] if saved.get(f.key) else ""}
                    for f in (kind.fields if kind else ()) if f.secret},
        "unreadable": unreadable,
        "notes": kind.notes(s, r["id"]) if kind and kind.notes else [],
        "created_at": r["created_at"], "last_ok_at": r["last_ok_at"], "last_error": r["last_error"],
    }


def _row(c, project_id: str, connector_id: str):
    r = c.execute("SELECT * FROM connectors WHERE id=? AND project_id=?", (connector_id, project_id)).fetchone()
    if not r:
        raise ServiceError(404, "Connector not found")
    return r


def list_connectors(s: Settings, user: dict, project_id: str) -> dict:
    project = _receiver_project(s, user, project_id)
    with db.connect(s) as c:
        rows = c.execute("SELECT * FROM connectors WHERE project_id=? ORDER BY created_at", (project.id,)).fetchall()
    return {"kinds": [_kind_out(k) for k in KINDS.values()], "connectors": [_out(s, r) for r in rows],
            "encrypted": bool(s.secret_key)}


def enabled(s: Settings, project_id: str, kind: str | None = None) -> list[Conn]:
    """The project's switched-on connectors, for the code that uses them. Unreadable ones are skipped."""
    with db.connect(s) as c:
        rows = c.execute("SELECT * FROM connectors WHERE project_id=? AND enabled=1 ORDER BY created_at", (project_id,)).fetchall()
    out = []
    for r in rows:
        if r["kind"] not in KINDS or (kind and r["kind"] != kind):
            continue
        try:
            out.append(_conn(s, r))
        except ConnectorError as e:
            log.warning("connector %s skipped: %s", r["id"], e)
    return out


def get(s: Settings, connector_id: str) -> Conn | None:
    """Look a connector up by id alone, for requests that arrive from the outside system."""
    with db.connect(s) as c:
        r = c.execute("SELECT * FROM connectors WHERE id=?", (connector_id,)).fetchone()
    if not r or r["kind"] not in KINDS or r["project_id"] not in s.projects:
        return None
    try:
        return _conn(s, r)
    except ConnectorError:
        return None


# ---------- saving ----------

def _generate(f: Field) -> str:
    return "whsec_" + pysecrets.token_urlsafe(32)


def _clean(s: Settings, kind: Kind, values: dict, old: Conn | None, regenerate: bool = False) -> tuple[dict, dict, dict]:
    """Check what was typed against the kind's fields. Returns (settings, secrets, secrets to show once)."""
    values = values or {}
    unknown = set(values) - {f.key for f in kind.fields}
    if unknown:
        raise ServiceError(422, "Unknown setting: " + ", ".join(sorted(unknown)))
    plain = dict(old.values) if old else {}
    secret = dict(old.secrets) if old else {}
    reveal = {}
    for f in kind.fields:
        if f.generated:
            if not old or regenerate:
                secret[f.key] = reveal[f.key] = _generate(f)
            continue
        if f.key not in values:
            if not old and f.required:
                raise ServiceError(422, f"{f.label} is needed")
            continue
        v = str(values[f.key] or "").strip()
        if f.secret and not v:
            if not secret.get(f.key) and f.required:
                raise ServiceError(422, f"{f.label} is needed")
            continue  # a blank secret means keep the saved one
        if not v and f.required:
            raise ServiceError(422, f"{f.label} is needed")
        # Never repeat the value in the error: it may be a secret.
        if len(v) > f.max or (v and f.pattern and not re.fullmatch(f.pattern, v)):
            raise ServiceError(422, f"{f.label} doesn't look right. {f.help}".strip())
        (secret if f.secret else plain)[f.key] = v
    if kind.clean:
        try:
            plain = kind.clean(s, plain)
        except ConnectorError as e:
            raise ServiceError(422, str(e))
    return plain, secret, reveal


def _name(name: str | None, kind: Kind) -> str:
    name = (name or "").strip() or kind.label
    if len(name) > 60:
        raise ServiceError(422, "The name can be up to 60 characters")
    return name


def create_connector(s: Settings, user: dict, project_id: str, kind_id: str, name: str | None, values: dict) -> dict:
    project = _receiver_project(s, user, project_id)
    kind = KINDS.get(kind_id)
    if not kind:
        raise ServiceError(422, "Unknown kind of connector")
    plain, secret, reveal = _clean(s, kind, values, None)
    cid, t = db.new_id(), db.now()
    with db.connect(s) as c:
        room = plans.limit(s, GROUPS[kind.group]) if kind.group else None
        if room is not None:
            kinds = [k for (k,) in c.execute("SELECT kind FROM connectors WHERE project_id=?", (project.id,))]
            if sum(1 for k in kinds if k in KINDS and KINDS[k].group == kind.group) >= room:
                raise ServiceError(402, plans.full(s, GROUPS[kind.group]))
        if c.execute("SELECT COUNT(*) FROM connectors WHERE project_id=?", (project.id,)).fetchone()[0] >= MAX_PER_PROJECT:
            raise ServiceError(422, f"A project can have up to {MAX_PER_PROJECT} connectors")
        c.execute("INSERT INTO connectors(id, project_id, kind, name, config, secrets, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                  (cid, project.id, kind.id, _name(name, kind), json.dumps(plain), vault.seal(s, json.dumps(secret)), user["email"], t, t))
        out = _out(s, _row(c, project.id, cid))
    log.info("connector %s (%s) added to %s by %s", cid, kind.id, project.id, user["email"])
    return {**out, "reveal": reveal}


def update_connector(s: Settings, user: dict, project_id: str, connector_id: str, name: str | None = None,
                     values: dict | None = None, enabled: bool | None = None, regenerate: bool = False) -> dict:
    project = _receiver_project(s, user, project_id)
    with db.connect(s) as c:
        r = _row(c, project.id, connector_id)
        kind = KINDS.get(r["kind"])
        if not kind:
            raise ServiceError(422, "This kind of connector is no longer supported. Remove it.")
        try:
            old = _conn(s, r)
        except ConnectorError:
            # The saved secrets can't be read, so they must all be typed in again.
            old = Conn(id=r["id"], project_id=r["project_id"], kind=r["kind"], name=r["name"], values=json.loads(r["config"]), secrets={}, enabled=bool(r["enabled"]))
            regenerate = True
        fields: dict = {}
        reveal: dict = {}
        if values is not None or regenerate:
            plain, secret, reveal = _clean(s, kind, values or {}, old, regenerate)
            missing = [f.label for f in kind.fields if f.secret and f.required and not secret.get(f.key)]
            if missing:
                raise ServiceError(422, missing[0] + " is needed")
            fields.update(config=json.dumps(plain), secrets=vault.seal(s, json.dumps(secret)), last_error="")
        if name is not None:
            fields["name"] = _name(name, kind)
        if enabled is not None:
            fields["enabled"] = int(enabled)
        if not fields:
            raise ServiceError(422, "Nothing to save")
        fields["updated_at"] = db.now()
        c.execute(f"UPDATE connectors SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), r["id"]))
        out = _out(s, _row(c, project.id, connector_id))
    log.info("connector %s changed by %s: %s", connector_id, user["email"], ", ".join(sorted(fields)))
    return {**out, "reveal": reveal}


def delete_connector(s: Settings, user: dict, project_id: str, connector_id: str) -> None:
    project = _receiver_project(s, user, project_id)
    with db.connect(s) as c:
        _row(c, project.id, connector_id)
        c.execute("DELETE FROM connectors WHERE id=?", (connector_id,))
    log.info("connector %s removed by %s", connector_id, user["email"])


# ---------- using ----------

def _record(s: Settings, connector_id: str, error: str = "") -> None:
    with db.connect(s) as c:
        if error:
            c.execute("UPDATE connectors SET last_error=? WHERE id=?", (error[:300], connector_id))
        else:
            c.execute("UPDATE connectors SET last_ok_at=?, last_error='' WHERE id=?", (db.now(), connector_id))


def call(s: Settings, conn: Conn, fn: Callable, *args):
    """Run one of a kind's functions, turning anything that goes wrong into a ConnectorError and noting the outcome."""
    kind = KINDS[conn.kind]
    try:
        result = fn(s, conn, *args)
    except ConnectorError as e:
        _record(s, conn.id, str(e))
        raise
    except httpx.HTTPError:
        msg = f"Couldn't reach {kind.label}. Try again in a minute."
        _record(s, conn.id, msg)
        raise ConnectorError(msg)
    _record(s, conn.id)
    return result


def test_connector(s: Settings, user: dict, project_id: str, connector_id: str) -> dict:
    project = _receiver_project(s, user, project_id)
    with db.connect(s) as c:
        r = _row(c, project.id, connector_id)
    try:
        conn = _conn(s, r)
        return {"ok": True, "detail": call(s, conn, KINDS[conn.kind].test)}
    except ConnectorError as e:
        raise ServiceError(502, str(e))
    except KeyError:
        raise ServiceError(422, "This kind of connector is no longer supported. Remove it.")


def build_destinations(s: Settings, project_id: str) -> list[Conn]:
    return [x for x in enabled(s, project_id) if KINDS[x.kind].send_build]


def send_build(s: Settings, conn: Conn, build: dict) -> Sent:
    sent = call(s, conn, KINDS[conn.kind].send_build, build)
    url = sent.url if re.fullmatch(r"https://[^\s<>\"']{1,500}", sent.url or "") else ""
    return Sent(label=(sent.label or conn.name)[:80], url=url, external_id=(sent.external_id or "")[:200])


def post_to_thread(s: Settings, card_id: str, event: dict) -> None:
    """Say something wherever the idea is being discussed, such as its Slack thread. Best effort:
    this runs off the request path and a failure only shows in Settings and the log."""
    with db.connect(s) as c:
        links = [dict(r) for r in c.execute("SELECT * FROM card_links WHERE card_id=? AND role='thread'", (card_id,))]
    for link in links:
        conn = get(s, link["connector_id"])
        kind = KINDS.get(conn.kind) if conn else None
        if not conn or not conn.enabled or not kind or not kind.to_thread:
            continue
        try:
            call(s, conn, kind.to_thread, link, event)
        except ConnectorError as e:
            log.warning("couldn't post to the thread for card %s: %s", card_id, e)


# Each kind registers itself when its module loads. These imports sit at the end because the
# modules need the classes above.
from . import linear, slack, webhook  # noqa: E402,F401
