"""Built-in sign-in (AUTH_MODE=builtin): people signing in through a provider, and AI apps connecting for them.

No test here talks to a real provider. `Fake` stands in for one in the round trips, and the checks on a provider's
signed statement are run against statements signed with a key made for the test."""
import base64
import hashlib
import json
import sqlite3
import time
from urllib.parse import parse_qs, quote, urlsplit

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from innerface import check, config, connect, signin

from test_app import AS_O, CONFIG, MCP_H, OUTSIDER, OWNER, PARTNER, client, make_client, pushes  # noqa: F401

URL = "https://ideas.example.com"
BUILTIN = dict(AUTH_MODE="builtin", PUBLIC_URL=URL, GOOGLE_CLIENT_ID="gid", GOOGLE_CLIENT_SECRET="gsecret")
STRANGER = "stranger@example.com"
WEB = {"X-Requested-With": "Inrface"}
CLAUDE = "https://claude.ai/api/mcp/auth_callback"
SESSION, PENDING = "__Host-inrface", "__Host-inrface-signin"
OAUTH_PATHS = (("GET", "/signin"), ("GET", "/signin/google"), ("GET", "/signin/google/back"), ("GET", "/connect"), ("GET", "/authorize"),
               ("POST", "/token"), ("POST", "/register"), ("POST", "/revoke"), ("GET", "/.well-known/oauth-authorization-server"),
               ("GET", "/.well-known/oauth-protected-resource"), ("GET", "/.well-known/oauth-protected-resource/mcp"), ("GET", "/api/account"))


class Fake:
    """A sign-in provider that says whatever the test tells it to."""
    id, label = "fake", "Fake"

    def __init__(self):
        self.email, self.error = OWNER, ""

    def start(self, redirect_uri, state, nonce, verifier):
        return f"https://fake.example/auth?state={state}&redirect_uri={quote(redirect_uri, safe='')}"

    def finish(self, redirect_uri, code, nonce, verifier):
        if self.error:
            raise signin.SignInError(self.error)
        return self.email


@pytest.fixture
def site(tmp_path, monkeypatch):
    fake = Fake()
    monkeypatch.setattr(signin, "providers", lambda s: {"fake": fake})
    with make_client(tmp_path, monkeypatch, **BUILTIN) as c:
        c.fake, c.data = fake, tmp_path
        yield c


def set_cookies(r) -> dict:
    """name -> (value, the rest of the Set-Cookie line)"""
    out = {}
    for line in r.headers.get_list("set-cookie"):
        pair, _, rest = line.partition(";")
        name, _, value = pair.partition("=")
        out[name] = (value, rest)
    return out


def start(c, next=""):
    r = c.get("/signin/fake" + (f"?next={quote(next, safe='')}" if next else ""), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("https://fake.example/auth?")
    return parse_qs(urlsplit(r.headers["location"]).query)["state"][0], set_cookies(r)[PENDING][0]


def back(c, state, pending, code="abc"):
    return c.get(f"/signin/fake/back?state={state}&code={code}", headers={"Cookie": f"{PENDING}={pending}"} if pending else {}, follow_redirects=False)


def sign_in(c, email=OWNER, agent="Mozilla/5.0 (iPhone) Safari/605") -> dict:
    """Go through the whole round trip and return the headers a signed-in browser sends."""
    c.fake.email, c.fake.error = email, ""
    state, pending = start(c)
    r = c.get(f"/signin/fake/back?state={state}&code=abc", headers={"Cookie": f"{PENDING}={pending}", "User-Agent": agent}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/", r.headers["location"]
    return {"Cookie": f"{SESSION}={set_cookies(r)[SESSION][0]}", **WEB}


def stored(c) -> str:
    """Everything the database holds, as one string, to look for secrets in."""
    db = sqlite3.connect(c.data / "innerface.db")
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return json.dumps([db.execute(f"SELECT * FROM {t}").fetchall() for t in tables], default=str)


# ---------- people signing in ----------

def test_signing_in_opens_the_app_for_someone_in_a_project(site):
    r = site.get("/api/me")
    assert r.status_code == 401 and r.headers["x-sign-in"] == "/signin"
    page = site.get("/signin")
    assert page.status_code == 200 and 'href="/signin/fake"' in page.text and "Continue with Fake" in page.text
    assert page.headers["cache-control"] == "no-store" and page.headers["strict-transport-security"]

    site.fake.email = OWNER
    state, pending = start(site)
    r = back(site, state, pending)
    assert r.status_code == 303 and r.headers["location"] == "/"
    token, flags = set_cookies(r)[SESSION]
    assert all(f in flags for f in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/")) and "Domain" not in flags
    assert set_cookies(r)[PENDING][0] == "" and "Max-Age=0" in set_cookies(r)[PENDING][1]

    me = site.get("/api/me", headers={"Cookie": f"{SESSION}={token}"}).json()
    assert (me["email"], me["name"], me["sign_in"]) == (OWNER, "Olly", "builtin")
    # Only a hash of the session is kept, so a copy of the database signs nobody in.
    assert token not in stored(site) and signin.hashed(token) in stored(site)
    # Someone already signed in doesn't see the sign-in page again.
    assert site.get("/signin", headers={"Cookie": f"{SESSION}={token}"}, follow_redirects=False).headers["location"] == "/"


def test_a_sign_in_reply_works_once_and_only_in_the_browser_that_started_it(site):
    state, pending = start(site)
    for wrong in ("", "someone-elses-cookie"):
        s2, _ = start(site)
        r = back(site, s2, wrong)
        assert r.headers["location"] == "/signin?e=failed" and SESSION not in set_cookies(r)
    assert back(site, state, pending).headers["location"] == "/"
    again = back(site, state, pending)
    assert again.headers["location"] == "/signin?e=failed" and SESSION not in set_cookies(again)
    assert back(site, "made-up-state", pending).headers["location"] == "/signin?e=failed"
    # No code, as when the person backs out at the provider.
    s3, p3 = start(site)
    assert back(site, s3, p3, code="").headers["location"] == "/signin?e=failed"
    assert site.get("/signin/nobody", follow_redirects=False).headers["location"] == "/signin?e=failed"


def test_a_sign_in_that_takes_too_long_has_to_start_again(site, monkeypatch):
    state, pending = start(site)
    monkeypatch.setattr(signin, "STATE_SECONDS", -1)
    r = back(site, state, pending)
    assert r.headers["location"] == "/signin?e=expired" and SESSION not in set_cookies(r)
    assert "took too long" in site.get("/signin?e=expired").text


def test_someone_who_is_in_no_project_is_turned_away_after_signing_in(site, caplog):
    site.fake.email = STRANGER
    state, pending = start(site)
    r = back(site, state, pending)
    assert r.headers["location"] == "/signin?e=noaccess" and SESSION not in set_cookies(r)
    assert "in any project here" in site.get("/signin?e=noaccess").text
    assert f"login refused: {STRANGER} signed in with fake" in caplog.text
    site.fake.error = "unverified"
    state, pending = start(site)
    assert back(site, state, pending).headers["location"] == "/signin?e=unverified"
    # A made-up reason shows nothing.
    assert "<script>" not in site.get("/signin?e=<script>").text


def test_after_signing_in_people_only_go_on_to_the_app_or_a_waiting_ai_app(site):
    for next in ("https://evil.example/", "//evil.example", "/api/me", "/connect?req=short"):
        state, pending = start(site, next)
        assert back(site, state, pending).headers["location"] == "/"
    ok = "/connect?req=" + "a" * 43
    state, pending = start(site, ok)
    assert back(site, state, pending).headers["location"] == ok
    assert f'href="/signin/fake?next={quote(ok, safe="")}"' in site.get("/signin?next=" + quote(ok, safe="")).text
    assert "next=" not in site.get("/signin?next=https://evil.example/").text


def test_signing_out_and_seeing_where_you_are_signed_in(site):
    phone, laptop = sign_in(site), sign_in(site, agent="Mozilla/5.0 (Macintosh) Chrome/130")
    pat = sign_in(site, PARTNER)
    a = site.get("/api/account", headers=phone).json()
    assert sorted((b["device"], b["this_one"]) for b in a["browsers"]) == [("Chrome on Mac", False), ("Safari on iPhone", True)]
    assert a["apps"] == [] and a["connector_url"] == URL + "/mcp"
    other = next(b["id"] for b in a["browsers"] if not b["this_one"])
    # Someone else can't end it, and nor can a request without the app's own header.
    assert site.delete(f"/api/account/browsers/{other}", headers=pat).status_code == 404
    assert site.delete(f"/api/account/browsers/{other}", headers={"Cookie": phone["Cookie"]}).status_code == 403
    left = site.delete(f"/api/account/browsers/{other}", headers=phone).json()
    assert [b["this_one"] for b in left["browsers"]] == [True]
    assert site.get("/api/me", headers=laptop).status_code == 401

    r = site.post("/api/signout", headers=phone)
    assert r.status_code == 200 and set_cookies(r)[SESSION][0] == "" and "Max-Age=0" in set_cookies(r)[SESSION][1]
    assert site.get("/api/me", headers=phone).status_code == 401
    assert site.get("/api/me", headers=pat).status_code == 200


def test_a_session_runs_out(site):
    olly = sign_in(site)
    db = sqlite3.connect(site.data / "innerface.db")
    db.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00+00:00'")
    db.commit()
    assert site.get("/api/me", headers=olly).status_code == 401


def test_a_request_from_another_site_cannot_use_the_session(site):
    olly = sign_in(site)
    body = {"email": "new@example.com", "role": "giver", "name": "New"}
    assert site.put("/api/projects/main/people", json=body, headers={**olly, "Origin": "https://evil.example"}).status_code == 403
    assert site.put("/api/projects/main/people", json=body, headers={**olly, "Origin": "null"}).status_code == 403
    assert site.put("/api/projects/main/people", json=body, headers={"Cookie": olly["Cookie"]}).status_code == 403
    assert site.put("/api/projects/main/people", json=body, headers={**olly, "Origin": URL}).status_code == 200
    # The header that says who you are in try-it mode means nothing here.
    assert site.get("/api/me", headers={"X-Dev-User": OWNER}).status_code == 401


def test_someone_taken_out_of_their_last_project_is_signed_out_everywhere(site):
    olly, pat = sign_in(site), sign_in(site, PARTNER)
    app = connect_app(site, pat)
    assert mcp(site, app["access_token"]).status_code == 200
    assert site.put("/api/projects/main/people", json={"email": PARTNER, "role": "none"}, headers=olly).status_code == 200
    assert site.get("/api/me", headers=pat).status_code == 401
    assert mcp(site, app["access_token"]).status_code == 401
    assert token(site, app["client_id"], grant_type="refresh_token", refresh_token=app["refresh_token"]).status_code == 400
    # Added back, they start clean: the old browser and the old AI app stay signed out.
    site.put("/api/projects/main/people", json={"email": PARTNER, "role": "giver"}, headers=olly)
    assert site.get("/api/me", headers=pat).status_code == 401
    assert mcp(site, app["access_token"]).status_code == 401


# ---------- what a provider's signed statement has to say ----------

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def statement(key=KEY, **changes):
    now = int(time.time())
    claims = {"iss": "https://accounts.google.com", "aud": "gid", "sub": "123", "email": "Owner@Example.com", "email_verified": True,
              "nonce": "the-nonce", "iat": now, "exp": now + 300, **changes}
    return pyjwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")


@pytest.fixture
def google(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch, **BUILTIN):
        p = signin.providers(config.get_settings())["google"]
        monkeypatch.setattr(signin.OIDCProvider, "_key", lambda self, token: KEY.public_key())
        sent = {}

        class Reply:
            status_code = 200

            def __init__(self, token):
                self.token = token

            def json(self):
                return {"id_token": self.token}

        def answer(token, status=200):
            def post(url, **kw):
                sent.update(url=url, **kw["data"])
                r = Reply(token)
                r.status_code = status
                return r
            monkeypatch.setattr(signin.httpx, "post", post)
            return p.finish(URL + "/signin/google/back", "the-code", "the-nonce", "the-verifier")
        yield p, answer, sent


def test_google_is_asked_the_right_way(google):
    p, answer, sent = google
    q = parse_qs(urlsplit(p.start(URL + "/signin/google/back", "st", "no", "ver")).query)
    assert q["redirect_uri"] == [URL + "/signin/google/back"] and q["client_id"] == ["gid"] and q["scope"] == ["openid email"]
    assert q["state"] == ["st"] and q["nonce"] == ["no"] and q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"] == [base64.urlsafe_b64encode(hashlib.sha256(b"ver").digest()).decode().rstrip("=")]
    assert "gsecret" not in p.start(URL + "/signin/google/back", "st", "no", "ver")
    assert answer(statement()) == OWNER
    assert sent["url"] == "https://oauth2.googleapis.com/token" and sent["code"] == "the-code" and sent["code_verifier"] == "the-verifier"
    assert sent["redirect_uri"] == URL + "/signin/google/back"


@pytest.mark.parametrize("changes, code", [
    ({"aud": "another-app"}, "failed"), ({"iss": "https://evil.example"}, "failed"), ({"nonce": "another-sign-in"}, "failed"),
    ({"nonce": None}, "failed"), ({"exp": int(time.time()) - 10}, "failed"), ({"email": None}, "failed"), ({"email": " "}, "failed"),
    ({"sub": None}, "failed"), ({"email_verified": False}, "unverified"), ({"email_verified": "true"}, "unverified"), ({"email_verified": None}, "unverified"),
])
def test_a_statement_that_is_off_in_any_way_signs_nobody_in(google, changes, code):
    _, answer, _ = google
    with pytest.raises(signin.SignInError) as e:
        answer(statement(**changes))
    assert e.value.code == code


def test_a_statement_nobody_signed_or_someone_else_signed_signs_nobody_in(google):
    _, answer, _ = google
    unsigned = pyjwt.encode({"iss": "https://accounts.google.com", "aud": "gid", "sub": "1", "email": OWNER, "email_verified": True, "nonce": "the-nonce",
                             "iat": int(time.time()), "exp": int(time.time()) + 300}, None, algorithm="none")
    for bad in (statement(key=OTHER_KEY), unsigned, "not-a-token"):
        with pytest.raises(signin.SignInError) as e:
            answer(bad)
        assert e.value.code == "failed"
    with pytest.raises(signin.SignInError):
        answer(statement(), status=400)
    with pytest.raises(signin.SignInError):
        answer(None)


def test_without_a_google_client_there_is_no_way_to_sign_in(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch, **{**BUILTIN, "GOOGLE_CLIENT_ID": ""}) as c:
        assert signin.providers(config.get_settings()) == {}
        assert "isn't set up yet" in c.get("/signin").text


# ---------- AI apps connecting ----------

def register(c, redirect=CLAUDE, name="Claude"):
    return c.post("/register", json={"redirect_uris": [redirect], "client_name": name, "token_endpoint_auth_method": "none",
                                     "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})


def pkce():
    verifier = "v" * 50
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def ask(c, client_id, redirect=CLAUDE, resource=URL + "/mcp", state="xyz"):
    """The AI app sends the person's browser to /authorize. Returns the id of the request that is now waiting."""
    q = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "code_challenge": pkce()[1], "code_challenge_method": "S256",
         "state": state, "resource": resource}
    r = c.get("/authorize", params=q, follow_redirects=False)
    assert r.status_code == 302, r.text
    return r


def token(c, client_id, **form):
    return c.post("/token", data={"client_id": client_id, **form})


def connect_app(c, who, name="Claude") -> dict:
    """Register, ask, press Allow, and swap the code for tokens."""
    client_id = register(c, name=name).json()["client_id"]
    req = parse_qs(urlsplit(ask(c, client_id).headers["location"]).query)["req"][0]
    go = c.post("/api/connect", json={"req": req, "allow": True}, headers=who).json()["go"]
    code = parse_qs(urlsplit(go).query)["code"][0]
    r = token(c, client_id, grant_type="authorization_code", code=code, redirect_uri=CLAUDE, code_verifier=pkce()[0])
    assert r.status_code == 200, r.text
    return {"client_id": client_id, "code": code, **r.json()}


def mcp(c, bearer=None, **headers):
    h = {**MCP_H, **headers}
    if bearer:
        h["Authorization"] = f"Bearer {bearer}"
    return c.post("/mcp", headers=h, content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}))


def test_an_ai_app_is_told_where_to_connect(site):
    r = mcp(site)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == f'Bearer resource_metadata="{URL}/.well-known/oauth-protected-resource/mcp"'
    for path in ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource"):
        rs = site.get(path).json()
        assert rs["resource"] == URL + "/mcp" and rs["authorization_servers"] == [URL]
    m = site.get("/.well-known/oauth-authorization-server").json()
    assert m["issuer"] == URL and m["authorization_endpoint"] == URL + "/authorize" and m["token_endpoint"] == URL + "/token"
    assert m["registration_endpoint"] == URL + "/register" and m["code_challenge_methods_supported"] == ["S256"]
    assert "none" in m["token_endpoint_auth_methods_supported"] and "scopes_supported" not in m and "scopes_supported" not in rs


def test_an_ai_app_connects_once_the_person_presses_allow(site):
    olly = sign_in(site)
    client_id = register(site).json()["client_id"]
    r = ask(site, client_id)
    assert r.headers["location"].startswith(URL + "/connect?req=")
    path = r.headers["location"][len(URL):]
    req = parse_qs(urlsplit(path).query)["req"][0]

    # Not signed in: off to sign in, and back to this page afterwards.
    away = site.get(path, follow_redirects=False)
    assert away.status_code == 303 and away.headers["location"] == "/signin?next=" + quote(path, safe="")
    page = site.get(path, headers=olly)
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store" and "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert "Connect Claude?" in page.text and "Olly" in page.text and OWNER in page.text and "claude.ai" in page.text and f'data-req="{req}"' in page.text
    # Looking at the page decides nothing.
    assert mcp(site, "anything").status_code == 401

    # The answer needs the signed-in browser and the app's own header. An AI app's token is not enough.
    assert site.post("/api/connect", json={"req": req, "allow": True}).status_code == 403
    assert site.post("/api/connect", json={"req": req, "allow": True}, headers=WEB).status_code == 401
    go = site.post("/api/connect", json={"req": req, "allow": True}, headers=olly).json()["go"]
    assert go.startswith(CLAUDE + "?") and parse_qs(urlsplit(go).query)["state"] == ["xyz"]
    code = parse_qs(urlsplit(go).query)["code"][0]
    assert site.post("/api/connect", json={"req": req, "allow": True}, headers=olly).status_code == 410
    assert "run out" in site.get(path, headers=olly).text

    # The code needs the proof the app sent when it asked, and works once.
    assert token(site, client_id, grant_type="authorization_code", code=code, redirect_uri=CLAUDE, code_verifier="w" * 50).status_code == 400
    assert token(site, client_id, grant_type="authorization_code", code=code, redirect_uri="https://claude.com/api/mcp/auth_callback", code_verifier=pkce()[0]).status_code == 400
    got = token(site, client_id, grant_type="authorization_code", code=code, redirect_uri=CLAUDE, code_verifier=pkce()[0])
    assert got.status_code == 200 and got.json()["token_type"].lower() == "bearer" and got.json()["expires_in"] == connect.ACCESS_SECONDS
    access = got.json()["access_token"]

    listed = mcp(site, access)
    assert listed.status_code == 200 and "submit_idea" in [t["name"] for t in listed.json()["result"]["tools"]]
    kept = stored(site)
    assert all(secret not in kept for secret in (access, got.json()["refresh_token"], code, req))
    apps = site.get("/api/account", headers=olly).json()["apps"]
    assert [a["app"] for a in apps] == ["Claude"]


def test_the_web_app_and_an_ai_app_hold_different_proof(site):
    olly = sign_in(site)
    app = connect_app(site, olly)
    bearer = {"Authorization": f"Bearer {app['access_token']}", **WEB}
    # The AI app's token opens /mcp and nothing else: not the web app's screens, people, share links or its own consent.
    assert site.get("/api/me", headers=bearer).status_code == 401
    assert site.get("/api/projects/main/people", headers=bearer).status_code == 401
    assert site.get("/api/account", headers=bearer).status_code == 401
    req = parse_qs(urlsplit(ask(site, app["client_id"]).headers["location"]).query)["req"][0]
    assert site.post("/api/connect", json={"req": req, "allow": True}, headers=bearer).status_code == 401
    # And the browser's cookie doesn't open /mcp, so a web page can't drive the tools through it.
    assert mcp(site, Cookie=olly["Cookie"]).status_code == 401
    assert mcp(site, app["refresh_token"]).status_code == 401
    assert mcp(site, app["access_token"]).status_code == 200


def test_saying_no_sends_the_ai_app_away_with_nothing(site):
    olly = sign_in(site)
    client_id = register(site).json()["client_id"]
    req = parse_qs(urlsplit(ask(site, client_id).headers["location"]).query)["req"][0]
    go = site.post("/api/connect", json={"req": req, "allow": False}, headers=olly).json()["go"]
    q = parse_qs(urlsplit(go).query)
    assert go.startswith(CLAUDE + "?") and q["error"] == ["access_denied"] and "code" not in q
    assert site.post("/api/connect", json={"req": req, "allow": True}, headers=olly).status_code == 410
    assert site.get("/api/account", headers=olly).json()["apps"] == []
    assert "Nothing to connect" in site.get("/connect", headers=olly).text and "Nothing to connect" in site.get("/connect?req=<b>", headers=olly).text


def test_only_ai_apps_on_the_list_can_register(site):
    for bad in ("https://evil.example/callback", "https://claude.ai.evil.example/api/mcp/auth_callback", "http://claude.ai/api/mcp/auth_callback",
                "https://chatgpt.com/connector/oauth/../../anything", "https://chatgpt.com/connector/oauth/%2e%2e/x", "https://claude.ai/api/mcp/auth_callback/extra"):
        r = register(site, redirect=bad)
        assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri", bad
    assert register(site, redirect="https://chatgpt.com/connector/oauth/abc123").status_code == 201
    client_id = register(site).json()["client_id"]
    # A registered app can't then ask to be sent somewhere else, or for a token meant for another address.
    q = {"response_type": "code", "client_id": client_id, "redirect_uri": "https://evil.example/callback", "code_challenge": pkce()[1], "code_challenge_method": "S256"}
    r = site.get("/authorize", params=q, follow_redirects=False)
    assert r.status_code == 400 and "location" not in r.headers
    r = ask(site, client_id, resource="https://other.example/mcp")
    assert r.headers["location"].startswith(CLAUDE) and "invalid_target" in r.headers["location"]
    assert site.get("/authorize", params={**q, "client_id": "nobody"}, follow_redirects=False).status_code == 400
    assert site.post("/register", content=b"x" * 70000, headers={"Content-Type": "application/json"}).status_code == 413


def test_what_an_ai_app_calls_itself_is_shown_as_plain_text(site):
    olly = sign_in(site)
    client_id = register(site, name='<img src=x onerror=alert(1)>"Claude"' + "x" * 200).json()["client_id"]
    path = ask(site, client_id).headers["location"][len(URL):]
    page = site.get(path, headers=olly).text
    assert "<img src=x" not in page and "&lt;img src=x" in page and "x" * 61 not in page


def test_renewing_swaps_the_token_and_an_old_one_ends_the_connection(site):
    olly = sign_in(site)
    app = connect_app(site, olly)
    r = token(site, app["client_id"], grant_type="refresh_token", refresh_token=app["refresh_token"])
    assert r.status_code == 200
    new = r.json()
    assert new["access_token"] != app["access_token"] and new["refresh_token"] != app["refresh_token"]
    assert mcp(site, app["access_token"]).status_code == 401 and mcp(site, new["access_token"]).status_code == 200
    # Another app can't use it.
    other = register(site, name="Other").json()["client_id"]
    assert token(site, other, grant_type="refresh_token", refresh_token=new["refresh_token"]).status_code == 400
    assert mcp(site, new["access_token"]).status_code == 200
    # The old one turning up again means a copy got out, so the whole connection ends.
    assert token(site, app["client_id"], grant_type="refresh_token", refresh_token=app["refresh_token"]).status_code == 400
    assert mcp(site, new["access_token"]).status_code == 401
    assert token(site, app["client_id"], grant_type="refresh_token", refresh_token=new["refresh_token"]).status_code == 400
    assert site.get("/api/account", headers=olly).json()["apps"] == []


def test_a_token_runs_out_and_a_person_can_disconnect_an_app(site):
    olly, pat = sign_in(site), sign_in(site, PARTNER)
    first, second = connect_app(site, olly), connect_app(site, olly, name="ChatGPT")
    apps = site.get("/api/account", headers=olly).json()["apps"]
    assert sorted(a["app"] for a in apps) == ["ChatGPT", "Claude"]
    gone = next(a["id"] for a in apps if a["app"] == "Claude")
    assert site.delete(f"/api/account/apps/{gone}", headers=pat).status_code == 404
    left = site.delete(f"/api/account/apps/{gone}", headers=olly).json()
    assert [a["app"] for a in left["apps"]] == ["ChatGPT"]
    assert mcp(site, first["access_token"]).status_code == 401 and mcp(site, second["access_token"]).status_code == 200
    assert token(site, first["client_id"], grant_type="refresh_token", refresh_token=first["refresh_token"]).status_code == 400
    # The app itself can hang up too.
    other = register(site, name="Other").json()["client_id"]
    assert site.post("/revoke", data={"client_id": other, "token": second["refresh_token"]}).status_code == 200
    assert site.post("/revoke", data={"client_id": "nobody", "token": second["refresh_token"]}).status_code == 401
    assert mcp(site, second["access_token"]).status_code == 200
    assert site.post("/revoke", data={"client_id": second["client_id"], "token": second["refresh_token"]}).status_code == 200
    assert mcp(site, second["access_token"]).status_code == 401

    third = connect_app(site, olly)
    db = sqlite3.connect(site.data / "innerface.db")
    db.execute("UPDATE oauth_tokens SET expires_at=1 WHERE kind='access'")
    db.commit()
    assert mcp(site, third["access_token"]).status_code == 401
    assert token(site, third["client_id"], grant_type="refresh_token", refresh_token=third["refresh_token"]).status_code == 200


def call(c, bearer, name, **args):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    res = c.post("/mcp", headers={**MCP_H, "Authorization": f"Bearer {bearer}", "X-Dev-User": OWNER}, content=body).json()["result"]
    return {"error": res["content"][0]["text"]} if res.get("isError") else res.get("structuredContent") or json.loads(res["content"][0]["text"])


def test_an_ai_app_acts_as_the_person_who_allowed_it(site):
    olly, pat = sign_in(site), sign_in(site, PARTNER)
    app = connect_app(site, pat)
    # Pat is a giver, and so is Pat's app, whatever other header it sends.
    said = call(site, app["access_token"], "list_projects")
    assert said["you_are_helping"] == "Pat" and [(p["id"], p["role"]) for p in said["projects"]] == [("main", "giver")]
    call(site, app["access_token"], "submit_idea", text="A dark theme for the app, my eyes hurt at night", title="Dark theme")
    cards = site.get("/api/cards", headers=olly).json()["cards"]
    assert [c["author_name"] for c in cards] == ["Pat"]
    assert "error" in call(site, app["access_token"], "file_card", card_id=cards[0]["id"], where="done")
    assert site.get("/api/cards", headers=olly).json()["cards"][0]["status"] == cards[0]["status"]


# ---------- installs that don't use it ----------

def test_none_of_it_exists_unless_the_install_turns_it_on(client, tmp_path, monkeypatch):
    for method, path in OAUTH_PATHS:
        assert client.request(method, path, headers=AS_O).status_code == 404, path
    assert client.post("/api/signout", headers=AS_O).status_code == 404
    assert client.post("/api/connect", json={"req": "a" * 43, "allow": True}, headers=AS_O).status_code == 404
    assert client.get("/api/me", headers=AS_O).json()["sign_in"] == "dev"
    assert "strict-transport-security" not in client.get("/api/me", headers=AS_O).headers


def test_behind_cloudflare_access_a_session_or_an_app_token_gets_nobody_in(tmp_path, monkeypatch):
    fake = Fake()
    monkeypatch.setattr(signin, "providers", lambda s: {"fake": fake})
    with make_client(tmp_path, monkeypatch, **BUILTIN) as c:
        c.fake = fake
        olly = sign_in(c)
        app = connect_app(c, olly)
    # The same data folder, now behind Cloudflare Access: what was handed out before opens nothing.
    with make_client(tmp_path, monkeypatch, AUTH_MODE="access", CF_ACCESS_TEAM="team", CF_ACCESS_AUD="the-aud", PUBLIC_URL=URL) as c:
        for method, path in OAUTH_PATHS:
            assert c.request(method, path, headers=olly).status_code in (401, 404), path
            assert c.request(method, path).status_code == 404 or path == "/api/account", path
        assert c.get("/api/me", headers=olly).status_code == 401
        assert c.get("/api/me", headers={"Cookie": olly["Cookie"].replace("__Host-", "")}).status_code == 401
        r = mcp(c, app["access_token"])
        assert r.status_code == 401 and "www-authenticate" not in r.headers
        assert "x-sign-in" not in c.get("/api/me").headers


@pytest.mark.parametrize("env, said", [
    ({"PUBLIC_URL": ""}, "PUBLIC_URL is required"),
    ({"PUBLIC_URL": "http://ideas.example.com"}, "must start with https://"),
    ({"CF_ACCESS_AUD": "abc"}, "Pick one way to sign in"),
    ({"SESSION_DAYS": "0"}, "SESSION_DAYS"),
    ({"CONNECTOR_REDIRECTS": "http://claude.ai/x"}, "https addresses"),
    ({"CONNECTOR_REDIRECTS": "https://claude.ai*"}, "can only end a path"),
    ({"CONNECTOR_REDIRECTS": "https://*.example.com/x"}, "can only end a path"),
])
def test_settings_that_would_make_sign_in_unsafe_are_refused(tmp_path, monkeypatch, env, said):
    make_client(tmp_path, monkeypatch, **{**BUILTIN, **env})
    with pytest.raises(config.ConfigError, match=said):
        config.get_settings()


def test_it_can_run_on_this_machine_without_https(tmp_path, monkeypatch):
    fake = Fake()
    monkeypatch.setattr(signin, "providers", lambda s: {"fake": fake})
    with make_client(tmp_path, monkeypatch, **{**BUILTIN, "PUBLIC_URL": "http://localhost:8080"}) as c:
        r = c.get("/signin/fake", follow_redirects=False)
        name, (value, flags) = next(iter(set_cookies(r).items()))
        assert name == "inrface-signin" and "Secure" not in flags and "HttpOnly" in flags
        assert "strict-transport-security" not in r.headers


def test_the_setup_check_covers_built_in_sign_in(tmp_path, monkeypatch):
    make_client(tmp_path, monkeypatch, **BUILTIN)
    lines = check.run(offline=True)
    text = "\n".join(f"{x.level} {x.text} {x.todo}" for x in lines)
    assert "NOTE AUTH_MODE is builtin" in text and "has not had an outside security review" in text
    assert f"OK People can sign in with Google. It has to allow the return address {URL}/signin/google/back" in text
    assert f"AI apps connect at {URL}/mcp and can be sent back to chatgpt.com, claude.ai, claude.com." in text
    assert "gsecret" not in text
    make_client(tmp_path, monkeypatch, **{**BUILTIN, "GOOGLE_CLIENT_SECRET": ""})
    assert any(x.level == check.FIX and "no way to sign in" in x.text for x in check.run(offline=True))
