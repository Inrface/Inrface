import json
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

OWNER, PARTNER, OUTSIDER = "owner@example.com", "partner@example.com", "quinn@example.com"

CONFIG = """
[people.olly]
email = "owner@example.com"
name = "Olly"
[people.pat]
email = "Partner@Example.com"
name = "Pat"
[people.quinn]
email = "quinn@example.com"
name = "Quinn"

[projects.main]
name = "Main"
givers = ["pat"]
receivers = ["olly"]
repo = "acme/app"
github_token_env = "TEST_GH_TOKEN"

[projects.side]
name = "Side"
givers = []
receivers = ["quinn"]
"""


def make_client(tmp_path, monkeypatch, config=CONFIG, **env):
    cfg = tmp_path / "innerface.toml"
    cfg.write_text(config)
    base = dict(AUTH_MODE="dev", INNERFACE_CONFIG=str(cfg), AI_PROVIDER="fake", DATA_DIR=str(tmp_path), PUSH_CONTACT="a@example.com", TEST_GH_TOKEN="ghtoken", GITHUB_POLL_SECONDS="0")
    base.update(env)
    for k in ("CF_ACCESS_AUD", "CF_ACCESS_TEAM", "DEV_USER"):
        monkeypatch.delenv(k, raising=False)
    for k, v in base.items():
        monkeypatch.setenv(k, v)
    from innerface import config
    config.get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from innerface.main import app
    return TestClient(app)


@pytest.fixture
def client(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as c:
        yield c


@pytest.fixture
def pushes(monkeypatch):
    sent = []
    from innerface import push, service
    monkeypatch.setattr(service, "_submit", lambda fn, *a: fn(*a))
    monkeypatch.setattr(push, "send", lambda s, email, title, body, card_id=None, ask=None: sent.append((email, title, body, card_id) + ((ask,) if ask else ())))
    return sent


@pytest.fixture
def gh(monkeypatch):
    """A stand-in for GitHub that records issues and lets tests set their progress."""
    from innerface import github
    state = {"issues": [], "progress": {"state": "open", "state_reason": None, "pr_url": "", "pr_merged": False}}

    def create_issue(s, project, title, body):
        state["issues"].append({"repo": project.repo, "title": title, "body": body, "label": project.build_label})
        n = len(state["issues"])
        return n, f"https://github.com/{project.repo}/issues/{n}"

    monkeypatch.setattr(github, "create_issue", create_issue)
    monkeypatch.setattr(github, "issue_progress", lambda s, project, number: dict(state["progress"]))
    return state


H = {"X-Requested-With": "Inrface"}
AS_P = {**H, "X-Dev-User": PARTNER}
AS_O = {**H, "X-Dev-User": OWNER}
AS_Q = {**H, "X-Dev-User": OUTSIDER}


def dump(c, text="An idea", who=AS_P, **kw):
    r = c.post("/api/cards/dump", json={"text": text, **kw}, headers=who)
    assert r.status_code == 200, r.text
    return r.json()["ids"]


def scope(c, cid, **fields):
    r = c.put(f"/api/cards/{cid}/scope", json=fields or {"summary": "Weekly report", "scope": "Email only", "acceptance": ["Sends on Monday"]}, headers=AS_O)
    assert r.status_code == 200, r.text
    return r.json()


# ----- basics -----

def test_mutations_need_the_custom_header(client):
    assert client.post("/api/cards/dump", json={"text": "x"}).status_code == 403


def test_security_headers(client):
    r = client.get("/api/me", headers=AS_O)
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


def test_me_lists_projects_and_roles(client):
    me = client.get("/api/me", headers=AS_P).json()
    assert [(p["id"], p["role"]) for p in me["projects"]] == [("main", "giver")]
    assert me["projects"][0]["receivers"] == ["Olly"]
    assert client.get("/api/me", headers={"X-Dev-User": "stranger@example.com"}).status_code == 403


def test_config_rejects_unknown_people_and_bad_repos(tmp_path, monkeypatch):
    from innerface import config
    for bad in (CONFIG.replace('receivers = ["olly"]', 'receivers = ["nobody"]'), CONFIG.replace("acme/app", "not a repo")):
        cfg = tmp_path / "bad.toml"
        cfg.write_text(bad)
        monkeypatch.setenv("INNERFACE_CONFIG", str(cfg))
        monkeypatch.setenv("AUTH_MODE", "dev")
        config.get_settings.cache_clear()
        with pytest.raises(config.ConfigError):
            config.get_settings()
    config.get_settings.cache_clear()


# ----- giving ideas -----

def test_brain_dump_splits_and_alerts_receivers(client, pushes):
    ids = dump(client, "first idea ||| second idea")
    assert len(ids) == 2
    cards = client.get("/api/cards", headers=AS_O).json()["cards"]
    assert {x["status"] for x in cards} == {"New"} and {x["project_id"] for x in cards} == {"main"}
    assert pushes[0][0] == OWNER


def test_without_ai_ideas_save_as_written(tmp_path, monkeypatch, pushes):
    with make_client(tmp_path, monkeypatch, AI_PROVIDER="none") as c:
        assert c.get("/api/me", headers=AS_P).json()["ai"] is False
        ids = dump(c, "one ||| two")
        assert len(ids) == 1
        assert c.post("/api/chat", json={"text": "hi"}, headers=AS_P).status_code == 503


def test_ai_failure_still_saves_the_idea(client, monkeypatch, pushes):
    from innerface import ai
    def boom(*a):
        raise ai.AIError("down")
    monkeypatch.setattr(ai, "extract_cards", boom)
    ids = dump(client, "keep me safe")
    card = client.get(f"/api/cards/{ids[0]}", headers=AS_O).json()
    assert card["original_text"] == "keep me safe" and card["title"] == "keep me safe"


def test_projects_are_walled_off(client, pushes):
    cid = dump(client)[0]
    assert client.get(f"/api/cards/{cid}", headers=AS_Q).status_code == 404
    assert client.get("/api/cards", headers=AS_Q).json()["cards"] == []
    assert client.post("/api/cards/dump", json={"text": "x", "project_id": "side"}, headers=AS_P).status_code == 404
    assert client.post(f"/api/cards/{cid}/comments", json={"text": "hi"}, headers=AS_Q).status_code == 404


def test_givers_cannot_triage_scope_or_build(client, pushes, gh):
    cid = dump(client)[0]
    assert client.patch(f"/api/cards/{cid}", json={"status": "Rejected"}, headers=AS_P).status_code == 403
    assert client.put(f"/api/cards/{cid}/scope", json={"summary": "x"}, headers=AS_P).status_code == 403
    assert client.post(f"/api/cards/{cid}/build", headers=AS_P).status_code == 403
    assert client.post(f"/api/cards/{cid}/merge", json={"into": cid}, headers=AS_P).status_code == 403
    assert client.get(f"/api/cards/{cid}/spec", headers=AS_P).status_code == 403
    assert client.post(f"/api/cards/{cid}/draft-question", headers=AS_P).status_code == 403
    assert gh["issues"] == []


# ----- the review loop -----

def test_send_back_and_answer_round_trip(client, pushes):
    cid = dump(client)[0]
    assert client.patch(f"/api/cards/{cid}", json={"status": "Needs info"}, headers=AS_O).status_code == 422
    assert client.patch(f"/api/cards/{cid}", json={"status": "Needs info", "message": "Why?"}, headers=AS_O).status_code == 200
    card = client.get(f"/api/cards/{cid}", headers=AS_P).json()
    assert card["status"] == "Needs info" and card["note"] == "Why?"
    assert pushes[-1][0] == PARTNER
    assert client.post(f"/api/cards/{cid}/comments", json={"text": "Because"}, headers=AS_P).json()["status"] == "Answered"
    assert pushes[-1][0] == OWNER
    assert [x["type"] for x in client.get(f"/api/cards/{cid}", headers=AS_O).json()["comments"]] == ["clarification", "reply"]


def test_reaction_does_not_count_as_answer(client, pushes):
    cid = dump(client)[0]
    client.patch(f"/api/cards/{cid}", json={"status": "Needs info", "message": "Why?"}, headers=AS_O)
    client.post(f"/api/cards/{cid}/comments", json={"kind": "dislike"}, headers=AS_P)
    assert client.get(f"/api/cards/{cid}", headers=AS_P).json()["status"] == "Needs info"


def test_note_cleared_when_leaving_needs_info(client, pushes):
    cid = dump(client)[0]
    client.patch(f"/api/cards/{cid}", json={"status": "Needs info", "message": "Why?"}, headers=AS_O)
    client.patch(f"/api/cards/{cid}", json={"status": "Rejected"}, headers=AS_O)
    assert client.get(f"/api/cards/{cid}", headers=AS_O).json()["note"] == ""


def test_chat_then_save_keeps_transcript(client, pushes):
    assert client.post("/api/chat", json={"text": "Big idea"}, headers=AS_P).status_code == 200
    assert len(client.get("/api/chat", headers=AS_P).json()["messages"]) == 2
    # The other person can't see a conversation in progress.
    assert client.get("/api/chat?project_id=main", headers=AS_O).json()["messages"] == []
    ids = client.post("/api/chat/save", json={}, headers=AS_P).json()["ids"]
    card = client.get(f"/api/cards/{ids[0]}", headers=AS_O).json()
    assert len(card["transcript"]) == 2 and card["source"] == "chat"
    assert client.get("/api/chat", headers=AS_P).json()["messages"] == []


def test_chat_ai_failure_loses_nothing_saved(client, monkeypatch):
    from innerface import ai
    def boom(*a):
        raise ai.AIError("Couldn't reach it")
    monkeypatch.setattr(ai, "chat", boom)
    r = client.post("/api/chat", json={"text": "hello"}, headers=AS_P)
    assert r.status_code == 503 and "Couldn't" in r.json()["detail"]
    assert client.get("/api/chat", headers=AS_P).json()["messages"] == []


def test_card_conversation_answers_needs_info(client, pushes):
    cid = dump(client)[0]
    client.patch(f"/api/cards/{cid}", json={"status": "Needs info", "message": "Why?"}, headers=AS_O)
    client.post("/api/chat", json={"text": "Because of X", "card_id": cid}, headers=AS_P)
    assert client.post("/api/chat/save", json={"card_id": cid}, headers=AS_P).status_code == 200
    card = client.get(f"/api/cards/{cid}", headers=AS_O).json()
    assert card["status"] == "Answered" and len(card["transcript"]) == 2


def test_receiver_review_chat_stays_private(client, pushes):
    cid = dump(client)[0]
    client.post("/api/chat", json={"text": "Worth doing?", "card_id": cid}, headers=AS_O)
    assert client.get(f"/api/chat?card_id={cid}", headers=AS_P).json()["messages"] == []
    assert client.post("/api/chat/save", json={"card_id": cid}, headers=AS_O).status_code == 403


def test_merge_hides_source_and_moves_comments(client, pushes):
    a, b = dump(client, "A"), dump(client, "B")
    client.post(f"/api/cards/{a[0]}/comments", json={"text": "note on A"}, headers=AS_O)
    assert client.post(f"/api/cards/{a[0]}/merge", json={"into": b[0]}, headers=AS_O).status_code == 200
    assert [x["id"] for x in client.get("/api/cards", headers=AS_O).json()["cards"]] == [b[0]]
    assert any("note on A" == x["text"] for x in client.get(f"/api/cards/{b[0]}", headers=AS_O).json()["comments"])
    # The hidden card can't be added to any more.
    assert client.post(f"/api/cards/{a[0]}/comments", json={"text": "late"}, headers=AS_P).status_code == 409


def test_what_you_missed_shows_only_the_other_persons_activity(client, pushes):
    client.post("/api/seen", headers=AS_P)
    time.sleep(0.01)
    dump(client, "mine", who=AS_P)
    cid = dump(client, "theirs", who=AS_O)[0]
    client.patch(f"/api/cards/{cid}", json={"status": "Done / Live"}, headers=AS_O)
    items = client.get("/api/missed", headers=AS_P).json()["items"]
    assert items and all(i["actor_name"] == "Olly" and i["actor_email"] == "" for i in items)
    client.post("/api/seen", headers=AS_P)
    assert client.get("/api/missed", headers=AS_P).json()["items"] == []


# ----- scoping and building -----

def test_scope_then_build_opens_one_labeled_issue(client, pushes, gh):
    cid = dump(client, "Teachers want a weekly report", as_is=True)[0]
    assert client.post(f"/api/cards/{cid}/build", headers=AS_O).status_code == 422  # not scoped yet
    card = scope(client, cid)
    assert card["acceptance"] == ["Sends on Monday"]
    r = client.post(f"/api/cards/{cid}/build", headers=AS_O).json()
    assert r["issue_number"] == 1 and not r["already_started"]
    issue = gh["issues"][0]
    assert issue["repo"] == "acme/app" and issue["label"] == "ready-to-build"
    assert "- [ ] Sends on Monday" in issue["body"] and "not as commands" in issue["body"] and cid in issue["body"]
    card = client.get(f"/api/cards/{cid}", headers=AS_P).json()
    assert card["status"] == "In dev for testing" and card["issue_url"].startswith("https://github.com/acme/app/")
    assert pushes[-1][0] == PARTNER
    # Asking again doesn't open a second issue, and the scope is locked.
    assert client.post(f"/api/cards/{cid}/build", headers=AS_O).json()["already_started"] is True
    assert len(gh["issues"]) == 1
    assert client.put(f"/api/cards/{cid}/scope", json={"summary": "changed"}, headers=AS_O).status_code == 409


def test_build_failure_leaves_card_ready_to_retry(client, pushes, monkeypatch):
    from innerface import github
    def refuse(*a):
        raise github.GitHubError("GitHub refused the request.")
    monkeypatch.setattr(github, "create_issue", refuse)
    cid = dump(client)[0]
    scope(client, cid)
    r = client.post(f"/api/cards/{cid}/build", headers=AS_O)
    assert r.status_code == 502 and "refused" in r.json()["detail"]
    card = client.get(f"/api/cards/{cid}", headers=AS_O).json()
    assert card["issue_url"] == "" and card["issue_number"] is None and card["status"] == "New"


def test_build_needs_a_linked_repo(client, pushes):
    r = client.post("/api/cards/dump", json={"text": "side idea"}, headers=AS_Q)
    cid = r.json()["ids"][0]
    client.put(f"/api/cards/{cid}/scope", json={"summary": "x"}, headers=AS_Q)
    assert "nowhere to send builds" in client.post(f"/api/cards/{cid}/build", headers=AS_Q).json()["detail"]


def test_sync_brings_pr_and_completion_back(client, pushes, gh):
    from innerface import config, service
    cid = dump(client)[0]
    scope(client, cid)
    client.post(f"/api/cards/{cid}/build", headers=AS_O)
    s = config.get_settings()
    assert service.sync_builds(s) == 0
    gh["progress"].update(pr_url="https://github.com/acme/app/pull/7")
    assert service.sync_builds(s) == 1
    assert client.get(f"/api/cards/{cid}", headers=AS_P).json()["pr_url"].endswith("/pull/7")
    gh["progress"].update(state="closed", pr_merged=True)
    service.sync_builds(s)
    assert client.get(f"/api/cards/{cid}", headers=AS_P).json()["status"] == "Done / Live"
    assert pushes[-1][1] == "Done"


def test_github_progress_ignores_prs_from_other_repos(monkeypatch):
    from innerface import config, github
    project = config.Project(id="p", name="P", givers=(), receivers=("a@x",), repo="acme/app", github_token_env="T")
    monkeypatch.setenv("T", "tok")

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, path, params=None):
            import httpx
            if path.endswith("/timeline"):
                data = [{"event": "cross-referenced", "source": {"issue": {"html_url": "https://github.com/evil/repo/pull/1", "pull_request": {"merged_at": "x"}, "repository": {"full_name": "evil/repo"}}}}]
            else:
                data = {"state": "open", "state_reason": None}
            return httpx.Response(200, json=data)

    monkeypatch.setattr(github.httpx, "Client", FakeClient)
    s = type("S", (), {"github_api": "https://api.github.com"})()
    assert github.issue_progress(s, project, 1) == {"state": "open", "state_reason": None, "pr_url": "", "pr_merged": False, "conflicts": None}


def test_the_runners_notes_about_conflicts_are_read_from_the_timeline_and_the_last_one_counts(monkeypatch):
    from innerface import config, github
    project = config.Project(id="p", name="P", givers=(), receivers=("a@x",), repo="acme/app", github_token_env="T")
    monkeypatch.setenv("T", "tok")
    events = [{"event": "commented", "id": 1, "body": "Build started"},
              {"event": "commented", "id": 2, "body": "Sending the agent back.\n\n<!-- inrface-runner:conflicts:fixing -->"},
              {"event": "commented", "id": 3, "body": "Fixed.\n\n<!-- inrface-runner:conflicts:fixed -->"},
              {"event": "labeled", "id": 4, "body": "<!-- inrface-runner:conflicts:stuck -->"}]

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, path, params=None):
            import httpx
            return httpx.Response(200, json=events if path.endswith("/timeline") else {"state": "open", "state_reason": None})

    monkeypatch.setattr(github.httpx, "Client", FakeClient)
    s = type("S", (), {"github_api": "https://api.github.com"})()
    assert github.issue_progress(s, project, 1)["conflicts"] == {"id": "3", "kind": "fixed"}


# ----- MCP -----

MCP_H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "MCP-Protocol-Version": "2025-06-18"}


def mcp(c, method, params=None, who=OWNER, raw=False):
    r = c.post("/mcp", headers={**MCP_H, "X-Dev-User": who}, content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}))
    if raw:
        return r
    assert r.status_code == 200, r.text
    return r.json()


def tool(c, name, who=OWNER, given=None, **args):
    """`given` is for arguments whose names clash with this helper's own, such as "who"."""
    res = mcp(c, "tools/call", {"name": name, "arguments": {**(given or {}), **args}}, who=who)["result"]
    text = res["content"][0]["text"] if res.get("content") else ""
    if res.get("isError"):
        return {"error": text}
    return res.get("structuredContent") or json.loads(text)


def test_mcp_lists_tools(client):
    names = {t["name"] for t in mcp(client, "tools/list")["result"]["tools"]}
    assert {"list_cards", "get_card", "ask_question", "file_card", "update_scope", "start_build", "submit_idea"} <= names


def test_mcp_full_review_loop(client, pushes, gh):
    cid = dump(client, "A dashboard for teachers")[0]
    cards = tool(client, "list_cards", waiting="review")["cards"]
    assert [c["id"] for c in cards] == [cid]
    assert tool(client, "get_card", card_id=cid)["original_text"] == "A dashboard for teachers"
    tool(client, "ask_question", card_id=cid, question="Which numbers matter?")
    assert pushes[-1][0] == PARTNER
    assert tool(client, "list_cards", waiting="answer")["count"] == 1
    tool(client, "comment", who=PARTNER, card_id=cid, text="Attendance")
    assert tool(client, "list_cards", waiting="review")["cards"][0]["status"] == "Answered"
    tool(client, "update_scope", card_id=cid, summary="Weekly attendance dashboard", acceptance=["Shows last 4 weeks"], open_questions=[])
    assert "error" in tool(client, "start_build", card_id=cid, user_confirmed=False)
    r = tool(client, "start_build", card_id=cid, user_confirmed=True)
    assert r["started"] is False and r["waiting_for_approval"] and not gh["issues"]
    r = client.post(f"/api/cards/{cid}/build", headers=AS_O).json()
    assert r["issue_number"] == 1 and len(gh["issues"]) == 1


def test_mcp_file_card_and_roles(client, pushes):
    cid = dump(client)[0]
    assert "error" in tool(client, "file_card", who=PARTNER, card_id=cid, where="rejected")
    assert "error" in tool(client, "get_card", who=OUTSIDER, card_id=cid)
    assert tool(client, "file_card", card_id=cid, where="near-term", note="After launch")["status"] == "6-month roadmap"
    card = client.get(f"/api/cards/{cid}", headers=AS_P).json()
    assert card["status"] == "6-month roadmap" and card["note"] == "After launch"


def test_mcp_submit_idea_from_a_giver(client, pushes):
    r = tool(client, "submit_idea", who=PARTNER, text="Dark mode please")
    assert client.get(f"/api/cards/{r['id']}", headers=AS_O).json()["title"] == "Dark mode please"
    assert r["sent_to"] == ["Olly"] and r["saved_write_up"] == []


def test_mcp_tells_each_role_how_to_help(client):
    init = mcp(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})["result"]
    assert "list_projects first" in init["instructions"]
    giver = tool(client, "list_projects", who=PARTNER)
    assert giver["you_are_helping"] == "Pat" and list(giver["how_to_help"]) == ["giver"]
    assert "one question at a time" in giver["how_to_help"]["giver"]
    assert giver["projects"][0]["role"] == "giver" and giver["projects"][0]["reviewed_by"] == ["Olly"]
    receiver = tool(client, "list_projects")
    assert set(receiver["how_to_help"]) == {"receiver", "bringing_an_idea"} and "start_build" in receiver["how_to_help"]["receiver"]


def test_mcp_a_giver_sends_an_idea_already_scoped(client, pushes):
    idea = {"text": "I want the weekly numbers emailed to me so I stop logging in to check", "title": "Weekly numbers by email",
            "summary": "Email the weekly numbers every Monday.", "who": "  The two owners ", "problem": "Checking means logging in.",
            "scope": "In: one email a week. Out: charts.", "open_questions": ["Which numbers?", " "]}
    r = tool(client, "submit_idea", who=PARTNER, given=idea)
    assert r["title"] == "Weekly numbers by email" and r["saved_write_up"] == ["open_questions", "problem", "scope", "summary", "who"]
    assert pushes[-1][0] == OWNER and "Weekly numbers by email" in pushes[-1][2]
    card = client.get(f"/api/cards/{r['id']}", headers=AS_O).json()
    assert card["source"] == "mcp" and card["author_name"] == "Pat" and card["status"] == "New"
    assert card["summary"] == "Email the weekly numbers every Monday." and card["who"] == "The two owners" and card["scope"].startswith("In: one email")
    assert card["open_questions"] == ["Which numbers?"] and card["original_text"].startswith("I want the weekly numbers")
    # What "done" means stays the receiver's to write.
    assert card["acceptance"] == []
    listed = tool(client, "list_cards", who=PARTNER, waiting="all")["cards"]
    assert listed[0]["yours"] and "yours" not in tool(client, "list_cards", waiting="all")["cards"][0]


def test_mcp_refuses_strangers_and_cross_site_posts(client):
    assert mcp(client, "tools/list", who="stranger@example.com", raw=True).status_code == 403
    # A cross-site form can only send simple content types, which the MCP endpoint must not act on.
    r = client.post("/mcp", headers={"Content-Type": "text/plain", "Accept": "application/json, text/event-stream", "X-Dev-User": OWNER},
                    content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "file_card", "arguments": {"card_id": "x", "where": "rejected"}}}))
    assert r.status_code >= 400


# ----- push -----

def test_push_subscribe_rejects_other_hosts(client):
    keys = {"p256dh": "x", "auth": "y"}
    assert client.post("/api/push/subscribe", json={"endpoint": "http://169.254.169.254/x", **keys}, headers=AS_O).status_code == 422
    assert client.post("/api/push/subscribe", json={"endpoint": "https://evil.example.com/x", **keys}, headers=AS_O).status_code == 422
    assert client.post("/api/push/subscribe", json={"endpoint": "https://web.push.apple.com/abc", **keys}, headers=AS_O).status_code == 200


def test_push_sends_real_vapid_and_drops_dead_subscriptions(client, monkeypatch):
    from innerface import config, push
    s = config.get_settings()
    client.post("/api/push/subscribe", json={"endpoint": "https://web.push.apple.com/abc", "p256dh": "x", "auth": "y"}, headers=AS_O)
    calls = []
    class Resp:
        status_code = 410
    def fake_webpush(**kw):
        calls.append(kw)
        kw["vapid_private_key"].sign({"aud": "https://web.push.apple.com", "sub": "mailto:a@example.com", "exp": int(time.time()) + 600})
        raise push.WebPushException("gone", response=Resp())
    monkeypatch.setattr(push, "webpush", fake_webpush)
    push.send(s, OWNER, "T", "B", "card1")
    assert json.loads(calls[0]["data"])["cardId"] == "card1" and json.loads(calls[0]["data"])["ask"] is None
    client.post("/api/push/subscribe", json={"endpoint": "https://web.push.apple.com/abc", "p256dh": "x", "auth": "y"}, headers=AS_O)
    push.send(s, OWNER, "T", "B", "card1", "build")
    assert json.loads(calls[-1]["data"])["ask"] == "build"
    from innerface import db
    with db.connect(s) as c:
        assert c.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0] == 0


def test_giver_rules_name_the_project_and_reviewers(client):
    from innerface import ai, config
    s = config.get_settings()
    rules = ai.giver_rules(s, s.projects["main"], "Pat")
    assert "Pat" in rules and "Olly" in rules and "Main" in rules


# ----- real login mode -----

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(key=KEY, aud="the-aud", iss="https://team.cloudflareaccess.com", email=OWNER, exp=None):
    now = int(time.time())
    return pyjwt.encode({"email": email, "aud": aud, "iss": iss, "iat": now, "exp": exp or now + 300}, key, algorithm="RS256")


@pytest.fixture
def access(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch, AUTH_MODE="access", CF_ACCESS_TEAM="team", CF_ACCESS_AUD="the-aud") as c:
        from innerface import auth
        monkeypatch.setattr(auth, "_signing_key", lambda t, team: KEY.public_key())
        yield c


def me(c, tok=None, **headers):
    h = dict(headers)
    if tok:
        h["Cf-Access-Jwt-Assertion"] = tok
    return c.get("/api/me", headers=h).status_code


def test_access_valid_tokens_for_configured_people(access):
    assert me(access, token()) == 200
    assert me(access, token(email=PARTNER.upper())) == 200


def test_access_rejects_bad_tokens(access):
    assert me(access) == 401
    assert me(access, "garbage") == 401
    assert me(access, token(key=OTHER_KEY)) == 401
    assert me(access, token(aud="another-app")) == 401
    assert me(access, token(iss="https://evil.cloudflareaccess.com")) == 401
    assert me(access, token(exp=int(time.time()) - 10)) == 401


def test_access_rejects_alg_none(access):
    forged = pyjwt.encode({"email": OWNER, "aud": "the-aud", "iss": "https://team.cloudflareaccess.com", "iat": 1, "exp": int(time.time()) + 300}, None, algorithm="none")
    assert me(access, forged) == 401


def test_access_rejects_other_emails_and_plain_headers(access):
    assert me(access, token(email="stranger@example.com")) == 403
    # The unsigned email header and dev header mean nothing here.
    assert me(access, **{"Cf-Access-Authenticated-User-Email": OWNER, "X-Dev-User": OWNER}) == 401


def test_refused_logins_say_why_in_the_log(access, caplog):
    from innerface import auth
    auth._said.clear()
    with caplog.at_level("WARNING", logger="innerface"):
        me(access)
        me(access, token(aud="0123456789abcdef"))
        me(access, token(email="stranger@example.com"))
        me(access, token(email="stranger@example.com"))
        me(access, "garbage")
    said = [r.getMessage() for r in caplog.records if "login refused" in r.getMessage()]
    assert len(said) == 4  # the repeat is not written again
    assert "no Cloudflare Access token" in said[0]
    assert "01234567" in said[1] and "0123456789abcdef" not in said[1] and "the-aud"[:8] in said[1]
    assert "stranger@example.com" in said[2] and "not listed" in said[2]
    assert "garbage" not in said[3] and "not valid" in said[3]


def test_access_guards_mcp_too(access):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert access.post("/mcp", headers={**MCP_H, "X-Dev-User": OWNER}, content=body).status_code == 401
    r = access.post("/mcp", headers={**MCP_H, "Cf-Access-Jwt-Assertion": token()}, content=body)
    assert r.status_code == 200 and r.json()["result"]["tools"]


def test_config_refuses_dev_mode_with_access_settings(tmp_path, monkeypatch):
    from innerface import config
    cfg = tmp_path / "c.toml"
    cfg.write_text(CONFIG)
    for k, v in dict(AUTH_MODE="dev", INNERFACE_CONFIG=str(cfg), CF_ACCESS_AUD="x").items():
        monkeypatch.setenv(k, v)
    config.get_settings.cache_clear()
    with pytest.raises(config.ConfigError):
        config.get_settings()
    config.get_settings.cache_clear()


def test_healthz_needs_no_login(access):
    assert access.get("/healthz").text == "ok"


# ----- GitHub settings screen -----

def test_github_settings_are_receiver_only_and_hide_the_token(client):
    r = client.get("/api/projects/main/github", headers=AS_O)
    assert r.status_code == 200, r.text
    assert r.json()["token_source"] == "server" and r.json()["token_hint"] == ""  # nothing of the server's token is shown
    assert "ghtoken" not in r.text
    assert client.get("/api/projects/main/github", headers=AS_P).status_code == 404
    assert client.put("/api/projects/main/github", json={"repo": "x/y"}, headers=AS_P).status_code == 404
    assert client.put("/api/projects/main/github", json={"repo": "x/y"}, headers=AS_Q).status_code == 404

    r = client.put("/api/projects/main/github", json={"repo": "https://github.com/acme/other.git", "token": "github_pat_" + "a" * 30 + "wxyz"}, headers=AS_O)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["repo"] == "acme/other" and d["token_source"] == "app" and d["token_hint"] == "wxyz"
    assert "github_pat_" not in r.text
    for bad in ({"repo": "not a repo"}, {"token": "short"}, {"build_label": "a,b"}, {}):
        assert client.put("/api/projects/main/github", json=bad, headers=AS_O).status_code == 422
    assert client.put("/api/projects/main/github", json={"repo": "x/y"}).status_code == 403  # no CSRF header

    # The server's own token was set up for acme/app. It is never lent to a repo picked in Settings.
    r = client.put("/api/projects/main/github", json={"clear_token": True}, headers=AS_O)
    assert r.json()["repo"] == "acme/other" and r.json()["token_set"] is False and r.json()["token_source"] == ""
    r = client.put("/api/projects/main/github", json={"repo": "acme/third"}, headers=AS_O)
    assert r.status_code == 422 and "save a token for it" in r.json()["detail"]
    r = client.put("/api/projects/main/github", json={"repo": "ACME/app"}, headers=AS_O)
    assert r.status_code == 200 and r.json()["token_source"] == "server"
    for dots in ("../app", "acme/..", "./app", "acme/."):
        assert client.put("/api/projects/main/github", json={"repo": dots}, headers=AS_O).status_code == 422


def test_the_servers_token_is_not_used_on_a_repo_picked_in_settings(client, pushes, gh):
    from innerface import db, service
    from innerface.config import get_settings
    s = get_settings()
    # A row saved before this rule existed: another repo, and no token of its own.
    with db.connect(s) as c:
        c.execute("INSERT INTO project_github(project_id, repo) VALUES ('main', 'acme/elsewhere')")
    assert service.github_project(s, s.projects["main"]).github_token == ""
    [cid] = dump(client)
    scope(client, cid)
    client.post(f"/api/cards/{cid}/build", json={}, headers=AS_O)
    assert gh["issues"] == []


def test_saved_settings_drive_builds(client, gh):
    client.put("/api/projects/main/github", json={"repo": "acme/other", "build_label": "go", "token": "github_pat_" + "b" * 30}, headers=AS_O)
    [cid] = dump(client)
    scope(client, cid)
    assert client.post(f"/api/cards/{cid}/build", json={}, headers=AS_O).status_code == 200
    assert gh["issues"][0]["repo"] == "acme/other" and gh["issues"][0]["label"] == "go"
    from innerface import service
    from innerface.config import get_settings
    s = get_settings()
    assert service.github_project(s, s.projects["main"]).github_token == "github_pat_" + "b" * 30


def test_github_check_reports_errors(client, monkeypatch):
    from innerface import github
    monkeypatch.setattr(github, "check", lambda s, p: {"repo": p.repo, "label": "created"})
    assert client.post("/api/projects/main/github/check", json={}, headers=AS_O).json() == {"repo": "acme/app", "label": "created"}

    def boom(s, p):
        raise github.GitHubError("GitHub refused the request.")
    monkeypatch.setattr(github, "check", boom)
    r = client.post("/api/projects/main/github/check", json={}, headers=AS_O)
    assert r.status_code == 502 and "refused" in r.text
    assert client.post("/api/projects/main/github/check", json={}, headers=AS_P).status_code == 404


# ----- intake API -----

def make_source(c, name="Website"):
    r = c.post("/api/projects/main/sources", json={"name": name}, headers=AS_O)
    assert r.status_code == 200, r.text
    return r.json()


def K(src):
    return {"Authorization": "Bearer " + src["key"]}


def test_sources_are_receiver_only_and_key_shown_once(client):
    assert client.post("/api/projects/main/sources", json={"name": "x"}, headers=AS_P).status_code == 404
    src = make_source(client)
    assert src["key"].startswith("inr_") and src["key_hint"] == src["key"][-4:]
    listed = client.get("/api/projects/main/sources", headers=AS_O).json()["sources"]
    assert len(listed) == 1 and "key" not in listed[0] and src["key"] not in json.dumps(listed)
    assert client.get("/api/projects/main/sources", headers=AS_P).status_code == 404


def test_intake_idea_creates_card_scrubs_and_is_idempotent(client, pushes):
    src = make_source(client)
    body = {"external_id": "fe_1", "text": "Email me at t.teacher@school.sa or call +966 50 123 4567, see https://x.example/secret?token=abc. Add Arabic export.",
            "tool": "quiz", "author_ref": "h" * 64, "url": "https://admin.example/f?id=1", "language": "ar"}
    r = client.post("/api/intake/v1/ideas", json=body, headers=K(src))
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] == "received" and d["redactions"] == 3 and d["flagged"] and not d["duplicate"]
    card = client.get(f"/api/cards/{d['card_id']}", headers=AS_O).json()
    assert "t.teacher" not in card["original_text"] and "123 4567" not in card["original_text"] and "token=abc" not in card["original_text"]
    assert "[email]" in card["original_text"] and "[link: x.example]" in card["original_text"]
    assert card["external"] and card["author_name"] == "Website" and card["tool"] == "quiz" and card["flagged"]
    assert any("Website" in p[1] for p in pushes)
    again = client.post("/api/intake/v1/ideas", json=body, headers=K(src))
    assert again.status_code == 200 and again.json()["duplicate"] and again.json()["card_id"] == d["card_id"]


def test_intake_rejects_unknown_fields_without_echoing(client):
    src = make_source(client)
    r = client.post("/api/intake/v1/ideas", json={"external_id": "x", "text": "hi", "email": "secret@example.com"}, headers=K(src))
    assert r.status_code == 422 and "email" in r.json()["detail"] and "secret@example.com" not in r.text
    r = client.post("/api/intake/v1/ideas", json={"external_id": "bad id!", "text": "private words"}, headers=K(src))
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "external_id" and "private words" not in r.text
    assert client.post("/api/intake/v1/ideas", json={"external_id": "y"}, headers=K(src)).status_code == 422
    r = client.post("/api/intake/v1/ideas", json={"external_id": "y", "url": "https://admin.example/1"}, headers=K(src))
    assert r.status_code == 201  # link-only


def test_intake_keys(client):
    src = make_source(client)
    ok = {"external_id": "a", "text": "hi"}
    assert client.post("/api/intake/v1/ideas", json=ok).status_code == 401
    assert client.post("/api/intake/v1/ideas", json=ok, headers={"Authorization": "Bearer inr_00000000." + "x" * 30}).status_code == 401
    bad_secret = src["key"].split(".")[0] + "." + "y" * 43
    assert client.post("/api/intake/v1/ideas", json=ok, headers={"Authorization": "Bearer " + bad_secret}).status_code == 401
    assert client.post(f"/api/projects/main/sources/{src['id']}/revoke", json={}, headers=AS_P).status_code == 404
    assert client.post(f"/api/projects/main/sources/{src['id']}/revoke", json={}, headers=AS_O).json()["revoked"]
    assert client.post("/api/intake/v1/ideas", json=ok, headers=K(src)).status_code == 403


def test_intake_rate_limit(client, monkeypatch):
    from innerface import intake
    monkeypatch.setattr(intake, "RATE_PER_MINUTE", 2)
    src = make_source(client)
    codes = [client.get("/api/intake/v1/ideas", headers=K(src)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_intake_status_sync_and_merge_and_external_questions(client):
    src = make_source(client)
    a = client.post("/api/intake/v1/ideas", json={"external_id": "a", "text": "Idea A"}, headers=K(src)).json()
    b = client.post("/api/intake/v1/ideas", json={"external_id": "b", "text": "Idea B"}, headers=K(src)).json()
    first = client.get("/api/intake/v1/ideas", headers=K(src)).json()
    assert [i["external_id"] for i in first["ideas"]] == ["a", "b"]
    since = first["next_since"]
    assert client.get("/api/intake/v1/ideas", params={"updated_since": since}, headers=K(src)).json()["ideas"] == []

    r = client.patch(f"/api/cards/{a['card_id']}", json={"status": "Needs info", "message": "Which grade?"}, headers=AS_O)
    assert r.status_code == 422 and "no one here to answer" in r.text
    time.sleep(0.01)
    assert client.patch(f"/api/cards/{a['card_id']}", json={"status": "6-month roadmap"}, headers=AS_O).status_code == 200
    changed = client.get("/api/intake/v1/ideas", params={"updated_since": since}, headers=K(src)).json()["ideas"]
    assert [(i["external_id"], i["status"]) for i in changed] == [("a", "planned")]

    # b merged into a follows a's status
    assert client.post(f"/api/cards/{b['card_id']}/merge", json={"into": a["card_id"]}, headers=AS_O).status_code == 200
    assert client.get("/api/intake/v1/ideas/b", headers=K(src)).json()["status"] == "planned"
    assert client.get("/api/intake/v1/ideas/nope", headers=K(src)).status_code == 404

    other = make_source(client, "Other")
    assert client.get("/api/intake/v1/ideas/a", headers=K(other)).status_code == 404


def test_intake_delete_erases_text(client):
    src = make_source(client)
    d = client.post("/api/intake/v1/ideas", json={"external_id": "z", "text": "My student Sara needs this"}, headers=K(src)).json()
    client.post(f"/api/cards/{d['card_id']}/comments", json={"kind": "comment", "text": "Sara again"}, headers=AS_O)
    assert client.delete("/api/intake/v1/ideas/z", headers=K(src)).status_code == 204
    card = client.get(f"/api/cards/{d['card_id']}", headers=AS_O).json()
    assert "Sara" not in json.dumps(card) and card["status"] == "Rejected"
    assert client.get("/api/intake/v1/ideas/z", headers=K(src)).json()["status"] == "deleted"
    assert client.delete("/api/intake/v1/ideas/z", headers=K(src)).status_code == 204


def test_signals_and_tool_health(client):
    from datetime import date, timedelta
    src = make_source(client)
    today = date.today()
    sig = lambda d, pos, neg, **kw: {"tool": "quiz", "date": (today - timedelta(days=d)).isoformat(), "positive": pos, "negative": neg, **kw}
    r = client.post("/api/intake/v1/signals", json={"signals": [sig(1, 8, 2, rating_count=10, rating_sum=40, tool_label="Quiz"), sig(40, 9, 1)]}, headers=K(src))
    assert r.json() == {"accepted": 2}
    # resending a day replaces it
    client.post("/api/intake/v1/signals", json={"signals": [sig(1, 6, 4, rating_count=10, rating_sum=36, tool_label="Quiz")]}, headers=K(src))
    client.post("/api/intake/v1/ideas", json={"external_id": "q", "text": "More quiz types", "tool": "quiz"}, headers=K(src))
    tools = client.get("/api/projects/main/tools", headers=AS_O).json()["tools"]
    assert tools == [{"tool": "quiz", "label": "Quiz", "days": 30, "positive": 6, "negative": 4, "neutral": 0, "comments": 0,
                      "positive_share": 0.6, "previous_positive_share": 0.9, "average_rating": 3.6, "uses": None,
                      "failure_rate": None, "previous_failure_rate": None, "rerun_rate": None, "previous_rerun_rate": None, "ideas": 1}]
    assert client.get("/api/projects/main/tools", headers=AS_P).status_code == 404
    bad = client.post("/api/intake/v1/signals", json={"signals": [{"tool": "quiz", "date": "2026-13-40", "positive": 1, "negative": 0}]}, headers=K(src))
    assert bad.status_code == 422
    assert client.post("/api/intake/v1/signals", json={"signals": [{**sig(1, -1, 0)}]}, headers=K(src)).status_code == 422


def test_intake_needs_access_in_access_mode(access):
    from innerface.config import get_settings
    from innerface import intake
    s = get_settings()
    src = intake.create_source(s, {"email": OWNER, "projects": {"main": "receiver"}}, "main", "Website")
    body = {"external_id": "a", "text": "hi"}
    assert access.post("/api/intake/v1/ideas", json=body, headers=K(src)).status_code == 401
    svc = pyjwt.encode({"common_name": "abc.access", "aud": "the-aud", "iss": "https://team.cloudflareaccess.com", "iat": int(time.time()), "exp": int(time.time()) + 300},
                       KEY, algorithm="RS256")
    assert access.post("/api/intake/v1/ideas", json=body, headers={**K(src), "Cf-Access-Jwt-Assertion": svc}).status_code == 201
    # a service token alone is not a person: the web API still refuses it
    assert me(access, svc) == 401


def test_api_docs_page(client):
    r = client.get("/docs/intake-api", headers={"X-Dev-User": OWNER})
    assert r.status_code == 200 and "<h2" in r.text and 'id="send-an-idea"' in r.text


def test_signals_usage_counts(client):
    from datetime import date, timedelta
    src = make_source(client)
    day = lambda d: (date.today() - timedelta(days=d)).isoformat()
    post = lambda sigs: client.post("/api/intake/v1/signals", json={"signals": sigs}, headers=K(src))
    # usage only, no ratings
    assert post([{"tool": "quiz", "date": day(1), "uses": 100, "failures": 5, "reruns": 20},
                 {"tool": "quiz", "date": day(2), "uses": 100, "failures": None},
                 {"tool": "quiz", "date": day(40), "uses": 50, "failures": 1, "reruns": 5}]).status_code == 200
    [q] = client.get("/api/projects/main/tools", headers=AS_O).json()["tools"]
    assert q["uses"] == 200 and q["positive_share"] is None
    assert q["failure_rate"] == 0.05 and q["previous_failure_rate"] == 0.02  # day 2 reported no failures, so it's left out
    assert q["rerun_rate"] == 0.2 and q["previous_rerun_rate"] == 0.1
    # a resend replaces every field; left-out usage becomes null
    post([{"tool": "quiz", "date": day(1), "positive": 3}])
    [q] = client.get("/api/projects/main/tools", headers=AS_O).json()["tools"]
    assert q["uses"] == 100 and q["failure_rate"] is None and q["positive_share"] == 1.0
    for bad in ({"failures": 1}, {"uses": 3, "failures": 4}, {"uses": 3, "reruns": 4}, {"uses": -1}):
        r = post([{"tool": "quiz", "date": day(1), **bad}])
        assert r.status_code == 422, bad


def test_a_long_first_line_is_shortened_at_a_word():
    from innerface.service import fallback_title
    assert fallback_title("Dark mode\nIt's bright at night.") == "Dark mode"
    long = "Let teachers copy last year's lesson plans into this year, including the attachments and their notes"
    title = fallback_title(long)
    assert title == "Let teachers copy last year's lesson plans into this year, including…"
    assert len(fallback_title("x" * 200)) == 70


def test_the_apps_own_files_must_be_checked_each_time_so_an_update_shows(client):
    for path in ("/app.js", "/style.css", "/sw.js", "/"):
        assert client.get(path, headers=AS_O).headers["cache-control"] == "no-cache", path
    assert client.get("/api/me", headers=AS_O).headers["cache-control"] == "no-store"
    assert "cache-control" not in client.get("/icons/icon-192.png", headers=AS_O).headers
