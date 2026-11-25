"""Web Push. Keys are created once and kept in the data folder."""
import base64
import json
import logging
import os
import re
from urllib.parse import urlsplit

import requests

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid02
from pywebpush import WebPushException, webpush

from . import db
from .config import Settings

log = logging.getLogger("innerface.push")

MAX_PER_PERSON = 10

# The server posts to whatever address a browser hands it, so only the browsers' own push services are
# accepted: Chrome, Edge, Brave and other Chromium browsers (FCM), Firefox, Safari and old Edge.
_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com")
_HOST_ENDS = (".push.apple.com", ".notify.windows.com")
_PLAIN = re.compile(r"https://[A-Za-z0-9.-]{1,253}/[A-Za-z0-9._~%!$&'()*+,;=:/?-]{1,1900}")


class BadEndpoint(ValueError):
    pass


def check_endpoint(url: str) -> str:
    """Refuse anything that isn't plainly an address at a known push service.

    Two URL parsers can disagree about where the host ends ("https://a.test\\@fcm.googleapis.com/"), so the
    address is first held to a small alphabet with no @, backslash, port or spaces, and only then parsed.
    """
    if not isinstance(url, str) or not _PLAIN.fullmatch(url):
        raise BadEndpoint("Unrecognized push service")
    host = (urlsplit(url).hostname or "").lower()
    if host != url[len("https://"):].split("/", 1)[0].lower():
        raise BadEndpoint("Unrecognized push service")
    if host not in _HOSTS and not any(host.endswith(end) and len(host) > len(end) for end in _HOST_ENDS):
        raise BadEndpoint("Unrecognized push service")
    return url


class _NoRedirects(requests.Session):
    """A push service has no reason to send the alert somewhere else, so a redirect is never followed."""

    def post(self, url, **kw):
        kw["allow_redirects"] = False
        return super().post(url, **kw)


def subscribe(s: Settings, email: str, endpoint: str, p256dh: str, auth: str, session_id: str = "") -> None:
    """Save a browser's subscription. Raises BadEndpoint. With built-in sign-in, `session_id` is the sign-in it was
    saved under: when that ends, so do the alerts."""
    check_endpoint(endpoint)
    with db.connect(s) as c:
        row = c.execute("SELECT email, p256dh, auth FROM push_subscriptions WHERE endpoint=?", (endpoint,)).fetchone()
        if row and row["email"] != email and (row["p256dh"], row["auth"]) != (p256dh, auth):
            # Someone else's subscription, and the caller doesn't hold its keys: not theirs to take over.
            raise BadEndpoint("That subscription belongs to someone else")
        c.execute(
            "INSERT INTO push_subscriptions(endpoint,email,p256dh,auth,created_at,session_id) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(endpoint) DO UPDATE SET email=excluded.email, p256dh=excluded.p256dh, auth=excluded.auth, session_id=excluded.session_id",
            (endpoint, email, p256dh, auth, db.now(), session_id),
        )
        # Keep the newest few; a browser that re-subscribes leaves its old address behind.
        c.execute(
            "DELETE FROM push_subscriptions WHERE email=? AND endpoint NOT IN "
            "(SELECT endpoint FROM push_subscriptions WHERE email=? ORDER BY created_at DESC, rowid DESC LIMIT ?)",
            (email, email, MAX_PER_PERSON),
        )


def _keyfile(s: Settings):
    return s.data_dir / "vapid_private.pem"


def _ensure_key(s: Settings) -> ec.EllipticCurvePrivateKey:
    path = _keyfile(s)
    if not path.exists():
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def public_key(s: Settings) -> str:
    raw = _ensure_key(s).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def send(s: Settings, email: str, title: str, body: str, card_id: str | None = None, ask: str | None = None) -> None:
    """Best effort. A failed alert must never break the action that caused it. `ask="build"` adds Start build and
    Not now buttons to the alert; pressing one makes the same request the app's own buttons make, so the server
    still checks who is asking."""
    if not s.push_contact:
        log.info("push skipped: PUSH_CONTACT not set")
        return
    with db.connect(s) as c:
        if s.auth_mode == "builtin":
            # A browser that has been signed out, by any route, gets nothing more. The app saves the subscription
            # again the next time someone opens it signed in.
            c.execute("DELETE FROM push_subscriptions WHERE email=? AND session_id NOT IN (SELECT id FROM sessions WHERE email=? AND expires_at > ?)",
                      (email, email, db.now()))
        subs = c.execute("SELECT * FROM push_subscriptions WHERE email=?", (email,)).fetchall()
    if not subs:
        return
    _ensure_key(s)
    vapid = Vapid02.from_file(str(_keyfile(s)))
    data = json.dumps({"title": title, "body": body[:180], "cardId": card_id, "ask": ask})
    session = _NoRedirects()
    for sub in subs:
        try:
            check_endpoint(sub["endpoint"])  # again, in case the row was saved under older rules
        except BadEndpoint:
            with db.connect(s) as c:
                c.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (sub["endpoint"],))
            log.warning("push: dropped a saved subscription whose address isn't a known push service")
            continue
        try:
            webpush(
                subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                data=data,
                vapid_private_key=vapid,
                vapid_claims={"sub": f"mailto:{s.push_contact}"},
                timeout=15,
                requests_session=session,
            )
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                with db.connect(s) as c:
                    c.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (sub["endpoint"],))
            else:
                log.warning("push failed with status %s", status)
        except Exception as e:  # network trouble and similar
            log.warning("push failed: %s", type(e).__name__)
