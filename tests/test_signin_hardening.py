"""What a pass over built-in sign-in for weak spots turned up, each kept from coming back.

The same rules as test_signin.py: nothing here talks to a real provider or a real AI app."""
import dataclasses
import json
import logging
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from innerface import config, connect, signin
from innerface.main import app

from test_app import CONFIG, OWNER, PARTNER, make_client  # noqa: F401
from test_signin import (BUILTIN, CLAUDE, PENDING, SESSION, URL, WEB, Fake, ask, back, connect_app, google, mcp, pkce, register,  # noqa: F401
                         set_cookies, sign_in, site, start, statement, stored, token)

SUB = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "p256dh": "k" * 20, "auth": "a" * 10}
LONG_AGO = "2020-01-01T00:00:00.000Z"


def rows(c, sql, *a):
    db = sqlite3.connect(c.data / "innerface.db")
    try:
        got = db.execute(sql, a).fetchall()
        db.commit()
        return got
    finally:
        db.close()


def count(c, table, where="1=1", *a):
    return rows(c, f"SELECT COUNT(*) FROM {table} WHERE {where}", *a)[0][0]


def req_of(r):
    return parse_qs(urlsplit(r.headers["location"]).query)["req"][0]


def allow(c, client_id, who):
    """Ask and press Allow. Returns the one-use code."""
    go = c.post("/api/connect", json={"req": req_of(ask(c, client_id)), "allow": True}, headers=who).json()["go"]
    return parse_qs(urlsplit(go).query)["code"][0]


def swap(c, client_id, code):
    return token(c, client_id, grant_type="authorization_code", code=code, redirect_uri=CLAUDE, code_verifier=pkce()[0])


def without(email):
    """Take someone out of every project the way an edit to the settings file would, behind the app's back.
    Returns a function that puts things back."""
    s = config.get_settings()
    people, projects = s.people, s.projects
    s.use(people, {k: dataclasses.replace(p, givers=tuple(e for e in p.givers if e != email)) for k, p in projects.items()})
    assert not s.projects_for(email)
    return lambda: s.use(people, projects)


@pytest.fixture
def real(site):
    """Answers the way the web server would: an error inside the app comes back as a 500, not as a test failure."""
    return TestClient(app, raise_server_exceptions=False)


# ---------- AI apps connecting ----------

def test_a_body_sent_in_pieces_is_held_to_the_same_limit(site):
    big = json.dumps({"redirect_uris": [CLAUDE], "client_name": "A" * (5 * 1024 * 1024), "token_endpoint_auth_method": "none"}).encode()
    pieces = lambda body: (body[i:i + 65536] for i in range(0, len(body), 65536))  # noqa: E731
    # Sent in pieces there is no Content-Length to look at, so the size is counted as it arrives.
    r = site.post("/register", content=pieces(big), headers={"Content-Type": "application/json"})
    assert "content-length" not in r.request.headers and r.status_code == 413 and count(site, "oauth_clients") == 0
    cid = register(site).json()["client_id"]
    form = f"response_type=code&client_id={cid}&code_challenge={pkce()[1]}&state={'S' * 900_000}".encode()
    r = site.post("/authorize", content=pieces(form), headers={"Content-Type": "application/x-www-form-urlencoded"}, follow_redirects=False)
    assert r.status_code == 413 and count(site, "oauth_requests") == 0
    assert site.post("/token", content=pieces(b"x" * 70000), headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 413
    assert site.post("/revoke", content=pieces(b"x" * 70000), headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 413
    assert site.post("/register", content=b"{}", headers={"Content-Type": "application/json", "Content-Length": "lots"}).status_code in (400, 422)
    # A small body sent in pieces still works, at every address that reads one.
    small = json.dumps({"redirect_uris": [CLAUDE], "client_name": "Claude", "token_endpoint_auth_method": "none"}).encode()
    r = site.post("/register", content=pieces(small), headers={"Content-Type": "application/json"})
    assert r.status_code == 201 and "content-length" not in r.request.headers
    cid, olly = r.json()["client_id"], sign_in(site)
    form = f"response_type=code&client_id={cid}&redirect_uri={CLAUDE}&code_challenge={pkce()[1]}&code_challenge_method=S256&state=s".encode()
    r = site.post("/authorize", content=pieces(form), headers={"Content-Type": "application/x-www-form-urlencoded"}, follow_redirects=False)
    code = parse_qs(urlsplit(site.post("/api/connect", json={"req": req_of(r), "allow": True}, headers=olly).json()["go"]).query)["code"][0]
    form = f"grant_type=authorization_code&client_id={cid}&code={code}&redirect_uri={CLAUDE}&code_verifier={pkce()[0]}".encode()
    got = site.post("/token", content=pieces(form), headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert got.status_code == 200 and mcp(site, got.json()["access_token"]).status_code == 200
    form = f"client_id={cid}&token={got.json()['access_token']}".encode()
    assert site.post("/revoke", content=pieces(form), headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 200
    assert mcp(site, got.json()["access_token"]).status_code == 401


def test_renewing_for_someone_who_has_left_ends_the_connection_for_good(site, real, caplog):
    pat = sign_in(site, PARTNER)
    conn = connect_app(site, pat)
    back_in = without(PARTNER)
    r = token(real, conn["client_id"], grant_type="refresh_token", refresh_token=conn["refresh_token"])
    # The app's own refusal, not an error inside it. And what the refusal ended stays ended.
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant" and "no longer has access" in r.json()["error_description"]
    assert count(site, "oauth_grants") == 0 and count(site, "oauth_tokens") == 0
    assert "was ended: that person no longer has access" in caplog.text
    back_in()
    assert token(real, conn["client_id"], grant_type="refresh_token", refresh_token=conn["refresh_token"]).status_code == 400
    assert mcp(real, conn["access_token"]).status_code == 401


def test_a_code_for_someone_who_has_left_is_spent(site, real):
    pat = sign_in(site, PARTNER)
    cid = register(site).json()["client_id"]
    code = allow(site, cid, pat)
    back_in = without(PARTNER)
    r = swap(real, cid, code)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant" and count(site, "oauth_codes") == 0
    back_in()
    assert swap(real, cid, code).status_code == 400 and count(site, "oauth_grants") == 0


def test_a_full_house_is_said_plainly(site, real, monkeypatch):
    cid = register(site).json()["client_id"]
    monkeypatch.setattr(connect, "MAX_CLIENTS", 1)
    r = register(real, name="One more")
    assert r.status_code == 400 and "Too many apps have registered" in r.json()["error_description"] and count(site, "oauth_clients") == 1
    monkeypatch.setattr(connect, "MAX_PENDING", 0)
    r = ask(real, cid)
    assert r.headers["location"].startswith(CLAUDE + "?") and "error=temporarily_unavailable" in r.headers["location"]
    # A request that has run out is a plain "gone", whichever way it is answered.
    olly = sign_in(site)
    assert real.post("/api/connect", json={"req": "r" * 43, "allow": True}, headers=olly).status_code == 410
    assert real.post("/api/connect", json={"req": "r" * 43, "allow": False}, headers=olly).status_code == 410


def test_one_app_cannot_hold_every_place_in_the_queue(site, monkeypatch):
    monkeypatch.setattr(connect, "MAX_PENDING", 5)
    monkeypatch.setattr(connect, "MAX_PENDING_EACH", 3)
    olly = sign_in(site)
    greedy, other = register(site, name="Greedy").json()["client_id"], register(site).json()["client_id"]
    asked = [req_of(ask(site, greedy)) for _ in range(8)]
    assert count(site, "oauth_requests") == 3
    # Its oldest requests gave way; its newest still work, and so does everyone else.
    assert "run out" in site.get("/connect?req=" + asked[0], headers=olly).text
    assert "Connect Greedy?" in site.get("/connect?req=" + asked[-1], headers=olly).text
    assert "Connect Claude?" in site.get("/connect?req=" + req_of(ask(site, other)), headers=olly).text


def test_using_a_code_twice_ends_the_connection_it_made(site, caplog):
    olly = sign_in(site)
    conn = connect_app(site, olly)
    assert mcp(site, conn["access_token"]).status_code == 200
    # A code turning up again means a copy of it got out, so what it made is ended.
    assert swap(site, conn["client_id"], conn["code"]).status_code == 400
    assert mcp(site, conn["access_token"]).status_code == 401
    assert token(site, conn["client_id"], grant_type="refresh_token", refresh_token=conn["refresh_token"]).status_code == 400
    assert site.get("/api/account", headers=olly).json()["apps"] == [] and "used a second time" in caplog.text
    # A code that never was ends nothing, and neither does another app trying this one's code.
    other = connect_app(site, olly, name="Other")
    assert swap(site, other["client_id"], "c" * 43).status_code == 400 and mcp(site, other["access_token"]).status_code == 200
    stranger = register(site, name="Stranger").json()["client_id"]
    code = allow(site, other["client_id"], olly)
    assert swap(site, stranger, code).status_code == 400
    assert swap(site, other["client_id"], code).status_code == 200 and mcp(site, other["access_token"]).status_code == 200


def test_clearing_out_old_registrations_leaves_the_ones_in_use(site):
    olly = sign_in(site)
    conn = connect_app(site, olly)
    gid = site.get("/api/account", headers=olly).json()["apps"][0]["id"]
    assert site.delete(f"/api/account/apps/{gid}", headers=olly).status_code == 200
    midway = register(site, name="Midway").json()["client_id"]
    asked = req_of(ask(site, midway))
    unused = register(site, name="Unused").json()["client_id"]
    rows(site, "UPDATE oauth_clients SET created_at=?", LONG_AGO)
    assert register(site, name="Newcomer").status_code == 201   # anyone registering clears out what is stale

    def known(client_id):
        return site.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": CLAUDE, "code_challenge": pkce()[1],
                                              "code_challenge_method": "S256"}, follow_redirects=False).status_code == 302
    # An app that connected someone can connect again after being disconnected. One that is part-way through carries on.
    assert known(conn["client_id"]) and not known(unused)
    code = parse_qs(urlsplit(site.post("/api/connect", json={"req": asked, "allow": True}, headers=olly).json()["go"]).query)["code"][0]
    assert register(site, name="Another").status_code == 201
    assert swap(site, midway, code).status_code == 200
    # Renewing counts as use. An app nobody has been connected through for months goes in the end.
    rows(site, "UPDATE oauth_clients SET used_at=? WHERE client_id=?", LONG_AGO, midway)
    got = connect_app(site, olly, name="Renews")
    rows(site, "UPDATE oauth_clients SET used_at=? WHERE client_id=?", LONG_AGO, got["client_id"])
    assert token(site, got["client_id"], grant_type="refresh_token", refresh_token=got["refresh_token"]).status_code == 200
    rows(site, "UPDATE oauth_clients SET used_at=? WHERE client_id=?", LONG_AGO, conn["client_id"])
    rows(site, "DELETE FROM oauth_requests")
    assert register(site, name="Last").status_code == 201
    assert not known(conn["client_id"]) and known(midway) and known(got["client_id"])


def test_a_connection_whose_tokens_have_all_run_out_is_cleared(site):
    olly = sign_in(site)
    connect_app(site, olly)
    kept = connect_app(site, olly, name="Kept")
    rows(site, "UPDATE oauth_tokens SET expires_at=? WHERE grant_id NOT IN (SELECT id FROM oauth_grants WHERE client_id=?)", time.time() - 5, kept["client_id"])
    assert register(site, name="Newcomer").status_code == 201
    assert [a["app"] for a in site.get("/api/account", headers=olly).json()["apps"]] == ["Kept"] and mcp(site, kept["access_token"]).status_code == 200


@pytest.mark.parametrize("uri", [
    "https://chatgpt.com/connector/oauth/x?next=https://evil.example", "https://chatgpt.com/connector/oauth/x?a=1&code=FIXED",
    "https://chatgpt.com/connector/oauth/x;y=z", "https://chatgpt.com/connector/oauth//evil.example/x", "https://chatgpt.com/connector/oauth/",
    "https://chatgpt.com/connector/oauth/x/", "https://chatgpt.com/connector/oauth/%252e%252e/x", "https://chatgpt.com/connector/oauth/x y",
    "https://chatgpt.com/connector/oauth/\u0445", "https://claude.ai/api/mcp/auth_callback?x=1",
])
def test_a_list_entry_that_ends_in_a_star_allows_plain_paths_only(site, uri):
    r = register(site, redirect=uri)
    assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri"


def test_plain_paths_under_a_starred_entry_still_register(site):
    for uri in ("https://chatgpt.com/connector/oauth/abc123", "https://chatgpt.com/connector/oauth/a/b-c_d.e~f"):
        assert register(site, redirect=uri).status_code == 201, uri
    # A line break on the end is dropped when the address is read, so what is kept is the plain one.
    r = register(site, redirect="https://chatgpt.com/connector/oauth/x\n")
    assert r.status_code == 201 and r.json()["redirect_uris"] == ["https://chatgpt.com/connector/oauth/x"]


def test_a_name_made_to_mislead_is_shown_without_its_tricks(site, caplog):
    caplog.set_level(logging.INFO)
    olly = sign_in(site)
    # Text that runs backwards, a hidden character, a line break for the log, a colour code for a terminal.
    cid = register(site, name="Cl\u202eedua\u200b \x00by\r\nINFO forged line \x1b[31mred").json()["client_id"]
    page = site.get("/connect?req=" + req_of(ask(site, cid)), headers=olly).text
    assert "Connect Cledua by INFO forged line [31mred?" in page
    assert not any(ch in page for ch in "\u202e\u200b\x00\x1b")
    assert swap(site, cid, allow(site, cid, olly)).status_code == 200
    assert site.get("/api/account", headers=olly).json()["apps"][0]["app"] == "Cledua by INFO forged line [31mred"
    assert "connected an AI app (Cledua by INFO forged line [31mred)" in caplog.text
    hidden = register(site, name="\u200b\u202e").json()["client_id"]
    assert "Connect An AI app?" in site.get("/connect?req=" + req_of(ask(site, hidden)), headers=olly).text


@pytest.mark.parametrize("challenge", ["", "short", "x" * 44, "x" * 42 + "=", "x" * 42 + "\n"])
def test_an_ai_app_has_to_send_real_proof_to_tie_the_code_to(site, challenge):
    cid = register(site).json()["client_id"]
    r = site.get("/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": CLAUDE, "code_challenge": challenge,
                                       "code_challenge_method": "S256", "state": "s"}, follow_redirects=False)
    assert "req=" not in r.headers.get("location", "") and count(site, "oauth_requests") == 0
    assert r.status_code == 400 or "error=invalid_request" in r.headers["location"]


# ---------- people signing in ----------

@pytest.fixture
def alerts(monkeypatch):
    from innerface import push, service
    sent = []
    monkeypatch.setattr(service, "_submit", lambda fn, *a: fn(*a))
    monkeypatch.setattr(push, "webpush", lambda **kw: sent.append((kw["subscription_info"]["endpoint"], kw["data"])))
    return sent


def idea(c, who, text="Secret plan for Q3 pricing"):
    assert c.post("/api/cards/dump", json={"text": text, "as_is": True}, headers=who).status_code == 200


def test_a_browser_that_was_signed_out_gets_no_more_alerts(site, alerts):
    phone, laptop, pat = sign_in(site), sign_in(site, agent="Mozilla/5.0 (Macintosh) Chrome/130"), sign_in(site, PARTNER)
    assert site.post("/api/push/subscribe", json=SUB, headers=phone).status_code == 200
    idea(site, pat, "First")
    assert [e for e, _ in alerts] == [SUB["endpoint"]]
    # Olly signs the lost phone out from the laptop. Its alerts stop with it.
    other = next(b["id"] for b in site.get("/api/account", headers=laptop).json()["browsers"] if not b["this_one"])
    assert site.delete(f"/api/account/browsers/{other}", headers=laptop).status_code == 200
    assert count(site, "push_subscriptions") == 0
    idea(site, pat)
    assert len(alerts) == 1
    # The same for the browser's own Sign out...
    assert site.post("/api/push/subscribe", json=SUB, headers=laptop).status_code == 200
    assert site.post("/api/signout", json={}, headers=laptop).status_code == 200 and count(site, "push_subscriptions") == 0
    # ...for a sign-in that runs out, and for one pushed out by newer ones.
    for ended in ("UPDATE sessions SET expires_at=? WHERE email=?", "DELETE FROM sessions WHERE expires_at > ? AND email=?"):
        browser = sign_in(site)
        assert site.post("/api/push/subscribe", json=SUB, headers=browser).status_code == 200
        rows(site, ended, LONG_AGO, OWNER)
        idea(site, pat)
        assert len(alerts) == 1 and count(site, "push_subscriptions") == 0
    # Opening the app signed in again saves the subscription again, and the alerts come back.
    assert site.post("/api/push/subscribe", json=SUB, headers=sign_in(site)).status_code == 200
    idea(site, pat, "Second")
    assert len(alerts) == 2 and "Second" in alerts[1][1]


def test_a_shared_browser_alerts_whoever_is_signed_in_on_it(site, alerts):
    olly = sign_in(site)
    assert site.post("/api/push/subscribe", json=SUB, headers=olly).status_code == 200
    site.post("/api/signout", json={}, headers=olly)
    pat = sign_in(site, PARTNER)
    assert site.post("/api/push/subscribe", json=SUB, headers=pat).status_code == 200
    assert rows(site, "SELECT email FROM push_subscriptions") == [(PARTNER,)]
    idea(site, pat)   # Pat's own idea alerts Olly, who is signed out on this browser
    assert alerts == []


def test_alerts_are_untouched_where_sign_in_is_not_built_in(tmp_path, monkeypatch, alerts):
    with make_client(tmp_path, monkeypatch) as c:
        c.data = tmp_path
        assert c.post("/api/push/subscribe", json=SUB, headers={"X-Dev-User": OWNER, **WEB}).status_code == 200
        idea(c, {"X-Dev-User": PARTNER, **WEB})
        assert len(alerts) == 1 and rows(c, "SELECT session_id FROM push_subscriptions") == [("",)]


def test_someone_taken_out_in_the_settings_file_is_signed_out_at_the_next_start(tmp_path, monkeypatch, caplog):
    fake = Fake()
    monkeypatch.setattr(signin, "providers", lambda s: {"fake": fake})
    with make_client(tmp_path, monkeypatch, **BUILTIN) as c:
        c.fake, c.data = fake, tmp_path
        olly, pat = sign_in(c), sign_in(c, PARTNER)
        conn = connect_app(c, pat)
        assert c.post("/api/push/subscribe", json=SUB, headers=pat).status_code == 200
    with make_client(tmp_path, monkeypatch, config=CONFIG.replace('givers = ["pat"]', "givers = []"), **BUILTIN) as c:
        c.data = tmp_path
        assert count(c, "sessions", "email=?", PARTNER) == 0 and count(c, "oauth_grants") == 0 and count(c, "push_subscriptions") == 0
        assert "is in no project any more" in caplog.text
        assert c.get("/api/me", headers=pat).status_code == 401 and c.get("/api/me", headers=olly).status_code == 200
        # And the sign-in page is there for them, to come in with another account.
        assert c.get("/signin", headers=pat, follow_redirects=False).status_code == 200
    with make_client(tmp_path, monkeypatch, **BUILTIN) as c:
        # Added back, they start clean, as when a receiver takes someone out in Settings.
        assert c.get("/api/me", headers=pat).status_code == 401
        assert token(c, conn["client_id"], grant_type="refresh_token", refresh_token=conn["refresh_token"]).status_code == 400


def test_someone_who_is_in_no_project_any_more_holds_nothing_after_they_are_turned_away(site):
    pat = sign_in(site, PARTNER)
    conn = connect_app(site, pat)
    back_in = without(PARTNER)
    # Through the web app...
    assert site.get("/api/me", headers=pat).status_code == 403
    assert count(site, "sessions") == 0 and count(site, "oauth_grants") == 0
    back_in()
    assert site.get("/api/me", headers=pat).status_code == 401 and mcp(site, conn["access_token"]).status_code == 401
    # ...through an AI app...
    pat = sign_in(site, PARTNER)
    conn = connect_app(site, pat)
    back_in = without(PARTNER)
    assert mcp(site, conn["access_token"]).status_code == 403 and count(site, "sessions") == 0 and count(site, "oauth_grants") == 0
    back_in()
    # ...and on the sign-in page, which no longer sends them round in a circle.
    pat = sign_in(site, PARTNER)
    back_in = without(PARTNER)
    r = site.get("/signin", headers=pat, follow_redirects=False)
    assert r.status_code == 200 and "isn&#x27;t in any project here" in r.text and "Continue with Fake" in r.text and count(site, "sessions") == 0
    back_in()
    # Someone who is in a project is still sent straight on.
    r = site.get("/signin", headers=sign_in(site, PARTNER), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"


@pytest.mark.parametrize("lead", ["\xa0", "\x85", "\x1c", "\x1f", "\u2003", "\x0b", "\x0c"])
def test_a_cookie_whose_name_only_looks_right_is_not_the_session(site, lead):
    olly = sign_in(site)
    value = olly["Cookie"].split("=", 1)[1]
    # A browser treats these as part of the name, so such a cookie never had the protection the real one's name gives it.
    assert signin.read_cookie(f"other=1; {lead}{SESSION}={value}", SESSION) == ""
    assert signin.read_cookie(f"other=1;{SESSION}={value}", SESSION) == signin.read_cookie(f"other=1; \t{SESSION}={value} ", SESSION) == value
    if ord(lead) < 0x80:   # the test client can't send the others as they are
        raw = {"Cookie": (f"other=1; {lead}{SESSION}={value}").encode("latin-1"), **WEB}
        assert site.get("/api/me", headers=raw).status_code == 401
        state, pending = start(site)
        r = site.get(f"/signin/fake/back?state={state}&code=abc", headers={"Cookie": f"{lead}{PENDING}={pending}".encode("latin-1")}, follow_redirects=False)
        assert r.headers["location"] == "/signin?e=failed"


def test_two_replies_to_one_sign_in_at_the_same_moment_open_one_session(site, monkeypatch):
    s = config.get_settings()
    state, pending = start(site)
    plain, calls, second = signin.hashed, [], []

    def hashed(secret):
        calls.append(secret)
        if len(calls) == 2:   # the first reply has looked the sign-in up and not yet used it up: a second reply arrives
            second.append(signin.complete(s, "fake", state, "abc", pending)[0])
        return plain(secret)
    monkeypatch.setattr(signin, "hashed", hashed)
    with pytest.raises(signin.SignInError) as e:
        signin.complete(s, "fake", state, "abc", pending)
    assert e.value.code == "failed" and len(second) == 1 and count(site, "sessions") == 1


def test_a_providers_clock_a_little_ahead_is_fine(google):
    _, answer, _ = google
    now = int(time.time())
    assert answer(statement(iat=now + 2)) == OWNER and answer(statement(iat=now + 50)) == OWNER
    # The grace is for the clock only. It doesn't make a statement last longer, or let one from well ahead through.
    for changes in ({"iat": now + 600}, {"exp": now - 1}, {"exp": now - 50}, {"exp": "later"}):
        with pytest.raises(signin.SignInError):
            answer(statement(**changes))


@pytest.mark.parametrize("public", ["https://Ideas.Example.com", "https://ideas.example.com:443", "HTTPS://IDEAS.EXAMPLE.COM/"])
def test_an_address_written_another_way_still_matches_the_browser(tmp_path, monkeypatch, public):
    fake = Fake()
    monkeypatch.setattr(signin, "providers", lambda s: {"fake": fake})
    with make_client(tmp_path, monkeypatch, **{**BUILTIN, "PUBLIC_URL": public}) as c:
        c.fake = fake
        assert config.get_settings().public_url == URL
        olly = sign_in(c)
        assert c.post("/api/signout", json={}, headers={**olly, "Origin": "https://evil.example"}).status_code == 403
        assert c.post("/api/signout", json={}, headers={**olly, "Origin": URL}).status_code == 200


def test_other_ports_and_plain_http_are_left_as_written(tmp_path, monkeypatch):
    for written, kept in (("https://ideas.example.com:8443", "https://ideas.example.com:8443"), ("http://LocalHost:80", "http://localhost"),
                          ("http://localhost:8080", "http://localhost:8080"), ("", "")):
        assert config._origin(written) == kept


def test_where_to_go_next_and_a_providers_id_end_where_they_should(site):
    ok = "/connect?req=" + "a" * 43
    assert signin._NEXT.match(ok) and not signin._NEXT.match(ok + "\n") and signin._ID.match("fake") and not signin._ID.match("fake\n")
    state, pending = start(site, ok + "\n")
    assert back(site, state, pending).headers["location"] == "/"


def test_signing_in_again_closes_the_sign_in_the_browser_came_with(site, alerts):
    pat, first = sign_in(site, PARTNER), sign_in(site)
    assert site.post("/api/push/subscribe", json=SUB, headers=first).status_code == 200
    state, pending = start(site)
    r = site.get(f"/signin/fake/back?state={state}&code=abc", headers={"Cookie": f"{PENDING}={pending}; {first['Cookie']}"}, follow_redirects=False)
    second = {"Cookie": f"{SESSION}={set_cookies(r)[SESSION][0]}", **WEB}
    assert site.get("/api/me", headers=first).status_code == 401 and site.get("/api/me", headers=second).status_code == 200
    assert count(site, "sessions", "email=?", OWNER) == 1
    # A sign-in that doesn't finish closes nothing.
    site.fake.error = "failed"
    state, pending = start(site)
    r = site.get(f"/signin/fake/back?state={state}&code=abc", headers={"Cookie": f"{PENDING}={pending}; {second['Cookie']}"}, follow_redirects=False)
    assert r.headers["location"] == "/signin?e=failed" and site.get("/api/me", headers=second).status_code == 200
    # Someone else's sign-in is never touched, and the alerts went with the sign-in that was closed.
    assert site.get("/api/me", headers=pat).status_code == 200 and count(site, "push_subscriptions") == 0


def test_sign_ins_nobody_finished_are_cleared_away(site, monkeypatch):
    start(site), start(site)
    assert count(site, "signin_states") == 2
    monkeypatch.setattr(signin, "STATE_SECONDS", -1)
    # Any reply clears them, even one that matches nothing. So does starting the app.
    assert back(site, "nothing", "x").headers["location"] == "/signin?e=failed" and count(site, "signin_states") == 0
    monkeypatch.setattr(signin, "STATE_SECONDS", 600)
    start(site)
    monkeypatch.setattr(signin, "STATE_SECONDS", -1)
    signin.tidy(config.get_settings())
    assert count(site, "signin_states") == 0


def test_what_is_kept_as_it_is_and_what_only_as_a_hash(site):
    olly = sign_in(site)
    conn = connect_app(site, olly)
    cid = register(site).json()["client_id"]
    asked = req_of(ask(site, cid, state="what-the-app-sent"))
    kept = stored(site)
    # Anything that signs someone in, or lets an app in, is not there to be copied.
    for secret in (olly["Cookie"].split("=", 1)[1], conn["access_token"], conn["refresh_token"], conn["code"], asked):
        assert secret not in kept
    # What the docs say is kept as it is, is: here, what a waiting request asked for.
    assert "what-the-app-sent" in kept


def test_a_database_from_before_this_gains_what_it_needs(tmp_path, monkeypatch, alerts):
    fake = Fake()
    monkeypatch.setattr(signin, "providers", lambda s: {"fake": fake})
    with make_client(tmp_path, monkeypatch, **BUILTIN) as c:
        c.fake, c.data = fake, tmp_path
        conn = connect_app(c, sign_in(c))
        for table, column in (("push_subscriptions", "session_id"), ("oauth_clients", "used_at"), ("oauth_grants", "code_hash")):
            rows(c, f"ALTER TABLE {table} DROP COLUMN {column}")
        rows(c, "INSERT INTO push_subscriptions(endpoint,email,p256dh,auth,created_at) VALUES (?,?,?,?,?)", SUB["endpoint"], OWNER, "k", "a", LONG_AGO)
    with make_client(tmp_path, monkeypatch, **BUILTIN) as c:
        c.fake, c.data = fake, tmp_path
        # The app that was connected counts as used, so the next clear-out leaves it be.
        assert count(c, "oauth_clients", "used_at IS NOT NULL") == 1
        assert token(c, conn["client_id"], grant_type="refresh_token", refresh_token=conn["refresh_token"]).status_code == 200
        # An alert subscription from before isn't tied to a sign-in, so it waits for the app to be opened again.
        olly, pat = sign_in(c), sign_in(c, PARTNER)
        idea(c, pat)
        assert alerts == []
        assert c.post("/api/push/subscribe", json=SUB, headers=olly).status_code == 200
        idea(c, pat)
        assert len(alerts) == 1
