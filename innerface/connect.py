"""Letting an AI app act for a signed-in person, when the app does its own sign-in (AUTH_MODE=builtin).

Behind Cloudflare Access, Cloudflare does this part. Here the app is its own OAuth 2.1 authorization server:
an AI app registers itself, sends the person to this app to agree, and gets a token that works only on /mcp.

What keeps it safe:
- An AI app can only be sent back to addresses on a short list (CONNECTOR_REDIRECTS), which names Claude and ChatGPT.
- The person sees which app is asking and has to press Allow while signed in.
- The code handed back works once, for a few minutes, and only with the proof (PKCE) the app sent when it asked.
- Tokens are stored as hashes. A token for /mcp lasts an hour; the one that renews it is swapped on every use,
  and using an old one a second time ends the connection. So does using a code a second time.
- The token is not accepted by the web app's own API, so an AI app can't reach the Settings screen.

The request handling (parameter checks, PKCE, client checks) is the MCP SDK's. This module is its storage and policy.

A refusal is never raised while the database is open. The SDK's errors can't be changed once made, which the
database's own clean-up on the way out tries to do, and anything deleted just before the refusal would be put back.
"""
import json
import logging
import re
import secrets
import time
import unicodedata

from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError, RefreshToken,
                                      RegistrationError, TokenError, construct_redirect_uri)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from . import db
from .config import Settings
from .service import ServiceError
from .signin import hashed

log = logging.getLogger("innerface")

ACCESS_SECONDS = 3600
REFRESH_SECONDS = 30 * 86400
CODE_SECONDS = 300
REQUEST_SECONDS = 600
MAX_CLIENTS = 500
MAX_PENDING = 500
MAX_PENDING_EACH = 10   # per registered app; its oldest is dropped
MAX_GRANTS = 20   # per person; the least recently used is dropped
UNUSED_SECONDS = 86400         # a registration nobody went on to use
IDLE_SECONDS = 90 * 86400      # a registration that was used once and has no connection left
SCOPE = "ideas"
_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")                 # what PKCE with S256 always gives
_UNDER = re.compile(r"[A-Za-z0-9._~-]+(/[A-Za-z0-9._~-]+)*")  # what may follow a list entry that ends in *


class Grant(RefreshToken):
    grant_id: str = ""
    spent: bool = False


def resource_url(s: Settings) -> str:
    return s.public_url + "/mcp"


def _allowed_redirect(s: Settings, uri: str) -> bool:
    # A list entry that ends in * allows what follows it, so anything that could climb back out of that path is refused,
    # and what follows may only be plain path parts: no query string, no empty part and nothing percent-encoded.
    if any(mark in uri for mark in ("..", "\\", "#", "@", "%2e", "%2E")):
        return False
    for a in s.connector_redirects:
        if uri == a or (a.endswith("*") and uri.startswith(a[:-1]) and _UNDER.fullmatch(uri[len(a) - 1:])):
            return True
    return False


def _name(client: OAuthClientInformationFull) -> str:
    """What the app calls itself, cut to something that fits on a button. It is the app's own claim, shown as plain text.
    Characters that don't print are dropped: they could reorder the name on the page or write lines into the log."""
    plain = "".join(ch for ch in client.client_name or "" if ch.isspace() or not unicodedata.category(ch).startswith("C"))
    return " ".join(plain.split())[:60] or "An AI app"


def _sweep(c) -> None:
    """Clear out what nobody is using. Anyone can register, so this is what keeps the list from filling up."""
    c.execute("DELETE FROM oauth_requests WHERE created_at < ?", (db.ago(REQUEST_SECONDS),))
    c.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (time.time(),))
    c.execute("DELETE FROM oauth_tokens WHERE expires_at < ?", (time.time(),))
    # A connection with no token left can't be renewed, so it is over.
    c.execute("DELETE FROM oauth_grants WHERE id NOT IN (SELECT grant_id FROM oauth_tokens)")
    busy = "SELECT client_id FROM oauth_grants UNION SELECT client_id FROM oauth_requests UNION SELECT client_id FROM oauth_codes"
    # An app that has connected someone keeps its registration for a while after the connection ends, so it can connect again.
    c.execute(f"DELETE FROM oauth_clients WHERE used_at IS NULL AND created_at < ? AND client_id NOT IN ({busy})", (db.ago(UNUSED_SECONDS),))
    c.execute(f"DELETE FROM oauth_clients WHERE used_at < ? AND client_id NOT IN ({busy})", (db.ago(IDLE_SECONDS),))


class Provider:
    """Storage and policy for the SDK's OAuth handlers."""

    def __init__(self, s: Settings):
        self.s = s

    # --- apps registering themselves ---

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with db.connect(self.s) as c:
            row = c.execute("SELECT info FROM oauth_clients WHERE client_id=?", (client_id,)).fetchone()
        return OAuthClientInformationFull.model_validate_json(row["info"]) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        uris = [str(u) for u in client_info.redirect_uris or []]
        if not uris or not all(_allowed_redirect(self.s, u) for u in uris):
            raise RegistrationError("invalid_redirect_uri", "This install only connects to AI apps on its list. Whoever runs it can add an address to CONNECTOR_REDIRECTS.")
        with db.connect(self.s) as c:
            _sweep(c)
            full = c.execute("SELECT COUNT(*) AS n FROM oauth_clients").fetchone()["n"] >= MAX_CLIENTS
            if not full:
                c.execute("INSERT INTO oauth_clients(client_id, info, created_at) VALUES (?,?,?)", (client_info.client_id, client_info.model_dump_json(), db.now()))
        if full:
            raise RegistrationError("invalid_client_metadata", "Too many apps have registered lately. Try again tomorrow.")

    # --- asking the person ---

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        s = self.s
        if params.resource and params.resource.rstrip("/") not in (resource_url(s), s.public_url):
            raise AuthorizeError("invalid_target", "That token would be for a different address than this one.")
        if not _CHALLENGE.fullmatch(params.code_challenge or ""):
            raise AuthorizeError("invalid_request", "A code_challenge made with S256 is required.")
        rid = secrets.token_urlsafe(32)
        with db.connect(s) as c:
            c.execute("DELETE FROM oauth_requests WHERE created_at < ?", (db.ago(REQUEST_SECONDS),))
            # One app can't hold every place in the queue: past a few, its oldest request gives way.
            old = c.execute("SELECT id_hash FROM oauth_requests WHERE client_id=? ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET ?",
                            (client.client_id, MAX_PENDING_EACH - 1)).fetchall()
            c.executemany("DELETE FROM oauth_requests WHERE id_hash=?", [(r["id_hash"],) for r in old])
            full = c.execute("SELECT COUNT(*) AS n FROM oauth_requests").fetchone()["n"] >= MAX_PENDING
            if not full:
                c.execute("INSERT INTO oauth_requests(id_hash, client_id, params, created_at) VALUES (?,?,?,?)",
                          (hashed(rid), client.client_id, params.model_dump_json(), db.now()))
        if full:
            raise AuthorizeError("temporarily_unavailable", "Too many requests are waiting. Try again in a few minutes.")
        return f"{s.public_url}/connect?req={rid}"

    # --- codes ---

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        with db.connect(self.s) as c:
            row = c.execute("SELECT * FROM oauth_codes WHERE code_hash=? AND client_id=?", (hashed(authorization_code), client.client_id)).fetchone()
            if row is None:
                # A code works once. If this one already made a connection, a copy of it got out, and that connection ends.
                if c.execute("DELETE FROM oauth_grants WHERE code_hash=?", (hashed(authorization_code),)).rowcount:
                    log.warning("an AI app connection was ended: the code that made it was used a second time")
                return None
        p = json.loads(row["params"])
        return AuthorizationCode(code=authorization_code, scopes=p.get("scopes") or [SCOPE], expires_at=row["expires_at"], client_id=row["client_id"],
                                 code_challenge=p["code_challenge"], redirect_uri=AnyUrl(p["redirect_uri"]),
                                 redirect_uri_provided_explicitly=p["redirect_uri_provided_explicitly"], resource=p.get("resource"), subject=row["email"])

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        s, email, code_hash = self.s, authorization_code.subject, hashed(authorization_code.code)
        tokens, why = None, ""
        with db.connect(s) as c:
            # One use. If two requests race, only one of them deletes the row, and the connection the other made ends.
            if c.execute("DELETE FROM oauth_codes WHERE code_hash=? AND client_id=?", (code_hash, client.client_id)).rowcount != 1:
                c.execute("DELETE FROM oauth_grants WHERE code_hash=?", (code_hash,))
                why = "That code has been used"
            elif not s.projects_for(email):
                why = "That person no longer has access"
            else:
                gid = db.new_id()
                old = c.execute("SELECT id FROM oauth_grants WHERE email=? ORDER BY used_at DESC LIMIT -1 OFFSET ?", (email, MAX_GRANTS - 1)).fetchall()
                c.executemany("DELETE FROM oauth_grants WHERE id=?", [(r["id"],) for r in old])
                c.execute("INSERT INTO oauth_grants(id, client_id, client_name, email, scopes, created_at, used_at, code_hash) VALUES (?,?,?,?,?,?,?,?)",
                          (gid, client.client_id, _name(client), email, " ".join(authorization_code.scopes), db.now(), db.now(), code_hash))
                c.execute("UPDATE oauth_clients SET used_at=? WHERE client_id=?", (db.now(), client.client_id))
                tokens = _issue(c, gid, authorization_code.scopes)
        if tokens is None:
            raise TokenError("invalid_grant", why)
        log.info("%s connected an AI app (%s)", email, _name(client))
        return tokens

    # --- renewing ---

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> Grant | None:
        with db.connect(self.s) as c:
            row = c.execute("SELECT t.*, g.client_id, g.email, g.scopes FROM oauth_tokens t JOIN oauth_grants g ON g.id=t.grant_id "
                            "WHERE t.token_hash=? AND t.kind='refresh' AND g.client_id=?", (hashed(refresh_token), client.client_id)).fetchone()
            if row is None:
                return None
            if row["spent"]:
                # A renewal token is swapped each time it is used. Seeing an old one again means a copy got out.
                c.execute("DELETE FROM oauth_grants WHERE id=?", (row["grant_id"],))
                log.warning("an AI app connection for %s was ended: an old renewal token was used a second time", row["email"])
                return None
        return Grant(token=refresh_token, client_id=row["client_id"], scopes=row["scopes"].split(), expires_at=int(row["expires_at"]),
                     subject=row["email"], grant_id=row["grant_id"])

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: Grant, scopes: list[str]) -> OAuthToken:
        s, tokens, why = self.s, None, ""
        with db.connect(s) as c:
            if c.execute("UPDATE oauth_tokens SET spent=1 WHERE token_hash=? AND kind='refresh' AND spent=0", (hashed(refresh_token.token),)).rowcount != 1:
                why = "That token has been used"
            elif not s.projects_for(refresh_token.subject or ""):
                why = "That person no longer has access"
            if why:
                # Either way the connection is over, and stays over if the person is added back.
                c.execute("DELETE FROM oauth_grants WHERE id=?", (refresh_token.grant_id,))
            else:
                c.execute("DELETE FROM oauth_tokens WHERE grant_id=? AND kind='access'", (refresh_token.grant_id,))
                c.execute("UPDATE oauth_grants SET used_at=? WHERE id=?", (db.now(), refresh_token.grant_id))
                c.execute("UPDATE oauth_clients SET used_at=? WHERE client_id=?", (db.now(), client.client_id))
                tokens = _issue(c, refresh_token.grant_id, scopes or refresh_token.scopes)
        if tokens is None:
            log.warning("an AI app connection for %s was ended: %s", refresh_token.subject, why.lower())
            raise TokenError("invalid_grant", why)
        return tokens

    # --- tokens in use ---

    async def load_access_token(self, token: str) -> AccessToken | None:
        found = lookup(self.s, token)
        return AccessToken(token=token, client_id=found["client_id"], scopes=found["scopes"].split(), expires_at=int(found["expires_at"]),
                           resource=resource_url(self.s), subject=found["email"]) if found else None

    async def revoke_token(self, token) -> None:
        hang_up(self.s, token.client_id, token.token)


def hang_up(s: Settings, client_id: str, token: str) -> None:
    """An AI app ending its own connection. Either of its tokens will do, and it ends the whole connection."""
    with db.connect(s) as c:
        c.execute("DELETE FROM oauth_grants WHERE client_id=? AND id IN (SELECT grant_id FROM oauth_tokens WHERE token_hash=?)", (client_id, hashed(token)))


def _issue(c, grant_id: str, scopes: list[str]) -> OAuthToken:
    access, refresh, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), time.time()
    c.execute("DELETE FROM oauth_tokens WHERE expires_at < ?", (now,))
    c.execute("INSERT INTO oauth_tokens(token_hash, kind, grant_id, expires_at) VALUES (?,?,?,?)", (hashed(access), "access", grant_id, now + ACCESS_SECONDS))
    c.execute("INSERT INTO oauth_tokens(token_hash, kind, grant_id, expires_at) VALUES (?,?,?,?)", (hashed(refresh), "refresh", grant_id, now + REFRESH_SECONDS))
    return OAuthToken(access_token=access, expires_in=ACCESS_SECONDS, refresh_token=refresh, scope=" ".join(scopes))


def lookup(s: Settings, token: str) -> dict | None:
    """Who a token for /mcp speaks for, if it is still good."""
    if not token:
        return None
    with db.connect(s) as c:
        row = c.execute("SELECT t.expires_at, g.id AS grant_id, g.client_id, g.email, g.scopes, g.used_at FROM oauth_tokens t JOIN oauth_grants g ON g.id=t.grant_id "
                        "WHERE t.token_hash=? AND t.kind='access' AND t.expires_at > ?", (hashed(token), time.time())).fetchone()
        if row is None:
            return None
        if row["used_at"] < db.ago(3600):
            c.execute("UPDATE oauth_grants SET used_at=? WHERE id=?", (db.now(), row["grant_id"]))
    return dict(row)


def email_from_headers(s: Settings, headers) -> str | None:
    kind, _, token = (headers.get("authorization") or "").partition(" ")
    found = lookup(s, token.strip()) if kind.lower() == "bearer" else None
    return found["email"] if found else None


# ---------- the page where the person agrees ----------

async def waiting(s: Settings, request_id: str) -> dict | None:
    """What a pending request is asking for, to show on the page. None if it is gone or too old."""
    with db.connect(s) as c:
        row = c.execute("SELECT * FROM oauth_requests WHERE id_hash=? AND created_at >= ?", (hashed(request_id or ""), db.ago(REQUEST_SECONDS))).fetchone()
    if row is None:
        return None
    client = await Provider(s).get_client(row["client_id"])
    if client is None:
        return None
    params = json.loads(row["params"])
    return {"app": _name(client), "back_to": params["redirect_uri"].split("/")[2], "params": params, "client_id": row["client_id"]}


async def answer(s: Settings, user: dict, request_id: str, allow: bool) -> str:
    """Record the person's answer and return where to send their browser."""
    asked = await waiting(s, request_id)
    code = secrets.token_urlsafe(32)
    with db.connect(s) as c:
        # One use, whichever way they answer.
        gone = c.execute("DELETE FROM oauth_requests WHERE id_hash=?", (hashed(request_id or ""),)).rowcount
        if asked is not None and gone == 1 and allow:
            c.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (time.time(),))
            c.execute("INSERT INTO oauth_codes(code_hash, client_id, email, params, expires_at) VALUES (?,?,?,?,?)",
                      (hashed(code), asked["client_id"], user["email"], json.dumps(asked["params"]), time.time() + CODE_SECONDS))
    if asked is None or gone != 1:
        raise ServiceError(410, "That request has run out. Go back to your AI app and connect again.")
    p = asked["params"]
    if not allow:
        return construct_redirect_uri(p["redirect_uri"], error="access_denied", state=p.get("state"))
    return construct_redirect_uri(p["redirect_uri"], code=code, state=p.get("state"))


# ---------- what a person has connected ----------

def mine(s: Settings, email: str) -> list:
    with db.connect(s) as c:
        rows = c.execute("SELECT * FROM oauth_grants WHERE email=? ORDER BY used_at DESC", (email,)).fetchall()
    return [{"id": r["id"], "app": r["client_name"], "since": r["created_at"], "last_used": r["used_at"]} for r in rows]


def end(s: Settings, email: str, grant_id: str) -> bool:
    with db.connect(s) as c:
        return c.execute("DELETE FROM oauth_grants WHERE id=? AND email=?", (grant_id, email)).rowcount > 0


def metadata(s: Settings) -> dict:
    u = s.public_url
    return {"issuer": u, "authorization_endpoint": u + "/authorize", "token_endpoint": u + "/token", "registration_endpoint": u + "/register",
            "revocation_endpoint": u + "/revoke", "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "revocation_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"]}


def resource_metadata(s: Settings) -> dict:
    return {"resource": resource_url(s), "authorization_servers": [s.public_url], "bearer_methods_supported": ["header"], "resource_name": "Inrface"}
