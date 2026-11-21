"""Slack: ideas come in from a channel, a slash command or a message shortcut, and are filed and
discussed from their thread. Nothing here can start a build. Reference: docs/slack.md

Slack calls one address for everything (see main.py). There is no login on it: each request is
checked against the connector's signing secret before anything else happens.
"""
import hashlib
import hmac
import json
import logging
import re
import sqlite3
import threading
import time
from collections import OrderedDict, deque
from urllib.parse import parse_qs

import httpx

from . import connectors, db, service
from .config import Settings
from .connectors import Conn, ConnectorError, Field, Kind, register
from .service import OUTSIDE, ServiceError

log = logging.getLogger("innerface.slack")

API = "https://slack.com/api/"
MAX_BODY = 256 * 1024
MAX_SKEW = 300          # seconds a request's timestamp may be off by
MAX_IDEA = 5000
USER_TTL = 600          # seconds before asking Slack again who someone is
IDEAS_PER_MINUTE = 30   # per connector
REPLIES_PER_MINUTE = 120  # thread replies turned into comments, per connector
FILE_AS = {"inr_approve": "Immediate roadmap", "inr_defer": "Far down the line"}
SHORTCUT, ASK = "inr_send", "inr_clarify"

_ID = re.compile(r"[A-Z0-9]{2,30}")
_TS = re.compile(r"\d{1,12}\.\d{1,9}")
_CARD = re.compile(r"[0-9a-f]{32}")


def _http(url: str, **kw) -> httpx.Response:
    return httpx.post(url, **kw)


# ---------- calling Slack ----------

def _problem(body: dict) -> str:
    code = body.get("error") if isinstance(body.get("error"), str) else ""
    if code in ("invalid_auth", "not_authed", "token_revoked", "token_expired", "account_inactive"):
        return "Slack refused the bot token. Copy it again from the Slack app's Install App page."
    if code == "missing_scope":
        needed = body.get("needed") if isinstance(body.get("needed"), str) and re.fullmatch(r"[a-z:.,_]{1,80}", body["needed"]) else ""
        return f"The Slack app is missing a permission{f' ({needed})' if needed else ''}. Add it under OAuth & Permissions, then reinstall the app."
    if code in ("not_in_channel", "channel_not_found"):
        return "Slack can't reach the ideas channel. Check the channel ID, and invite the app there: type /invite and pick the app."
    if code == "is_archived":
        return "The ideas channel is archived."
    if code == "ratelimited":
        return "Slack is asking us to slow down. Try again in a minute."
    # Slack's codes are short fixed words, safe to repeat. Anything else stays in the log.
    return f"Slack couldn't do that ({code})." if re.fullmatch(r"[a-z_]{1,40}", code) else "Slack couldn't do that just now."


def _api(conn: Conn, method: str, args: dict, as_json: bool = False, timeout: float = 8.0) -> dict:
    token = conn.secrets.get("bot_token")
    if not token:
        raise ConnectorError("Add the bot token from the Slack app first.")
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "Inrface"}
    if as_json:
        r = _http(API + method, content=json.dumps(args).encode(), timeout=timeout,
                  headers={**headers, "Content-Type": "application/json; charset=utf-8"})
    else:
        r = _http(API + method, data=args, headers=headers, timeout=timeout)
    if r.status_code == 429:
        raise ConnectorError("Slack is asking us to slow down. Try again in a minute.")
    try:
        body = r.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict) or not body.get("ok"):
        body = body if isinstance(body, dict) else {}
        log.warning("slack %s failed: %s %s", method, r.status_code, str(body.get("error"))[:60])
        raise ConnectorError(_problem(body))
    return body


def _esc(text: str) -> str:
    """Make text safe to show in a Slack message: no stray mentions, links or @channel."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _respond(url, text: str) -> None:
    """Answer the person who pressed a button or ran a command, visible only to them."""
    if not isinstance(url, str) or not url.startswith("https://hooks.slack.com/"):
        return
    try:
        _http(url, json={"response_type": "ephemeral", "replace_original": False, "text": text}, timeout=8.0)
    except httpx.HTTPError:
        log.warning("couldn't answer in Slack")


def _safely(fn, *args) -> None:
    """For work done after Slack has been answered: there is nobody left to show an error to."""
    try:
        with service.through("slack"):
            fn(*args)
    except Exception:
        log.exception("slack: %s failed", getattr(fn, "__name__", "work"))


class _Busy(Exception):
    """Too much work is already waiting. Slack is told to try again."""


def _later(fn, *args) -> None:
    if service._submit(_safely, fn, *args) is False:
        raise _Busy()


# ---------- the signature ----------

def verify(secret: str, timestamp: str, signature: str, raw: bytes, now: float | None = None) -> bool:
    """Slack signs "v0:<timestamp>:<body>" with the app's signing secret. Old timestamps are refused so a
    captured request can't be sent again later."""
    if not secret or not re.fullmatch(r"[0-9]{1,12}", timestamp or ""):
        return False
    if abs((time.time() if now is None else now) - int(timestamp)) > MAX_SKEW:
        return False
    mac = hmac.new(secret.encode(), b"v0:" + timestamp.encode() + b":" + raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(("v0=" + mac).encode(), (signature or "").encode("utf-8", "replace"))


# ---------- who someone is ----------

def _lookup(s: Settings, conn: Conn, uid: str) -> dict:
    with db.connect(s) as c:
        r = c.execute("SELECT name, email, fetched_at FROM slack_users WHERE connector_id=? AND user_id=?", (conn.id, uid)).fetchone()
    if r and r["fetched_at"] > db.ago(USER_TTL):
        return {"name": r["name"], "email": r["email"]}
    try:
        u = _api(conn, "users.info", {"user": uid}, timeout=2.5).get("user") or {}
    except (ConnectorError, httpx.HTTPError):
        # Without a fresh answer from Slack, keep the name but never the identity.
        return {"name": r["name"] if r else "", "email": ""}
    profile = u.get("profile") if isinstance(u.get("profile"), dict) else {}
    name = str(u.get("real_name") or profile.get("real_name") or profile.get("display_name") or "").strip()[:80]
    email = ""
    theirs = str(profile.get("email") or "").strip().lower()
    # An address counts only when Slack says outright that its owner confirmed it.
    trusted = not (u.get("deleted") or u.get("is_bot") or u.get("is_stranger")) and u.get("is_email_confirmed") is True
    if trusted:
        email = theirs
    elif theirs in s.people:
        log.info("slack connector %s: %s isn't matched to anyone here, because Slack doesn't mark the email address as confirmed", conn.id, uid)
    if email not in s.people:
        email = ""  # only keep an address that belongs to someone already listed here
    with db.connect(s) as c:
        c.execute("INSERT INTO slack_users(connector_id, user_id, name, email, fetched_at) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(connector_id, user_id) DO UPDATE SET name=excluded.name, email=excluded.email, fetched_at=excluded.fetched_at",
                  (conn.id, uid, name, email, db.now()))
    service.remember_name(s, OUTSIDE + uid, name)
    return {"name": name, "email": email}


def _who(s: Settings, conn: Conn, uid: str) -> dict:
    """The person behind a Slack id, in the shape the rest of the app uses. Someone listed in innerface.toml
    with the same email address gets their role on this connector's project, and only on that project.
    Anyone else in the workspace can bring ideas and reply in threads, as a giver."""
    info = _lookup(s, conn, uid)
    role = s.projects[conn.project_id].role(info["email"]) if info["email"] else None
    if role:
        return {"email": info["email"], "name": s.name(info["email"]), "projects": {conn.project_id: role}}
    return {"email": OUTSIDE + uid, "name": service.outside_name(info["name"]), "projects": {conn.project_id: "giver"}}


def _receiver(s: Settings, conn: Conn, uid) -> dict:
    user = _who(s, conn, uid) if isinstance(uid, str) and _ID.fullmatch(uid) else None
    if not user or not service.is_receiver(user, conn.project_id):
        raise ServiceError(403, f"Only a receiver on {s.projects[conn.project_id].name} can do that. "
                                "Inrface matches people by the email address on their Slack account.")
    return user


def _slack_id(s: Settings, conn: Conn, who: str) -> str:
    if who.startswith(OUTSIDE):
        return who[len(OUTSIDE):]
    with db.connect(s) as c:
        r = c.execute("SELECT user_id FROM slack_users WHERE connector_id=? AND email=? ORDER BY fetched_at DESC LIMIT 1", (conn.id, who)).fetchone()
    return r["user_id"] if r else ""


# ---------- reading Slack's text ----------

_MARKUP = re.compile(r"<([^<>\n]{1,2000})>")


def plain(s: Settings, text: str) -> str:
    """Slack's message markup as ordinary text: <@U1> to @Name, <https://x|label> to label (https://x)."""
    def one(m):
        body, _, label = m.group(1).partition("|")
        if body.startswith("@"):
            if not label:
                with db.connect(s) as c:
                    r = c.execute("SELECT name FROM slack_users WHERE user_id=? ORDER BY fetched_at DESC LIMIT 1", (body[1:],)).fetchone()
                label = r["name"] if r and r["name"] else "someone"
            return "@" + label.lstrip("@")
        if body.startswith("#"):
            return "#" + (label or "channel")
        if body.startswith("!"):
            return "@" + (label or body[1:].split("^")[0]).lstrip("@")
        return f"{label} ({body})" if label and label != body else body
    out = _MARKUP.sub(one, text if isinstance(text, str) else "")
    return out.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").strip()


# ---------- making cards ----------

_recent: dict[str, deque] = {}
_seen: OrderedDict = OrderedDict()
_lock = threading.Lock()


def _allow(connector_id: str, what: str = "idea") -> bool:
    limit = REPLIES_PER_MINUTE if what == "reply" else IDEAS_PER_MINUTE
    now = time.monotonic()
    with _lock:
        q = _recent.setdefault((connector_id, what), deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


_signed: OrderedDict = OrderedDict()


def _fresh(key: tuple) -> bool:
    """A signed request is good for five minutes, so the same one arriving twice in that time is a copy.
    An exact repeat carries nothing new, so it is answered and dropped."""
    now = time.monotonic()
    with _lock:
        while _signed and now - next(iter(_signed.values())) > 2 * MAX_SKEW:
            _signed.popitem(last=False)
        if key in _signed:
            return False
        _signed[key] = now
        while len(_signed) > 20000:
            _signed.popitem(last=False)
        return True


def _forget(key: tuple, event: tuple | None = None) -> None:
    with _lock:
        _signed.pop(key, None)
        if event:
            _seen.pop(event, None)


def _first_time(connector_id: str, event_id) -> bool:
    """Slack sends an event again if the first answer was slow. Handle each once."""
    if not isinstance(event_id, str) or not event_id:
        return True
    key = (connector_id, event_id)
    with _lock:
        if key in _seen:
            return False
        _seen[key] = True
        while len(_seen) > 2000:
            _seen.popitem(last=False)
        return True


def _link(c, card_id: str, conn: Conn, role: str, external_id: str, url: str = "") -> None:
    c.execute("INSERT INTO card_links(id, card_id, connector_id, kind, role, state, label, url, external_id, created_at) VALUES (?,?,?,?,?,'sent',?,?,?,?)",
              (db.new_id(), card_id, conn.id, "slack", role, "Slack thread" if role == "thread" else "Slack message", url, external_id, db.now()))


def _add(s: Settings, conn: Conn, user: dict, text: str, claim: tuple[str, str] | None) -> tuple[str, str] | None:
    """Make the card. `claim` is (role, "channel:ts") for the Slack message it came from: the link is saved
    with the card, and a message that already has one makes nothing. Returns (card id, title) or None."""
    also = (lambda c, card_id: _link(c, card_id, conn, claim[0], claim[1])) if claim else None
    try:
        ids, titles = service.create_cards(s, user, conn.project_id, "slack", text[:MAX_IDEA], [], [None], also=also)
    except sqlite3.IntegrityError:
        return None
    return ids[0], titles[0]


def _permalink(conn: Conn, channel: str, ts: str) -> str:
    try:
        url = _api(conn, "chat.getPermalink", {"channel": channel, "message_ts": ts}).get("permalink")
    except (ConnectorError, httpx.HTTPError):
        return ""
    return url if isinstance(url, str) and re.fullmatch(r"https://[^\s<>\"'|]{1,500}", url) else ""


def _blocks(s: Settings, card_id: str, lead: str) -> list:
    if s.public_url:
        lead += f"\n<{s.public_url}/#card={card_id}|Open the card>"

    def button(action: str, label: str, **more) -> dict:
        return {"type": "button", "action_id": action, "value": card_id, "text": {"type": "plain_text", "text": label}, **more}
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": lead}},
        {"type": "actions", "block_id": "inr_file", "elements": [
            button("inr_approve", "Approve", style="primary"), button("inr_defer", "Defer"), button(ASK, "Clarify")]},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "Replies in this thread are added to the card. The buttons are for receivers."}]},
    ]


def _post(s: Settings, conn: Conn, card_id: str, title: str, lead: str, thread_ts: str | None = None) -> str:
    """Post the card's message with its buttons in the ideas channel. Returns the message's ts."""
    args = {"channel": conn.values["channel"], "text": f"Added to Inrface: {_esc(title)}", "blocks": _blocks(s, card_id, lead),
            "unfurl_links": False, "unfurl_media": False}
    if thread_ts:
        args["thread_ts"] = thread_ts
    ts = connectors.call(s, conn, lambda _s, _c: _api(conn, "chat.postMessage", args, as_json=True)).get("ts")
    return ts if isinstance(ts, str) else ""


def _start_thread(s: Settings, conn: Conn, card_id: str, title: str, lead: str) -> str:
    """For an idea that didn't start as a message in the ideas channel: post it there and make that its thread.
    Returns a link to the message, or raises ConnectorError."""
    channel = conn.values["channel"]
    ts = _post(s, conn, card_id, title, lead)
    url = _permalink(conn, channel, ts) if ts else ""
    if ts:
        with db.connect(s) as c:
            _link(c, card_id, conn, "thread", f"{channel}:{ts}", url)
    return url


def _quote(text: str) -> str:
    return "\n".join("> " + line for line in _esc(text[:2500]).splitlines())


def _from_channel(s: Settings, conn: Conn, uid: str, channel: str, ts: str, text: str) -> None:
    if not _allow(conn.id):
        log.warning("slack connector %s: too many ideas in a minute, one skipped", conn.id)
        return
    made = _add(s, conn, _who(s, conn, uid), text, ("thread", f"{channel}:{ts}"))
    if not made:
        return
    card_id, title = made
    try:
        _post(s, conn, card_id, title, f"Added to Inrface as *{_esc(title)}*.", thread_ts=ts)
    except ConnectorError:
        pass  # the card exists; Settings shows what went wrong
    url = _permalink(conn, channel, ts)
    if url:
        with db.connect(s) as c:
            c.execute("UPDATE card_links SET url=? WHERE card_id=? AND connector_id=? AND role='thread'", (url, card_id, conn.id))


def _from_reply(s: Settings, conn: Conn, uid: str, channel: str, thread_ts: str, text: str) -> None:
    with db.connect(s) as c:
        link = c.execute("SELECT card_id FROM card_links WHERE connector_id=? AND role='thread' AND external_id=?",
                         (conn.id, f"{channel}:{thread_ts}")).fetchone()
        merged = c.execute("SELECT merged_into FROM cards WHERE id=?", (link["card_id"],)).fetchone() if link else None
    if not link:
        return
    if not _allow(conn.id, "reply"):
        log.warning("slack connector %s: too many thread replies in a minute, one skipped", conn.id)
        return
    # A card that was merged into another keeps its thread: the reply goes to the card it became.
    card_id = (merged["merged_into"] if merged and merged["merged_into"] else link["card_id"])
    try:
        service.comment(s, _who(s, conn, uid), card_id, "comment", text[:service.MAX_TEXT], from_thread=True)
    except ServiceError as e:
        log.info("slack reply not added to card %s: %s", card_id, e)


def _from_command(s: Settings, conn: Conn, uid: str, text: str, response_url) -> None:
    if not _allow(conn.id):
        return _respond(response_url, "Too many ideas in the last minute. Try again shortly.")
    card_id, title = _add(s, conn, _who(s, conn, uid), text, None)
    try:
        url = _start_thread(s, conn, card_id, title, f"*Idea from <@{uid}>*\n{_quote(text)}")
    except ConnectorError as e:
        return _respond(response_url, f"Added to Inrface as *{_esc(title)}*, but it couldn't be posted in the ideas channel. {e}")
    _respond(response_url, f"Added to Inrface as *{_esc(title)}*." + (f" <{url}|See it in the ideas channel>" if url else ""))


def _from_shortcut(s: Settings, conn: Conn, p: dict) -> None:
    msg = p.get("message") if isinstance(p.get("message"), dict) else {}
    channel = (p.get("channel") or {}).get("id") if isinstance(p.get("channel"), dict) else None
    sender = (p.get("user") or {}).get("id") if isinstance(p.get("user"), dict) else None
    ts, response_url = msg.get("ts"), p.get("response_url")
    if not all(isinstance(x, str) for x in (channel, sender, ts)) or not (_ID.fullmatch(channel) and _ID.fullmatch(sender) and _TS.fullmatch(ts)):
        return
    author = msg.get("user") if isinstance(msg.get("user"), str) and _ID.fullmatch(msg["user"]) and not msg.get("bot_id") else sender
    text = plain(s, msg.get("text"))
    if not text:
        return _respond(response_url, "That message has no text to send.")
    if not _allow(conn.id):
        return _respond(response_url, "Too many ideas in the last minute. Try again shortly.")
    top = not msg.get("thread_ts") or msg.get("thread_ts") == ts
    in_place = top and channel == conn.values["channel"]
    made = _add(s, conn, _who(s, conn, author), text, ("thread" if in_place else "origin", f"{channel}:{ts}"))
    if not made:
        return _respond(response_url, "That message is already in Inrface.")
    card_id, title = made
    try:
        if in_place:
            _post(s, conn, card_id, title, f"Added to Inrface as *{_esc(title)}*.", thread_ts=ts)
        else:
            source = _permalink(conn, channel, ts)
            lead = f"*Idea from <@{author}>*" + ("" if author == sender else f", sent by <@{sender}>") + f"\n{_quote(text)}"
            _start_thread(s, conn, card_id, title, lead + (f"\n<{source}|See the original message>" if source else ""))
    except ConnectorError as e:
        return _respond(response_url, f"Added to Inrface as *{_esc(title)}*, but it couldn't be posted in the ideas channel. {e}")
    _respond(response_url, f"Added to Inrface as *{_esc(title)}*.")


# ---------- filing from the thread ----------

def _linked(s: Settings, conn: Conn, card_id) -> str:
    """The buttons only work on cards this connector has a thread for."""
    ok = isinstance(card_id, str) and _CARD.fullmatch(card_id)
    if ok:
        with db.connect(s) as c:
            ok = c.execute("SELECT 1 FROM card_links WHERE card_id=? AND connector_id=? AND role='thread'", (card_id, conn.id)).fetchone()
    if not ok:
        raise ServiceError(404, "That idea isn't in Inrface any more.")
    return card_id


def _file(s: Settings, conn: Conn, uid, card_id, status: str, response_url) -> None:
    try:
        user = _receiver(s, conn, uid)
        card = service.read_card(s, user, _linked(s, conn, card_id))
        if card["build_started_at"]:
            raise ServiceError(409, "This idea is already being built.")
        if card["status"] == status:
            raise ServiceError(409, f"It's already in {status}.")
        service.triage(s, user, card_id, status=status)
    except ServiceError as e:
        _respond(response_url, str(e))


def _open_question(s: Settings, conn: Conn, uid, card_id, trigger_id, response_url) -> None:
    try:
        _receiver(s, conn, uid)
        _linked(s, conn, card_id)
    except ServiceError as e:
        return _later(_respond, response_url, str(e))
    view = {
        "type": "modal", "callback_id": ASK, "private_metadata": card_id,
        "title": {"type": "plain_text", "text": "Ask a question"},
        "submit": {"type": "plain_text", "text": "Ask"}, "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [{"type": "input", "block_id": "q", "label": {"type": "plain_text", "text": "What do you need to know?"},
                    "element": {"type": "plain_text_input", "action_id": "text", "multiline": True, "max_length": 2000}}],
    }
    try:
        # Slack only accepts this for three seconds after the button was pressed.
        _api(conn, "views.open", {"trigger_id": str(trigger_id), "view": view}, as_json=True, timeout=2.5)
    except (ConnectorError, httpx.HTTPError):
        _later(_respond, response_url, "Couldn't open the question box. Try again.")


def _ask(s: Settings, conn: Conn, uid, view: dict) -> dict | None:
    try:
        question = view["state"]["values"]["q"]["text"]["value"]
    except (KeyError, TypeError):
        question = ""
    try:
        user = _receiver(s, conn, uid)
        service.triage(s, user, _linked(s, conn, view.get("private_metadata")), status="Needs info", message=question if isinstance(question, str) else "")
    except ServiceError as e:
        return {"response_action": "errors", "errors": {"q": str(e)}}
    return None  # an empty answer closes the box


def to_thread(s: Settings, conn: Conn, link: dict, event: dict) -> None:
    """Something happened to the card in Inrface. Say so in its Slack thread."""
    channel, _, ts = link["external_id"].partition(":")
    by, text = _esc(event.get("by") or ""), _esc((event.get("text") or "")[:3000])
    if event["kind"] == "question":
        author = _slack_id(s, conn, event.get("author") or "")
        out = (f"<@{author}> " if author else "") + f"*{by} asks:* {text}\nReply in this thread to answer."
    elif event["kind"] == "status":
        out = (f"{by} moved this to *{_esc(event['status'])}*." if by else f"This is now *{_esc(event['status'])}*.") + (f"\n{text}" if text else "")
    else:
        out = f"*{by}:* {text}"
    _api(conn, "chat.postMessage", {"channel": channel, "thread_ts": ts, "text": out, "unfurl_links": False, "unfurl_media": False}, as_json=True)


# ---------- what Slack sends ----------

def _on_message(s: Settings, conn: Conn, ev: dict) -> None:
    # Skip the app's own posts, other bots, edits, joins and the like.
    if ev.get("bot_id") or ev.get("subtype") not in (None, "file_share", "thread_broadcast"):
        return
    uid, channel, ts, thread_ts = ev.get("user"), ev.get("channel"), ev.get("ts"), ev.get("thread_ts")
    if not all(isinstance(x, str) for x in (uid, channel, ts)) or not (_ID.fullmatch(uid) and _ID.fullmatch(channel) and _TS.fullmatch(ts)):
        return
    text = plain(s, ev.get("text"))
    if not text:
        return
    if isinstance(thread_ts, str) and thread_ts != ts:
        if _TS.fullmatch(thread_ts):
            _from_reply(s, conn, uid, channel, thread_ts, text)
    elif channel == conn.values.get("channel"):
        _from_channel(s, conn, uid, channel, ts, text)


def _event(s: Settings, conn: Conn, body) -> tuple[int, dict | None]:
    if not isinstance(body, dict):
        return 400, {"detail": "Bad request"}
    if body.get("type") == "url_verification":
        return 200, {"challenge": str(body.get("challenge") or "")[:200]}
    ev = body.get("event")
    if body.get("type") == "event_callback" and isinstance(ev, dict) and ev.get("type") == "message" and _first_time(conn.id, body.get("event_id")):
        try:
            _later(_on_message, s, conn, ev)
        except _Busy:
            _forget((), (conn.id, body.get("event_id")))
            raise
    return 200, None


def _interaction(s: Settings, conn: Conn, p) -> tuple[int, dict | None]:
    if not isinstance(p, dict):
        return 400, {"detail": "Bad request"}
    uid = (p.get("user") or {}).get("id") if isinstance(p.get("user"), dict) else None
    if p.get("type") == "message_action" and p.get("callback_id") == SHORTCUT:
        _later(_from_shortcut, s, conn, p)
    elif p.get("type") == "block_actions":
        actions = p.get("actions")
        action = actions[0] if isinstance(actions, list) and actions and isinstance(actions[0], dict) else {}
        if action.get("action_id") in FILE_AS:
            _later(_file, s, conn, uid, action.get("value"), FILE_AS[action["action_id"]], p.get("response_url"))
        elif action.get("action_id") == ASK:
            _open_question(s, conn, uid, action.get("value"), p.get("trigger_id"), p.get("response_url"))
    elif p.get("type") == "view_submission" and isinstance(p.get("view"), dict) and p["view"].get("callback_id") == ASK:
        return 200, _ask(s, conn, uid, p["view"])
    return 200, None


def _command(s: Settings, conn: Conn, form: dict) -> tuple[int, dict | None]:
    uid, text = form.get("user_id", ""), plain(s, form.get("text", ""))
    if not _ID.fullmatch(uid):
        return 400, {"detail": "Bad request"}
    if not text:
        return 200, {"response_type": "ephemeral", "text": f"Type your idea after the command, like `{form.get('command') or '/idea'} a weekly summary email`."}
    _later(_from_command, s, conn, uid, text, form.get("response_url"))
    return 200, None


def receive(s: Settings, connector_id: str, headers: dict, raw: bytes) -> tuple[int, dict | None]:
    """Handle one request from Slack. Returns the status and the JSON to answer with, if any."""
    conn = connectors.get(s, connector_id) if _CARD.fullmatch(connector_id) else None
    if not conn or conn.kind != "slack" or not conn.enabled:
        return 404, {"detail": "Not found"}
    stamp, signature = headers.get("x-slack-request-timestamp", ""), headers.get("x-slack-signature", "")
    if not conn.secrets.get("signing_secret"):
        # Slack only gives out the signing secret once the app exists, but it checks the Request URL while the app is
        # being made. So until the secret is saved, answer that one check and nothing else. It echoes back what Slack
        # sent, which is no use to anyone but Slack.
        try:
            body = json.loads(raw) if headers.get("content-type", "").startswith("application/json") else None
        except ValueError:
            body = None
        if isinstance(body, dict) and body.get("type") == "url_verification" and isinstance(body.get("challenge"), str):
            return 200, {"challenge": body["challenge"][:200]}
    if not verify(conn.secrets.get("signing_secret", ""), stamp, signature, raw):
        return 401, {"detail": "The request isn't signed with this connector's signing secret"}
    once = (conn.id, stamp, signature)
    if not _fresh(once):
        return 200, None  # an exact copy of a request already handled
    try:
        with service.through("slack"):
            if headers.get("content-type", "").startswith("application/json"):
                return _event(s, conn, json.loads(raw))
            form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8"), keep_blank_values=True, max_num_fields=100).items()}
            if "payload" in form:
                return _interaction(s, conn, json.loads(form["payload"]))
            if "command" in form:
                return _command(s, conn, form)
    except ValueError:  # bad JSON, bad UTF-8, too many fields
        pass
    except _Busy:
        # Nothing was done with it, so let Slack send it again.
        _forget(once)
        return 503, {"detail": "Busy. Try again shortly."}
    return 400, {"detail": "Bad request"}


# ---------- the connector ----------

def test(s: Settings, conn: Conn) -> str:
    who = _api(conn, "auth.test", {})
    line = f"Connected to {who.get('team') or 'Slack'} as {who.get('user') or 'the app'}."
    try:
        channel = _api(conn, "conversations.info", {"channel": conn.values["channel"]}).get("channel") or {}
    except ConnectorError as e:
        raise ConnectorError(f"{line} {e}")
    if channel.get("is_member") is False:
        raise ConnectorError(f"{line} The app isn't in #{channel.get('name') or 'the ideas channel'} yet. In that channel, type /invite and pick the app.")
    line += f" Ideas come from #{channel.get('name') or conn.values['channel']}."
    if not conn.secrets.get("signing_secret"):
        line += " Add the signing secret too: without it, nothing Slack sends is accepted."
    return line


MANIFEST = """display_information:
  name: Inrface
  description: Send ideas to Inrface
features:
  bot_user:
    display_name: Inrface
    always_online: true
  shortcuts:
    - name: Send to Inrface
      type: message
      callback_id: inr_send
      description: Turn this message into an idea
  slash_commands:
    - command: /idea
      url: {url}
      description: Send an idea to Inrface
      usage_hint: your idea
      should_escape: false
oauth_config:
  scopes:
    bot:
      - commands
      - chat:write
      - channels:history
      - groups:history
      - channels:read
      - groups:read
      - users:read
      - users:read.email
settings:
  event_subscriptions:
    request_url: {url}
    bot_events:
      - message.channels
      - message.groups
  interactivity:
    is_enabled: true
    request_url: {url}
  org_deploy_enabled: false
  socket_mode_enabled: false
  token_rotation_enabled: false
"""


def notes(s: Settings, connector_id: str) -> list[dict]:
    # Without PUBLIC_URL the server doesn't know its own address, so the Settings screen fills it in.
    url = f"{s.public_url or '{origin}'}/hooks/slack/{connector_id}"
    return [{"label": "Request URL", "value": url, "show": True},
            {"label": "App manifest (YAML)", "value": MANIFEST.replace("{url}", url), "show": False}]


register(Kind(
    id="slack", label="Slack", group="chat",
    summary="Ideas come in from a Slack channel, the /idea command and a message shortcut. Receivers file them from the thread. Slack never starts a build.",
    docs="/docs/slack",
    fields=(
        Field("channel", "Ideas channel ID", placeholder="C0123ABCDEF", pattern=r"[CG][A-Z0-9]{6,20}", max=21,
              help="Every new message in this channel becomes a card. In Slack, open the channel, tap its name at the top, and scroll to the bottom of the details. The ID starts with C."),
        Field("signing_secret", "Signing secret", secret=True, required=False, pattern=r"[A-Za-z0-9]{20,100}", max=100,
              help="From the Slack app's Basic Information page. Leave blank until the Slack app exists."),
        Field("bot_token", "Bot token", secret=True, required=False, placeholder="xoxb-...", pattern=r"xoxb-[A-Za-z0-9-]{20,250}", max=255,
              help="From the Slack app's Install App page, after installing it. Leave blank until then."),
    ),
    test=test, to_thread=to_thread, notes=notes,
))
