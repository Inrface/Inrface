"""Connectors: the shared layer, the signed webhook, Linear, and Start build sending to several places."""
import hashlib
import hmac
import json
import sqlite3

import httpx
import pytest

from test_app import AS_O, AS_P, AS_Q, H, client, dump, gh, make_client, mcp, pushes, scope, tool  # noqa: F401

LINEAR_KEY = "lin_api_" + "k" * 32
BASE = "/api/projects/main/connectors"


def add(c, kind="webhook", who=AS_O, base=BASE, **values):
    if kind == "webhook":
        values = values or {"url": "https://hooks.example.com/inrface"}
    r = c.post(base, json={"kind": kind, "values": values}, headers=who)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def hook(monkeypatch):
    """A stand-in for the outside world: records what a webhook connector posts."""
    from innerface import net
    state = {"posts": [], "status": 200, "reply": b'{"url": "https://tracker.example.com/items/7", "id": 7}'}

    def post(url, body, headers, allow_private=False, timeout=10.0):
        net.check_url(url, allow_private)
        state["posts"].append({"url": url, "body": body, "headers": headers})
        return state["status"], state["reply"]

    monkeypatch.setattr(net, "post", post)
    return state


@pytest.fixture
def linear(monkeypatch):
    """A stand-in for Linear's GraphQL API."""
    from innerface import linear as mod
    state = {"calls": [], "issues": [], "teams": [{"id": "team-1", "key": "ENG", "name": "Engineering"}],
             "labels": [{"id": "label-1", "name": "from-inrface", "team": None}], "fail": None}

    def post(url, json=None, headers=None, timeout=None):
        state["calls"].append({"url": url, "auth": headers["Authorization"], "query": json["query"], "variables": json["variables"]})
        if state["fail"]:
            return httpx.Response(state["fail"][0], json=state["fail"][1])
        if headers["Authorization"] != LINEAR_KEY:
            return httpx.Response(401, json={"errors": [{"message": "Authentication required", "extensions": {"userError": True}}]})
        q = json["query"]
        if "issueCreate" in q:
            state["issues"].append(json["variables"]["input"])
            n = len(state["issues"])
            return httpx.Response(200, json={"data": {"issueCreate": {"success": True, "issue": {"id": f"uuid-{n}", "identifier": f"ENG-{n}", "url": f"https://linear.app/acme/issue/ENG-{n}"}}}})
        if "issueLabels" in q:
            return httpx.Response(200, json={"data": {"issueLabels": {"nodes": [x for x in state["labels"] if x["name"].lower() == json["variables"]["name"].lower()]}}})
        return httpx.Response(200, json={"data": {"viewer": {"name": "Olly"}, "organization": {"name": "Acme"},
                                                  "teams": {"nodes": [t for t in state["teams"] if t["key"] == json["variables"]["key"]]}}})

    monkeypatch.setattr(mod.httpx, "post", post)
    return state


def db_text(c, sql):
    from innerface import db
    from innerface.config import get_settings
    with db.connect(get_settings()) as conn:
        return json.dumps([tuple(r) for r in conn.execute(sql).fetchall()])


# ----- the shared layer -----

def test_connectors_are_for_receivers_only(client):
    assert client.get(BASE, headers=AS_P).status_code == 404  # a giver on this project
    assert client.get(BASE, headers=AS_Q).status_code == 404  # not on this project at all
    assert client.post(BASE, json={"kind": "webhook", "values": {"url": "https://a.example.com/x"}}, headers=AS_P).status_code == 404
    assert client.post(BASE, json={"kind": "webhook", "values": {"url": "https://a.example.com/x"}}, headers={"X-Dev-User": "owner@example.com"}).status_code == 403  # no CSRF header
    made = add(client)
    for method, path in (("PUT", f"{BASE}/{made['id']}"), ("DELETE", f"{BASE}/{made['id']}"), ("POST", f"{BASE}/{made['id']}/test")):
        assert client.request(method, path, json={"enabled": False} if method == "PUT" else None, headers=AS_P).status_code == 404
    # Another project's receiver can't reach it through their own project either.
    assert client.put(f"/api/projects/side/connectors/{made['id']}", json={"enabled": False}, headers=AS_Q).status_code == 404
    assert client.get(BASE, headers=AS_O).json()["connectors"][0]["enabled"] is True


def test_listing_describes_kinds_and_their_fields(client):
    d = client.get(BASE, headers=AS_O).json()
    kinds = {k["id"]: k for k in d["kinds"]}
    assert {"webhook", "linear"} <= set(kinds) and d["connectors"] == [] and d["encrypted"] is False
    assert [f["key"] for f in kinds["linear"]["fields"]] == ["api_key", "team", "label"]
    assert kinds["linear"]["fields"][0]["secret"] and kinds["webhook"]["fields"][1]["generated"]
    assert all(k["docs"].startswith("/docs/") for k in kinds.values())
    # Slack brings ideas in. Start build never goes to it.
    assert {k: v["sends_builds"] for k, v in kinds.items()} == {"webhook": True, "linear": True, "slack": False}


def test_bad_input_is_refused_without_echoing_it(client):
    def post(**body):
        return client.post(BASE, json=body, headers=AS_O)
    assert post(kind="nope", values={}).status_code == 422
    assert post(kind="linear", values={"team": "ENG"}).json()["detail"] == "API key is needed"
    assert post(kind="linear", values={"api_key": LINEAR_KEY, "team": "ENG", "extra": "x"}).status_code == 422
    assert post(kind="linear", values={"api_key": LINEAR_KEY, "team": "ENG"}, surprise=1).status_code == 422
    r = post(kind="linear", values={"api_key": "short secret!", "team": "ENG"})
    assert r.status_code == 422 and "short secret" not in r.text
    assert post(kind="webhook", values={"url": "http://hooks.example.com/x"}).status_code == 422
    assert post(kind="webhook", values={"url": "https://user:pw@hooks.example.com/x"}).status_code == 422
    assert post(kind="webhook", name="n" * 61, values={"url": "https://hooks.example.com/x"}).status_code == 422
    assert client.get(BASE, headers=AS_O).json()["connectors"] == []


def test_secrets_go_in_and_never_come_back(client, linear):
    made = add(client, "linear", api_key=LINEAR_KEY, team="eng", label="from-inrface")
    assert made["values"] == {"team": "ENG", "label": "from-inrface"} and made["reveal"] == {}
    assert made["secrets"] == {"api_key": {"set": True, "hint": LINEAR_KEY[-4:]}}
    cid = made["id"]
    # A blank secret on an edit keeps the saved one.
    r = client.put(f"{BASE}/{cid}", json={"name": "Our Linear", "values": {"api_key": "", "team": "ENG"}}, headers=AS_O)
    assert r.status_code == 200 and r.json()["name"] == "Our Linear" and r.json()["secrets"]["api_key"]["set"]
    assert client.post(f"{BASE}/{cid}/test", json={}, headers=AS_O).status_code == 200
    for text in (json.dumps(made), r.text, client.get(BASE, headers=AS_O).text):
        assert LINEAR_KEY not in text


def test_generated_secret_is_shown_once(client, hook):
    made = add(client)
    secret = made["reveal"]["signing_secret"]
    assert secret.startswith("whsec_") and made["secrets"]["signing_secret"] == {"set": True, "hint": secret[-4:]}
    assert secret not in client.get(BASE, headers=AS_O).text
    # Editing the address keeps the secret; asking for a new one replaces it.
    r = client.put(f"{BASE}/{made['id']}", json={"values": {"url": "https://hooks.example.com/v2"}}, headers=AS_O).json()
    assert r["reveal"] == {} and r["values"]["url"].endswith("/v2")
    r = client.put(f"{BASE}/{made['id']}", json={"regenerate": True}, headers=AS_O).json()
    assert r["reveal"]["signing_secret"] not in ("", secret)
    # A signing secret can't be chosen by the person.
    assert client.put(f"{BASE}/{made['id']}", json={"values": {"signing_secret": "mine"}}, headers=AS_O).json()["reveal"] == {}
    client.post(f"{BASE}/{made['id']}/test", json={}, headers=AS_O)
    sent = hook["posts"][-1]
    t = sent["headers"]["X-Inrface-Signature"].split(",")[0][2:]
    assert sent["headers"]["X-Inrface-Signature"].endswith(hmac.new(r["reveal"]["signing_secret"].encode(), f"{t}.".encode() + sent["body"], hashlib.sha256).hexdigest())


def test_turn_off_remove_and_the_limit(client, hook):
    made = add(client)
    assert client.get("/api/me", headers=AS_O).json()["projects"][0]["build_targets"] == ["GitHub", "Webhook"]
    client.put(f"{BASE}/{made['id']}", json={"enabled": False}, headers=AS_O)
    assert client.get("/api/me", headers=AS_O).json()["projects"][0]["build_targets"] == ["GitHub"]
    assert client.put(f"{BASE}/{made['id']}", json={}, headers=AS_O).status_code == 422
    assert client.delete(f"{BASE}/{made['id']}", headers=AS_O).json() == {"ok": True}
    assert client.delete(f"{BASE}/{made['id']}", headers=AS_O).status_code == 404
    for _ in range(12):
        add(client)
    assert client.post(BASE, json={"kind": "webhook", "values": {"url": "https://hooks.example.com/x"}}, headers=AS_O).status_code == 422


def test_test_button_records_the_outcome(client, hook):
    made = add(client)
    r = client.post(f"{BASE}/{made['id']}/test", json={}, headers=AS_O)
    assert r.json()["ok"] is True
    body = json.loads(hook["posts"][0]["body"])
    assert body["event"] == "ping" and body["project"] == {"id": "main", "name": "Main"} and hook["posts"][0]["headers"]["X-Inrface-Event"] == "ping"
    assert client.get(BASE, headers=AS_O).json()["connectors"][0]["last_ok_at"]
    hook["status"] = 500
    r = client.post(f"{BASE}/{made['id']}/test", json={}, headers=AS_O)
    assert r.status_code == 502 and "500" in r.json()["detail"]
    assert "500" in client.get(BASE, headers=AS_O).json()["connectors"][0]["last_error"]


# ----- outbound safety -----

def test_private_addresses_are_refused():
    from innerface import net
    for url in ("https://127.0.0.1/x", "https://localhost/x", "https://169.254.169.254/latest/meta-data", "https://10.0.0.8/x", "https://[::1]/x", "https://192.168.1.10:8443/x"):
        with pytest.raises(net.UnsafeAddress):
            net.post(url, b"{}", {})
    for url in ("http://example.com/x", "ftp://example.com/x", "https://user:pw@example.com/x", "not a url", ""):
        with pytest.raises(net.UnsafeAddress):
            net.check_url(url)
    assert net.check_url("http://10.0.0.8/x", allow_private=True).host == "10.0.0.8"
    assert net._public_address("10.0.0.8", 80, allow_private=True) == "10.0.0.8"


def test_webhook_to_a_private_address_fails_safely(client):
    made = add(client, url="https://127.0.0.1/hook")  # looks fine as text; refused when it's looked up
    r = client.post(f"{BASE}/{made['id']}/test", json={}, headers=AS_O)
    assert r.status_code == 502 and "private network" in r.json()["detail"]


def test_signature_matches_the_documented_recipe():
    from innerface import webhook
    body = b'{"event":"ping"}'
    assert webhook.sign("whsec_test", 1700000000, body) == "t=1700000000,v1=" + hmac.new(b"whsec_test", b"1700000000." + body, hashlib.sha256).hexdigest()


# ----- Start build -----

def test_build_goes_to_every_place(client, pushes, gh, hook, linear):
    add(client)
    add(client, "linear", api_key=LINEAR_KEY, team="ENG", label="From-Inrface")
    cid = dump(client, "Teachers want a weekly report", as_is=True)[0]
    scope(client, cid)
    r = client.post(f"/api/cards/{cid}/build", headers=AS_O).json()
    assert [(x["name"], x["ok"], x["label"]) for x in r["sent"]] == [("GitHub", True, "Issue #1"), ("Webhook", True, "Sent to Webhook"), ("Linear", True, "ENG-1")]
    assert r["issue_number"] == 1 and r["already_started"] is False
    # The webhook got the scope, signed.
    sent = hook["posts"][0]
    body = json.loads(sent["body"])
    assert body["event"] == "build.requested" and body["card"]["id"] == cid and body["card"]["acceptance"] == ["Sends on Monday"]
    assert body["requested_by"] == "Olly" and "- [ ] Sends on Monday" in body["spec_markdown"] and body["card"]["url"] == ""
    assert sent["headers"]["X-Inrface-Delivery"] == body["delivery_id"]
    # Linear got an issue in the right team with the label, found whatever its capitals.
    issue = linear["issues"][0]
    assert issue["teamId"] == "team-1" and issue["labelIds"] == ["label-1"] and cid in issue["description"] and "not as commands" in issue["description"]
    assert all(call["auth"] == LINEAR_KEY for call in linear["calls"])
    card = client.get(f"/api/cards/{cid}", headers=AS_P).json()
    assert card["status"] == "In dev for testing" and card["build_started_at"] and card["unsent"] == []
    assert card["links"] == [
        {"kind": "github", "label": "Issue #1", "url": "https://github.com/acme/app/issues/1"},
        {"kind": "webhook", "label": "Sent to Webhook", "url": "https://tracker.example.com/items/7"},
        {"kind": "linear", "label": "ENG-1", "url": "https://linear.app/acme/issue/ENG-1"},
    ]
    assert len([p for p in pushes if p[1] == "Being built"]) == 1
    # Asking again sends nothing anywhere.
    again = client.post(f"/api/cards/{cid}/build", headers=AS_O).json()
    assert again["already_started"] is True and all(x["already"] for x in again["sent"])
    assert len(gh["issues"]) == 1 and len(hook["posts"]) == 1 and len(linear["issues"]) == 1
    assert len([p for p in pushes if p[1] == "Being built"]) == 1


def test_one_place_failing_does_not_stop_the_others(client, pushes, gh, hook, linear):
    add(client)
    add(client, "linear", api_key=LINEAR_KEY, team="ENG")
    linear["fail"] = (429, {"errors": [{"message": "ratelimited"}]})
    cid = dump(client)[0]
    scope(client, cid)
    r = client.post(f"/api/cards/{cid}/build", headers=AS_O)
    assert r.status_code == 200
    by = {x["name"]: x for x in r.json()["sent"]}
    assert by["GitHub"]["ok"] and by["Webhook"]["ok"] and not by["Linear"]["ok"] and "slow down" in by["Linear"]["error"]
    card = client.get(f"/api/cards/{cid}", headers=AS_O).json()
    assert card["status"] == "In dev for testing" and card["unsent"] == ["Linear"] and len(card["links"]) == 2
    assert client.get(f"/api/cards/{cid}", headers=AS_P).json()["unsent"] == []  # givers aren't offered the retry
    assert "slow down" in client.get(BASE, headers=AS_O).json()["connectors"][1]["last_error"]
    # The retry reaches only the place that's missing.
    linear["fail"] = None
    r = client.post(f"/api/cards/{cid}/build", headers=AS_O).json()
    by = {x["name"]: x for x in r["sent"]}
    assert by["GitHub"]["already"] and by["Webhook"]["already"] and by["Linear"]["ok"] and not by["Linear"]["already"] and r["already_started"] is False
    assert len(gh["issues"]) == 1 and len(hook["posts"]) == 1 and len(linear["issues"]) == 1
    assert client.get(f"/api/cards/{cid}", headers=AS_O).json()["unsent"] == []
    assert len([p for p in pushes if p[1] == "Being built"]) == 1


def test_everything_failing_leaves_the_card_alone(client, pushes, hook, monkeypatch):
    from innerface import github
    def refuse(*a):
        raise github.GitHubError("GitHub refused the request.")
    monkeypatch.setattr(github, "create_issue", refuse)
    add(client)
    hook["status"] = 503
    cid = dump(client)[0]
    scope(client, cid)
    r = client.post(f"/api/cards/{cid}/build", headers=AS_O)
    assert r.status_code == 502 and "refused" in r.json()["detail"]
    card = client.get(f"/api/cards/{cid}", headers=AS_O).json()
    assert card["status"] == "New" and not card["build_started_at"] and card["links"] == [] and card["unsent"] == []
    assert client.put(f"/api/cards/{cid}/scope", json={"summary": "still editable"}, headers=AS_O).status_code == 200


def test_a_project_with_only_a_connector_can_build(client, pushes, hook):
    side = "/api/projects/side/connectors"
    assert client.get("/api/me", headers=AS_Q).json()["projects"][0]["builds"] is False
    add(client, who=AS_Q, base=side)
    me = client.get("/api/me", headers=AS_Q).json()["projects"][0]
    assert me["builds"] is True and me["build_targets"] == ["Webhook"]
    cid = dump(client, "side idea", who=AS_Q)[0]
    client.put(f"/api/cards/{cid}/scope", json={"summary": "x"}, headers=AS_Q)
    hook["reply"] = b"thanks"  # a reply that isn't JSON is fine
    r = client.post(f"/api/cards/{cid}/build", headers=AS_Q).json()
    assert r["issue_number"] is None and r["sent"][0]["ok"] and r["sent"][0]["url"] == ""
    card = client.get(f"/api/cards/{cid}", headers=AS_Q).json()
    assert card["status"] == "In dev for testing" and card["links"] == [{"kind": "webhook", "label": "Sent to Webhook", "url": ""}]
    assert client.put(f"/api/cards/{cid}/scope", json={"summary": "changed"}, headers=AS_Q).status_code == 409


def test_a_link_from_a_webhook_must_be_https(client, pushes, hook):
    add(client, who=AS_Q, base="/api/projects/side/connectors")
    cid = dump(client, "side idea", who=AS_Q)[0]
    client.put(f"/api/cards/{cid}/scope", json={"summary": "x"}, headers=AS_Q)
    hook["reply"] = b'{"url": "javascript:alert(1)", "id": {"nested": true}}'
    assert client.post(f"/api/cards/{cid}/build", headers=AS_Q).json()["sent"][0]["url"] == ""


def test_a_connector_added_later_can_still_get_the_build(client, pushes, gh, hook):
    cid = dump(client)[0]
    scope(client, cid)
    client.post(f"/api/cards/{cid}/build", headers=AS_O)
    add(client)
    assert client.get(f"/api/cards/{cid}", headers=AS_O).json()["unsent"] == ["Webhook"]
    r = client.post(f"/api/cards/{cid}/build", headers=AS_O).json()
    assert [(x["name"], x["already"]) for x in r["sent"]] == [("GitHub", True), ("Webhook", False)]
    assert len(gh["issues"]) == 1 and len(hook["posts"]) == 1


def test_two_quick_presses_send_once(client, pushes, gh):
    from innerface import db
    from innerface.config import get_settings
    s = get_settings()
    cid = dump(client)[0]
    scope(client, cid)
    with db.connect(s) as c:  # another request is part-way through sending
        c.execute("INSERT INTO card_links(id, card_id, connector_id, kind, role, state, created_at) VALUES ('x', ?, 'github', 'github', 'build', 'pending', ?)", (cid, db.now()))
    r = client.post(f"/api/cards/{cid}/build", headers=AS_O)
    assert r.status_code == 409 and "already starting" in r.json()["detail"] and gh["issues"] == []
    with db.connect(s) as c:  # that request died long ago, so the next press takes over
        c.execute("UPDATE card_links SET created_at=? WHERE id='x'", (db.ago(600),))
    assert client.post(f"/api/cards/{cid}/build", headers=AS_O).json()["issue_number"] == 1
    assert len(gh["issues"]) == 1


def test_card_link_back_uses_public_url(tmp_path, monkeypatch, pushes, gh, hook):
    with make_client(tmp_path, monkeypatch, PUBLIC_URL="https://ideas.example.com") as c:
        add(c)
        cid = dump(c)[0]
        scope(c, cid)
        c.post(f"/api/cards/{cid}/build", headers=AS_O)
        link = f"https://ideas.example.com/#card={cid}"
        assert json.loads(hook["posts"][0]["body"])["card"]["url"] == link and link in gh["issues"][0]["body"]


# ----- Linear -----

def test_linear_test_reports_what_it_reached(client, linear):
    made = add(client, "linear", api_key=LINEAR_KEY, team="ENG", label="missing label")
    d = client.post(f"{BASE}/{made['id']}/test", json={}, headers=AS_O).json()["detail"]
    assert "Acme" in d and "Engineering (ENG)" in d and "missing label" in d
    assert linear["calls"][0]["url"] == "https://api.linear.app/graphql" and linear["issues"] == []


def test_linear_errors_are_plain_and_keep_secrets_out(client, linear, monkeypatch):
    wrong = add(client, "linear", api_key="lin_api_" + "w" * 32, team="ENG")
    r = client.post(f"{BASE}/{wrong['id']}/test", json={}, headers=AS_O)
    assert r.status_code == 502 and "refused the API key" in r.json()["detail"] and "wwww" not in r.text
    lost = add(client, "linear", api_key=LINEAR_KEY, team="OPS")
    assert "no team with the key OPS" in client.post(f"{BASE}/{lost['id']}/test", json={}, headers=AS_O).json()["detail"]
    ok = add(client, "linear", api_key=LINEAR_KEY, team="ENG")
    # Linear's own wording is repeated only when Linear marks it as meant for the person.
    linear["fail"] = (200, {"errors": [{"message": "internal stack trace at db.internal", "extensions": {}}]})
    assert "stack trace" not in client.post(f"{BASE}/{ok['id']}/test", json={}, headers=AS_O).text
    linear["fail"] = (400, {"errors": [{"message": "Title is too long", "extensions": {"userError": True}}]})
    assert "Title is too long" in client.post(f"{BASE}/{ok['id']}/test", json={}, headers=AS_O).json()["detail"]
    def down(*a, **k):
        raise httpx.ConnectError("no route")
    from innerface import linear as mod
    monkeypatch.setattr(mod.httpx, "post", down)
    assert "Couldn't reach Linear" in client.post(f"{BASE}/{ok['id']}/test", json={}, headers=AS_O).json()["detail"]


def test_linear_prefers_the_teams_own_label(client, pushes, linear):
    linear["labels"] = [{"id": "other-team", "name": "bug", "team": {"id": "team-9"}}, {"id": "workspace", "name": "bug", "team": None}, {"id": "ours", "name": "bug", "team": {"id": "team-1"}}]
    add(client, "linear", who=AS_Q, base="/api/projects/side/connectors", api_key=LINEAR_KEY, team="ENG", label="bug")
    cid = dump(client, "side idea", who=AS_Q)[0]
    client.put(f"/api/cards/{cid}/scope", json={"summary": "x"}, headers=AS_Q)
    assert client.post(f"/api/cards/{cid}/build", headers=AS_Q).json()["sent"][0]["label"] == "ENG-1"
    assert linear["issues"][0]["labelIds"] == ["ours"]


# ----- secrets at rest -----

def test_secrets_are_encrypted_when_a_key_is_set(tmp_path, monkeypatch, pushes, gh, hook):
    token = "github_pat_" + "b" * 30
    with make_client(tmp_path, monkeypatch) as c:  # saved before a key was set
        add(c, "linear", api_key=LINEAR_KEY, team="ENG")
        c.put("/api/projects/main/github", json={"token": token}, headers=AS_O)
        assert LINEAR_KEY in db_text(c, "SELECT secrets FROM connectors") and token in db_text(c, "SELECT token FROM project_github")
    with make_client(tmp_path, monkeypatch, INNERFACE_SECRET_KEY="s" * 40) as c:  # the key is turned on
        assert c.get(BASE, headers=AS_O).json()["encrypted"] is True
        made = add(c)
        stored = db_text(c, "SELECT secrets FROM connectors") + db_text(c, "SELECT token FROM project_github")
        assert LINEAR_KEY not in stored and token not in stored and made["reveal"]["signing_secret"] not in stored and stored.count("enc:v1:") == 3
        # Everything still works, with the real values.
        assert c.get("/api/projects/main/github", headers=AS_O).json()["token_hint"] == "bbbb"
        assert c.get(BASE, headers=AS_O).json()["connectors"][0]["secrets"]["api_key"]["hint"] == LINEAR_KEY[-4:]
        assert c.post(f"{BASE}/{made['id']}/test", json={}, headers=AS_O).status_code == 200
    with make_client(tmp_path, monkeypatch, INNERFACE_SECRET_KEY="t" * 40) as c:  # the key was lost and replaced
        listed = c.get(BASE, headers=AS_O).json()["connectors"]
        assert all(x["unreadable"] and not x["secrets"][k]["set"] for x in listed for k in x["secrets"])
        assert c.post(f"{BASE}/{made['id']}/test", json={}, headers=AS_O).status_code == 502
        assert c.get("/api/me", headers=AS_O).json()["projects"][0]["build_targets"] == ["GitHub"]  # the server's own token still works
        assert c.get("/api/projects/main/github", headers=AS_O).json()["token_source"] == "server"
        # Typing the secrets in again repairs it.
        fixed = c.put(f"{BASE}/{listed[0]['id']}", json={"values": {"api_key": LINEAR_KEY}}, headers=AS_O).json()
        assert fixed["unreadable"] is False and fixed["secrets"]["api_key"]["set"]
        assert c.put(f"{BASE}/{made['id']}", json={"name": "Hook"}, headers=AS_O).json()["reveal"]["signing_secret"].startswith("whsec_")


def test_settings_are_checked_at_startup(tmp_path, monkeypatch):
    from innerface.config import ConfigError
    for env in ({"INNERFACE_SECRET_KEY": "too short"}, {"PUBLIC_URL": "ideas.example.com"}, {"PUBLIC_URL": "https://ideas.example.com/path"}):
        with pytest.raises(ConfigError):
            with make_client(tmp_path, monkeypatch, **env):
                pass
        for k in env:
            monkeypatch.delenv(k)


# ----- upgrades, docs and the assistant -----

def test_builds_from_before_connectors_are_carried_over(tmp_path, monkeypatch):
    from innerface import db
    from innerface.config import get_settings
    with make_client(tmp_path, monkeypatch) as c:
        built, stuck = dump(c, "built")[0], dump(c, "stuck")[0]
        s = get_settings()
        with db.connect(s) as conn:  # how an older version left things
            conn.execute("UPDATE cards SET issue_number=4, issue_url='https://github.com/acme/app/issues/4', status='In dev for testing' WHERE id=?", (built,))
            conn.execute("UPDATE cards SET issue_url='pending' WHERE id=?", (stuck,))
        db.init(s)
        db.init(s)  # safe to run on every start
        card = c.get(f"/api/cards/{built}", headers=AS_O).json()
        assert card["build_started_at"] and card["links"] == [{"kind": "github", "label": "Issue #4", "url": "https://github.com/acme/app/issues/4"}]
        assert c.put(f"/api/cards/{built}/scope", json={"summary": "x"}, headers=AS_O).status_code == 409
        other = c.get(f"/api/cards/{stuck}", headers=AS_O).json()
        assert other["issue_url"] == "" and not other["build_started_at"] and other["links"] == []
        with sqlite3.connect(db.db_path(s)) as raw:
            assert raw.execute("SELECT COUNT(*) FROM card_links").fetchone()[0] == 1


def test_reference_pages_are_served(client):
    for name, phrase in (("sign-in", "docker-compose.builtin.yml"), ("connectors", "Start build"), ("webhook", "X-Inrface-Signature"), ("linear", "Team key"), ("slack", "Request URL"), ("intake-api", "external_id")):
        r = client.get(f"/docs/{name}", headers=AS_P)
        assert r.status_code == 200 and phrase in r.text and "<title>" in r.text
    assert client.get("/docs/nope", headers=AS_P).status_code == 404
    assert client.get("/docs/..%2FREADME", headers=AS_P).status_code == 404


def test_mcp_names_where_builds_go(client, pushes, gh, hook):
    add(client)
    projects = tool(client, "list_projects")["projects"]
    assert projects[0]["builds_go_to"] == ["GitHub", "Webhook"]
    cid = dump(client)[0]
    scope(client, cid)
    hook["status"] = 500
    assert tool(client, "start_build", card_id=cid, user_confirmed=True)["would_go_to"] == ["GitHub", "Webhook"]
    r = client.post(f"/api/cards/{cid}/build", headers=AS_O).json()
    assert [x["ok"] for x in r["sent"]] == [True, False]
    card = tool(client, "get_card", card_id=cid)
    assert card["sent_to"] == [{"where": "Issue #1", "url": "https://github.com/acme/app/issues/1"}] and card["not_yet_sent_to"] == ["Webhook"]
