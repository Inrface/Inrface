"""The pages and addresses for built-in sign-in and for AI apps connecting. Everything here answers 404 unless
AUTH_MODE=builtin, so an install behind Cloudflare Access gains nothing new to reach."""
import html
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.register import RegistrationHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import AuthenticationError, ClientAuthenticator
from mcp.server.auth.settings import ClientRegistrationOptions
from pydantic import BaseModel, Field

from . import connect, signin, voice
from .auth import current_user
from .config import Settings, get_settings

router = APIRouter()
MAX_BODY = 64 * 1024


def builtin(s: Settings = Depends(get_settings)) -> Settings:
    if s.auth_mode != "builtin":
        raise HTTPException(404, "Not found")
    return s


def _page(title: str, body: str, script: bool = False, req: str = "") -> HTMLResponse:
    e = html.escape
    return HTMLResponse(
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{e(title)}</title><link rel="icon" href="/favicon.ico" sizes="any"><link rel="stylesheet" href="/signin.css">'
        + ('<script src="/connect.js" defer></script>' if script else "") +
        f'</head><body data-req="{e(req, quote=True)}"><main><img class="logo light" src="/brand/logo-horizontal.png" alt="Inrface">'
        f'<img class="logo dark" src="/brand/logo-horizontal-dark.png" alt="">{body}</main></body></html>')


def _go(url: str, *cookies: str) -> RedirectResponse:
    r = RedirectResponse(url, status_code=303)
    for c in cookies:
        r.headers.append("Set-Cookie", c)
    return r


# ---------- people signing in ----------

@router.get("/signin", response_class=HTMLResponse)
def signin_page(request: Request, e: str = "", next: str = "", s: Settings = Depends(builtin)):
    next = next if signin._NEXT.match(next or "") else ""
    here = signin.session_from_headers(s, request.headers)
    if here and not s.projects_for(here["email"]):
        # Signed in, but taken out of every project since. They stay on this page, where another account can be used.
        signin.forget(s, here["email"])
        here, e = None, e or "noaccess"
    if not e and here:
        return _go(next or "/")
    found = signin.providers(s)
    esc = html.escape
    said = f'<p class="error" role="alert">{esc(signin.MESSAGES[e])}</p>' if e in signin.MESSAGES else ""
    tail = ("?next=" + quote(next, safe="")) if next else ""
    ways = "".join(f'<a class="btn" href="/signin/{esc(p.id)}{tail}">Continue with {esc(p.label)}</a>' for p in found.values())
    if not ways:
        ways = '<p class="error" role="alert">Sign-in isn\'t set up yet. Whoever runs this install has to add a way to sign in.</p>'
    return _page("Sign in to Inrface", f"<h1>Sign in</h1>{said}{ways}<p class=\"hint\">Only people who have been added to a project can come in.</p>")


@router.get("/signin/{provider_id}")
def signin_start(provider_id: str, next: str = "", s: Settings = Depends(builtin)):
    try:
        url, browser = signin.begin(s, provider_id, next)
    except signin.SignInError as err:
        return _go("/signin?e=" + err.code)
    return _go(url, signin.set_cookie_header(s, signin.PENDING_COOKIE, browser, signin.STATE_SECONDS))


@router.get("/signin/{provider_id}/back")
def signin_back(provider_id: str, request: Request, state: str = "", code: str = "", s: Settings = Depends(builtin)):
    browser = signin.read_cookie(request.headers.get("cookie", ""), signin.cookie_name(s, signin.PENDING_COOKIE))
    before = signin.read_cookie(request.headers.get("cookie", ""), signin.cookie_name(s))
    done = signin.set_cookie_header(s, signin.PENDING_COOKIE, "", 0)
    try:
        token, next = signin.complete(s, provider_id, state, code, browser, request.headers.get("user-agent", ""))
    except signin.SignInError as err:
        if err.code != "noaccess":
            signin.log.warning("sign-in did not finish: %s", err)
        return _go("/signin?e=" + err.code, done)
    # The browser now holds the new sign-in only, so the one it came with is closed and not left behind.
    signin.close_session(s, before)
    return _go(next, done, signin.set_cookie_header(s, signin.COOKIE, token, s.session_days * 86400))


@router.post("/api/signout")
def signout(request: Request, s: Settings = Depends(builtin)):
    signin.close_session(s, signin.read_cookie(request.headers.get("cookie", ""), signin.cookie_name(s)))
    r = JSONResponse({"ok": True})
    r.headers.append("Set-Cookie", signin.set_cookie_header(s, signin.COOKIE, "", 0))
    return r


@router.get("/api/account")
def account(request: Request, user: dict = Depends(current_user), s: Settings = Depends(builtin)):
    here = signin.session_from_headers(s, request.headers)
    return {"browsers": signin.mine(s, user["email"], here["id"] if here else ""), "apps": connect.mine(s, user["email"]),
            "connector_url": connect.resource_url(s)}


@router.delete("/api/account/browsers/{session_id}")
def end_browser(session_id: str, request: Request, user: dict = Depends(current_user), s: Settings = Depends(builtin)):
    if not signin.end(s, user["email"], session_id):
        raise HTTPException(404, "That one is already signed out")
    return account(request, user, s)


@router.delete("/api/account/apps/{grant_id}")
def end_app(grant_id: str, request: Request, user: dict = Depends(current_user), s: Settings = Depends(builtin)):
    if not connect.end(s, user["email"], grant_id):
        raise HTTPException(404, "That one is already disconnected")
    return account(request, user, s)


# ---------- AI apps connecting ----------

class AnswerIn(BaseModel):
    req: str = Field(min_length=20, max_length=80)
    allow: bool


@router.get("/connect", response_class=HTMLResponse)
async def connect_page(request: Request, req: str = "", s: Settings = Depends(builtin)):
    if not signin._NEXT.match("/connect?req=" + req):
        return _page("Connect an AI app", "<h1>Nothing to connect</h1><p>Start from your AI app: add a connector with this install's address.</p>")
    try:
        user = current_user(request, s)
    except HTTPException as err:
        if err.status_code == 401:
            return _go("/signin?next=" + quote("/connect?req=" + req, safe=""))
        return _page("Connect an AI app", f'<h1>No access</h1><p class="error" role="alert">{html.escape(signin.MESSAGES["noaccess"])}</p>')
    asked = await connect.waiting(s, req)
    if asked is None:
        return _page("Connect an AI app", "<h1>That request has run out</h1><p>Go back to your AI app and connect again.</p>")
    e = html.escape
    return _page(f"Connect {asked['app']}", (
        f"<h1>Connect {e(asked['app'])}?</h1>"
        f"<p><strong>{e(asked['app'])}</strong> is asking to work in Inrface as <strong>{e(user['name'])}</strong> ({e(user['email'])}).</p>"
        "<ul><li>It will be able to send in ideas for you, and read and work on the ideas you can see.</li>"
        "<li>It can't change people or settings.</li>"
        "<li>A build it asks for waits for you to press Start build here.</li></ul>"
        f"<p class=\"hint\">Only allow this if you just asked your AI app to connect. You will go back to {e(asked['back_to'])}.</p>"
        '<p class="error" id="msg" role="alert" hidden></p>'
        '<div class="row"><button class="btn" id="allow" type="button">Allow</button><button class="quiet" id="deny" type="button">Don\'t allow</button></div>'),
        script=True, req=req)


@router.post("/api/connect")
async def connect_answer(body: AnswerIn, user: dict = Depends(current_user), s: Settings = Depends(builtin)):
    return {"go": await connect.answer(s, user, body.req, body.allow)}


def _cors(r: JSONResponse) -> JSONResponse:
    r.headers["Access-Control-Allow-Origin"] = "*"
    return r


@router.get("/.well-known/oauth-authorization-server")
def as_metadata(s: Settings = Depends(builtin)):
    return _cors(JSONResponse(connect.metadata(s)))


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def rs_metadata(s: Settings = Depends(builtin)):
    return _cors(JSONResponse(connect.resource_metadata(s)))


async def _small(request: Request) -> Request:
    """Read what was sent, stopping at the limit, and hand on a request that already holds it. A body sent in
    pieces says nothing up front about its size, so the header alone can't be the check."""
    try:
        said = int(request.headers.get("content-length") or 0)
    except ValueError:
        raise HTTPException(400, "Bad request")
    if said > MAX_BODY:
        raise HTTPException(413, "Too large")
    got = bytearray()
    async for piece in request.stream():
        got += piece
        if len(got) > MAX_BODY:
            raise HTTPException(413, "Too large")
    sent = False

    async def again() -> dict:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": bytes(got), "more_body": False}

    return Request(request.scope, again)


@router.api_route("/authorize", methods=["GET", "POST"])
async def authorize(request: Request, s: Settings = Depends(builtin)):
    return await AuthorizationHandler(connect.Provider(s)).handle(await _small(request))


@router.post("/token")
async def token(request: Request, s: Settings = Depends(builtin)):
    p = connect.Provider(s)
    return await TokenHandler(p, ClientAuthenticator(p)).handle(await _small(request))


@router.post("/register")
async def register(request: Request, s: Settings = Depends(builtin)):
    return await RegistrationHandler(connect.Provider(s), options=ClientRegistrationOptions(enabled=True)).handle(await _small(request))


@router.post("/revoke")
async def revoke(request: Request, s: Settings = Depends(builtin)):
    request = await _small(request)
    # Not the SDK's handler: that one turns away an app that has no secret, which is every app that registers here as public.
    try:
        client = await ClientAuthenticator(connect.Provider(s)).authenticate_request(request)
    except AuthenticationError:
        return JSONResponse({"error": "unauthorized_client"}, status_code=401)
    given = (await request.form()).get("token")
    if isinstance(given, str) and given:
        connect.hang_up(s, client.client_id, given)
    # The same answer whether or not the token was one of this app's, as the standard asks.
    return Response(status_code=200)
