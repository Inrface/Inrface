"""Signing people in without anything in front of the app (AUTH_MODE=builtin).

A person proves who they are to a sign-in provider such as Google, and the app keeps a session for their browser.
The app never sees a password. Who may come in is still decided by the projects: an address that is in no project
is turned away after signing in, exactly as it is behind Cloudflare Access.

Providers are looked up in a registry. Google ships here. Others can be added by a separate package that names
an entry point in the group "inrface.signin": a function that takes the settings and returns a list of providers.
"""
import base64
import hashlib
import hmac
import logging
import re
import secrets
import time
from dataclasses import dataclass
from importlib.metadata import entry_points
from urllib.parse import urlencode

import httpx
import jwt

from . import db, plans
from .config import Settings

log = logging.getLogger("innerface")

COOKIE = "inrface"            # carries the session; sent as __Host-inrface over https
PENDING_COOKIE = "inrface-signin"  # ties a sign-in that is under way to the browser that started it
STATE_SECONDS = 600
MAX_PENDING = 500
MAX_SESSIONS = 20             # per person; the oldest is dropped
SEEN_EVERY = 3600             # how often a session's "last used" time is written
_ID = re.compile(r"^[a-z0-9-]{1,20}\Z")
_NEXT = re.compile(r"^/connect\?req=[A-Za-z0-9_-]{20,80}\Z")


class SignInError(Exception):
    """A sign-in that didn't finish. `code` picks the sentence shown on the sign-in page."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


MESSAGES = {
    "failed": "Sign-in didn't finish. Try again.",
    "expired": "That sign-in took too long. Try again.",
    "unverified": "That account's email address hasn't been confirmed with the provider, so it can't be used here.",
    "noaccess": "That account isn't in any project here. Ask a receiver to add the address you signed in with.",
    "busy": "Too many sign-ins are under way. Try again in a few minutes.",
}


def hashed(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def cookie_name(s: Settings, name: str = COOKIE) -> str:
    # The __Host- prefix makes browsers refuse the cookie unless it is Secure, host-only and for the whole site.
    return ("__Host-" if s.secure_cookies else "") + name


def read_cookie(header: str, name: str) -> str:
    for part in (header or "").split(";"):
        # Only the spaces a browser puts between cookies are trimmed, so a name padded with anything else isn't this one.
        k, _, v = part.strip(" \t").partition("=")
        if k == name:
            return v
    return ""


# ---------- providers ----------

@dataclass(frozen=True)
class OIDCProvider:
    """A provider that speaks OpenID Connect. The same class serves Google and, from an add-on, any other."""
    id: str
    label: str
    client_id: str
    client_secret: str
    issuers: tuple            # accepted `iss` values
    authorize_url: str
    token_url: str
    jwks_url: str
    scope: str = "openid email"

    def start(self, redirect_uri: str, state: str, nonce: str, verifier: str) -> str:
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        return self.authorize_url + "?" + urlencode({
            "response_type": "code", "client_id": self.client_id, "redirect_uri": redirect_uri, "scope": self.scope,
            "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account"})

    def finish(self, redirect_uri: str, code: str, nonce: str, verifier: str) -> str:
        """Swap the code for the provider's signed statement of who signed in, check it, and return their address."""
        try:
            r = httpx.post(self.token_url, timeout=10, data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                "client_id": self.client_id, "client_secret": self.client_secret, "code_verifier": verifier})
            token = r.json().get("id_token") if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as e:
            raise SignInError("failed", f"{self.id} could not be reached ({type(e).__name__})")
        if not isinstance(token, str):
            raise SignInError("failed", f"{self.id} did not accept the code (HTTP {r.status_code})")
        try:
            # A minute's grace for a clock here that runs a little behind the provider's, so a statement made "just
            # now" isn't taken for one from the future. The grace doesn't stretch how long a statement lasts.
            claims = jwt.decode(token, self._key(token), algorithms=["RS256", "ES256"], audience=self.client_id, leeway=60,
                                options={"require": ["exp", "iat", "aud", "iss", "sub"]})
        except (jwt.PyJWTError, OSError) as e:
            raise SignInError("failed", f"{self.id} sent a statement that is not valid ({type(e).__name__})")
        if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] <= time.time():
            raise SignInError("failed", f"{self.id} sent a statement that has run out")
        if claims.get("iss") not in self.issuers:
            raise SignInError("failed", f"{self.id} sent a statement from a different issuer")
        if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
            raise SignInError("failed", f"{self.id} sent a statement for a different sign-in")
        email = claims.get("email")
        if not isinstance(email, str) or not email.strip():
            raise SignInError("failed", f"{self.id} sent no email address")
        if claims.get("email_verified") is not True:
            raise SignInError("unverified", f"{self.id} has not confirmed the address")
        return email.strip().lower()

    def _key(self, token: str):
        if self.id not in _jwks:
            _jwks[self.id] = jwt.PyJWKClient(self.jwks_url, cache_keys=True, lifespan=3600)
        return _jwks[self.id].get_signing_key_from_jwt(token).key


_jwks: dict[str, jwt.PyJWKClient] = {}


def google(s: Settings) -> OIDCProvider | None:
    if not (s.google_client_id and s.google_client_secret):
        return None
    return OIDCProvider(id="google", label="Google", client_id=s.google_client_id, client_secret=s.google_client_secret,
                        issuers=("https://accounts.google.com", "accounts.google.com"),
                        authorize_url="https://accounts.google.com/o/oauth2/v2/auth", token_url="https://oauth2.googleapis.com/token",
                        jwks_url="https://www.googleapis.com/oauth2/v3/certs")


_told = False


def _added(s: Settings) -> list:
    found = []
    waiting = list(entry_points(group="inrface.signin"))
    if waiting and not plans.has(s, "signin_addons"):
        global _told
        if not _told:
            log.warning("sign-in add-ons are installed but not part of the %s plan: %s", plans.name(s), ", ".join(ep.name for ep in waiting))
            _told = True
        return found
    for ep in waiting:
        try:
            found.extend(ep.load()(s) or [])
        except Exception:
            log.exception("sign-in add-on %s could not be loaded", ep.name)
    return found


def providers(s: Settings) -> dict:
    """The ways to sign in on this install, by id, in the order to show them."""
    out = {}
    for p in [google(s), *_added(s)]:
        if p is not None and _ID.match(getattr(p, "id", "")) and p.id not in out:
            out[p.id] = p
    return out


def callback_url(s: Settings, provider_id: str) -> str:
    return f"{s.public_url}/signin/{provider_id}/back"


# ---------- the round trip ----------

def begin(s: Settings, provider_id: str, next_path: str = "") -> tuple[str, str]:
    """Start a sign-in. Returns where to send the browser, and the secret to leave in its cookie."""
    p = providers(s).get(provider_id)
    if p is None:
        raise SignInError("failed", "no such provider")
    state, browser, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
    with db.connect(s) as c:
        c.execute("DELETE FROM signin_states WHERE created_at < ?", (db.ago(STATE_SECONDS),))
        if c.execute("SELECT COUNT(*) AS n FROM signin_states").fetchone()["n"] >= MAX_PENDING:
            raise SignInError("busy")
        c.execute("INSERT INTO signin_states(state_hash, browser_hash, provider, nonce, verifier, next, created_at) VALUES (?,?,?,?,?,?,?)",
                  (hashed(state), hashed(browser), p.id, nonce, verifier, next_path if _NEXT.match(next_path or "") else "", db.now()))
    return p.start(callback_url(s, p.id), state, nonce, verifier), browser


def complete(s: Settings, provider_id: str, state: str, code: str, browser: str, agent: str = "") -> tuple[str, str]:
    """Finish a sign-in. Returns the session secret for the browser's cookie, and where to go next."""
    with db.connect(s) as c:
        row = c.execute("SELECT * FROM signin_states WHERE state_hash=?", (hashed(state or ""),)).fetchone()
        # One use: whatever happens next, this sign-in can't be replayed. If two replies race, only one deletes the row.
        won = c.execute("DELETE FROM signin_states WHERE state_hash=?", (hashed(state or ""),)).rowcount == 1
        c.execute("DELETE FROM signin_states WHERE created_at < ?", (db.ago(STATE_SECONDS),))
    if row is None or not won or row["provider"] != provider_id:
        raise SignInError("failed", "no sign-in is under way for that reply")
    if row["created_at"] < db.ago(STATE_SECONDS):
        raise SignInError("expired")
    # The reply has to come back to the browser that started it. Otherwise a link could sign someone in as somebody else.
    if not browser or not hmac.compare_digest(row["browser_hash"], hashed(browser)):
        raise SignInError("failed", "the reply came to a different browser than the one that started")
    p = providers(s).get(provider_id)
    if p is None or not code:
        raise SignInError("failed", "the provider sent no code")
    email = p.finish(callback_url(s, p.id), code, row["nonce"], row["verifier"])
    if not s.projects_for(email):
        # Safe to write: the provider vouched for the address.
        log.warning("login refused: %s signed in with %s but is not %s on this install", email, p.id, "in any project" if email in s.people else "listed")
        raise SignInError("noaccess")
    return open_session(s, email, p.id, agent), row["next"] or "/"


# ---------- sessions ----------

def open_session(s: Settings, email: str, provider_id: str, agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    with db.connect(s) as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (db.now(),))
        old = c.execute("SELECT id FROM sessions WHERE email=? ORDER BY seen_at DESC LIMIT -1 OFFSET ?", (email, MAX_SESSIONS - 1)).fetchall()
        c.executemany("DELETE FROM sessions WHERE id=?", [(r["id"],) for r in old])
        c.execute("INSERT INTO sessions(id, token_hash, email, provider, agent, created_at, seen_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
                  (db.new_id(), hashed(token), email, provider_id, _device(agent), db.now(), db.now(), db.ago(-s.session_days * 86400)))
    log.info("%s signed in with %s", email, provider_id)
    return token


def session(s: Settings, token: str) -> dict | None:
    """The session this cookie belongs to, if it is still good."""
    if not token:
        return None
    with db.connect(s) as c:
        row = c.execute("SELECT * FROM sessions WHERE token_hash=? AND expires_at > ?", (hashed(token), db.now())).fetchone()
        if row is None:
            return None
        if row["seen_at"] < db.ago(SEEN_EVERY):
            c.execute("UPDATE sessions SET seen_at=? WHERE id=?", (db.now(), row["id"]))
    return dict(row)


def session_from_headers(s: Settings, headers) -> dict | None:
    return session(s, read_cookie(headers.get("cookie", ""), cookie_name(s)))


def close_session(s: Settings, token: str) -> None:
    with db.connect(s) as c:
        # Alerts turned on in this browser stop with it.
        c.execute("DELETE FROM push_subscriptions WHERE session_id != '' AND session_id IN (SELECT id FROM sessions WHERE token_hash=?)", (hashed(token or ""),))
        c.execute("DELETE FROM sessions WHERE token_hash=?", (hashed(token or ""),))


def mine(s: Settings, email: str, current_id: str = "") -> list:
    with db.connect(s) as c:
        rows = c.execute("SELECT * FROM sessions WHERE email=? AND expires_at > ? ORDER BY seen_at DESC", (email, db.now())).fetchall()
    return [{"id": r["id"], "device": r["agent"] or "A browser", "since": r["created_at"], "last_used": r["seen_at"], "this_one": r["id"] == current_id} for r in rows]


def end(s: Settings, email: str, session_id: str) -> bool:
    with db.connect(s) as c:
        if c.execute("DELETE FROM sessions WHERE id=? AND email=?", (session_id, email)).rowcount == 0:
            return False
        c.execute("DELETE FROM push_subscriptions WHERE session_id=? AND email=?", (session_id, email))
        return True


def forget(s: Settings, email: str) -> None:
    """End everything a person is signed in to: their browsers, their AI apps and the alerts their browsers get.
    Used when they leave their last project."""
    with db.connect(s) as c:
        c.execute("DELETE FROM sessions WHERE email=?", (email,))
        c.execute("DELETE FROM oauth_grants WHERE email=?", (email,))
        c.execute("DELETE FROM oauth_codes WHERE email=?", (email,))
        c.execute("DELETE FROM push_subscriptions WHERE email=?", (email,))


def tidy(s: Settings) -> None:
    """Clear out what has run out, and end everything held by someone who is in no project any more. Taking a person
    out in Settings does that at once; this catches someone taken out of the settings file while the app was stopped."""
    with db.connect(s) as c:
        c.execute("DELETE FROM signin_states WHERE created_at < ?", (db.ago(STATE_SECONDS),))
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (db.now(),))
        held = [r["email"] for r in c.execute("SELECT email FROM sessions UNION SELECT email FROM oauth_grants UNION SELECT email FROM oauth_codes")]
    for email in held:
        if not s.projects_for(email):
            forget(s, email)
            log.warning("%s is in no project any more, so their browsers and AI apps were signed out", email)


def _device(agent: str) -> str:
    """A few plain words for the list of places someone is signed in. The full header is never kept."""
    a = agent or ""
    kind = next((name for mark, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Macintosh", "Mac"), ("Windows", "Windows"), ("Linux", "Linux")) if mark in a), "")
    browser = next((name for mark, name in (("Edg/", "Edge"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari")) if mark in a), "")
    return " on ".join(x for x in (browser, kind) if x)


def set_cookie_header(s: Settings, name: str, value: str, max_age: int) -> str:
    return (f"{cookie_name(s, name)}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax" + ("; Secure" if s.secure_cookies else ""))


def ready(s: Settings) -> None:
    """Said once at start, so a missing provider shows up in the log and not as a blank sign-in page."""
    if s.auth_mode != "builtin":
        return
    tidy(s)
    found = providers(s)
    if not found:
        log.warning("AUTH_MODE=builtin but there is no way to sign in. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.")
    else:
        log.info("built-in sign-in is on, with: %s. This way of signing in is new and has not had an outside security review.", ", ".join(found))
