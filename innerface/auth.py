"""Identify the viewer from Cloudflare Access's signed token, and allow only configured people.

The same check serves the web app and the MCP endpoint: with Access-managed OAuth, MCP clients'
requests reach the origin carrying the same signed Cf-Access-Jwt-Assertion header as browsers.
"""
import logging
import time
from collections.abc import Mapping

import jwt
from fastapi import Depends, HTTPException, Request

from .config import Settings, get_settings

log = logging.getLogger("innerface")

_jwks: jwt.PyJWKClient | None = None

# Why a login was refused goes in the server's log, so whoever runs it can tell a wrong account from a
# misconfigured audience. The same reason is written at most once a minute, so it can't fill the log.
_said: dict[str, float] = {}


def _refused(reason: str) -> None:
    now = time.monotonic()
    if now - _said.get(reason, -60.0) < 60:
        return
    if len(_said) > 200:
        _said.clear()
    _said[reason] = now
    log.warning("login refused: %s", reason)


def _why(e: Exception, token: str, s: Settings) -> str:
    """A refused token, described without repeating the token or anything in it that isn't a plain id."""
    if isinstance(e, jwt.InvalidAudienceError):
        try:
            aud = jwt.decode(token, options={"verify_signature": False}).get("aud")
        except jwt.PyJWTError:
            aud = None
        got = ", ".join(a[:8] for a in ([aud] if isinstance(aud, str) else aud or []) if isinstance(a, str) and a.isalnum())
        return (f"the token is for a different Access application (it names {got or 'no usable audience'}, and CF_ACCESS_AUD starts {s.access_aud[:8]}). "
                "Check which Access application covers this address.")
    if isinstance(e, NoEmail):
        return "the token has no email address, so it is a machine login (a service token) and not a person"
    if isinstance(e, jwt.ExpiredSignatureError):
        return "the token has expired"
    if isinstance(e, jwt.InvalidIssuerError):
        return "the token comes from a different Cloudflare team than CF_ACCESS_TEAM"
    if isinstance(e, (jwt.PyJWKClientError, OSError)):
        return f"Cloudflare's signing keys could not be fetched ({type(e).__name__})"
    return f"the token is not valid ({type(e).__name__})"


class NoEmail(jwt.InvalidTokenError):
    pass


class NotAllowed(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _signing_key(token: str, team: str):
    global _jwks
    if _jwks is None:
        _jwks = jwt.PyJWKClient(f"https://{team}.cloudflareaccess.com/cdn-cgi/access/certs", cache_keys=True, lifespan=3600)
    return _jwks.get_signing_key_from_jwt(token).key


def verify_access_claims(token: str, s: Settings) -> dict:
    """Return the claims of a valid Access token for this app, or raise jwt.PyJWTError.
    Service tokens (machine logins) are valid too; they carry common_name instead of email."""
    return jwt.decode(
        token,
        _signing_key(token, s.access_team),
        algorithms=["RS256"],
        audience=s.access_aud,
        issuer=f"https://{s.access_team}.cloudflareaccess.com",
        options={"require": ["exp", "iat", "aud", "iss"]},
    )


def verify_access_token(token: str, s: Settings) -> str:
    """Return the verified lowercase email, or raise jwt.PyJWTError."""
    claims = verify_access_claims(token, s)
    email = claims.get("email")
    if not isinstance(email, str) or not email:
        raise NoEmail("no email claim")
    return email.strip().lower()


def user_from_headers(headers: Mapping[str, str], s: Settings, via: str = "web") -> dict:
    """Return the signed-in person, or raise NotAllowed.

    `via` matters only to built-in sign-in, where the web app and AI apps hold different proof: a browser has
    a session cookie, an AI app has a token for /mcp, and neither is accepted in the other's place."""
    if s.auth_mode == "dev":
        email = (headers.get("x-dev-user") or s.dev_user or next(iter(s.people))).strip().lower()
    elif s.auth_mode == "builtin":
        from . import connect, signin
        if via == "mcp":
            email = connect.email_from_headers(s, headers)
        else:
            found = signin.session_from_headers(s, headers)
            email = found["email"] if found else None
        if not email:
            raise NotAllowed(401, "Not signed in")
    else:
        token = headers.get("cf-access-jwt-assertion")
        if not token:
            _refused("the request has no Cloudflare Access token, so it did not come through an Access application")
            raise NotAllowed(401, "Not signed in")
        try:
            email = verify_access_token(token, s)
        except (jwt.PyJWTError, OSError) as e:
            _refused(_why(e, token, s))
            raise NotAllowed(401, "Not signed in")
    person = s.people.get(email)
    projects = {p.id: p.role(email) for p in s.projects_for(email)} if person else {}
    if not projects:
        if s.auth_mode == "builtin":
            _refused(f"{email} is signed in but is no longer in any project on this install")
            # Whatever they still hold ends here, so being added back later means signing in again.
            signin.forget(s, email)
        elif s.auth_mode != "dev":
            # The address is safe to write: it comes from a token Cloudflare signed.
            _refused(f"{email} signed in through Cloudflare but is not {'in any project' if person else 'listed'} on this install")
        raise NotAllowed(403, "This account doesn't have access")
    return {"email": email, "name": person.name, "projects": projects}


def require_access(headers: Mapping[str, str], s: Settings) -> None:
    """For the intake API: the request must have come through Access (a service token is fine).
    The caller then proves which source it is with its own key. With built-in sign-in nothing stands in front
    of the app, so that key is the whole proof."""
    if s.auth_mode in ("dev", "builtin"):
        return
    token = headers.get("cf-access-jwt-assertion")
    if not token:
        raise NotAllowed(401, "Not signed in to Cloudflare Access. Send the service token headers.")
    try:
        verify_access_claims(token, s)
    except (jwt.PyJWTError, OSError):
        raise NotAllowed(401, "Not signed in to Cloudflare Access. Send the service token headers.")


def current_user(request: Request, s: Settings = Depends(get_settings)) -> dict:
    try:
        return user_from_headers(request.headers, s)
    except NotAllowed as e:
        # Tells the web app where to send someone whose session has ended.
        raise HTTPException(e.status, str(e), headers={"X-Sign-In": "/signin"} if e.status == 401 and s.auth_mode == "builtin" else None)
