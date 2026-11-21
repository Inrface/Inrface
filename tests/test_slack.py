"""Slack: ideas in from a channel, a command and a shortcut; filing and questions from the thread."""
import hashlib
import hmac
import itertools
import json
import time
from urllib.parse import urlencode

import httpx
import pytest

from test_app import AS_O, AS_P, AS_Q, H, client, dump, gh, make_client, pushes, scope  # noqa: F401
from test_connectors import BASE, add

SECRET = "5" * 32
TOKEN = "xoxb-" + "1" * 30
CHANNEL = "C0IDEAS0001"
OLLY, PAT, DANA = "U0OLLY", "U0PAT", "U0DANA"  # a receiver, a giver, and someone who isn't listed in Inrface


@pytest.fixture
def slack(monkeypatch, pushes):
    """A stand-in for Slack's Web API. Records what the app posts and answers like Slack would."""
    from innerface import slack as mod
    state = {
        "posts": [], "views": [], "responses": [], "calls": [], "fail": {}, "member": True,
        "users": {
            OLLY: {"real_name": "Olly O", "is_email_confirmed": True, "profile": {"email": "Owner@Example.com"}},
            PAT: {"real_name": "Pat P", "is_email_confirmed": True, "profile": {"email": "partner@example.com"}},
            DANA: {"real_name": "Dana", "is_email_confirmed": True, "profile": {"email": "dana@elsewhere.example"}},
        },
    }

    def post(url, **kw):
        if url.startswith("https://hooks.slack.com/"):
            state["responses"].append(kw["json"])
            return httpx.Response(200, json={"ok": True})
        method = url.rsplit("/", 1)[1]
        args = json.loads(kw["content"]) if "content" in kw else kw.get("data", {})
        state["calls"].append((method, args))
        if kw["headers"]["Authorization"] != f"Bearer {TOKEN}":
            return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
        if method in state["fail"]:
            return httpx.Response(200, json={"ok": False, "error": state["fail"][method]})
        if method == "auth.test":
            return httpx.Response(200, json={"ok": True, "team": "Acme", "user": "inrface"})
        if method == "conversations.info":
            return httpx.Response(200, json={"ok": True, "channel": {"id": args["channel"], "name": "ideas", "is_member": state["member"]}})
        if method == "users.info":
            u = state["users"].get(args["user"])
            return httpx.Response(200, json={"ok": True, "user": {"id": args["user"], **u}} if u else {"ok": False, "error": "user_not_found"})
        if method == "chat.postMessage":
            ts = f"1700000000.{len(state['posts']) + 1:06d}"
            state["posts"].append({**args, "ts": ts})
            return httpx.Response(200, json={"ok": True, "channel": args["channel"], "ts": ts})
        if method == "chat.getPermalink":
            return httpx.Response(200, json={"ok": True, "permalink": f"https://acme.slack.com/archives/{args['channel']}/p{args['message_ts'].replace('.', '')}"})
        if method == "views.open":
            state["views"].append(args)
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"ok": False, "error": "unknown_method"})

    monkeypatch.setattr(mod, "_http", post)
    mod._seen.clear()
    mod._recent.clear()
    mod._signed.clear()
    return state


def connect(c, **values):
    return add(c, kind="slack", **({"channel": CHANNEL, "signing_secret": SECRET, "bot_token": TOKEN} | values))


def send(c, conn, body, ctype="application/json", secret=SECRET, ts=None, **headers):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode() if ctype == "application/json" else urlencode(body).encode()
    ts = str(int(time.time()) if ts is None else ts)
    sig = "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + raw, hashlib.sha256).hexdigest()
    return c.post(f"/hooks/slack/{conn['id']}", content=raw, headers={"Content-Type": ctype, "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig, **headers})


def message(c, conn, text, user=PAT, ts="1699999999.000100", channel=CHANNEL, event_id=None, **more):
    return send(c, conn, {"type": "event_callback", "team_id": "T1", "event_id": event_id or f"Ev{ts}",
                          "event": {"type": "message", "channel": channel, "user": user, "text": text, "ts": ts, **more}})


_presses = itertools.count(1)


def trigger():
    """Slack gives every press, command and shortcut its own trigger_id, so no two requests are byte-for-byte the same."""
    return f"trig{next(_presses)}"


def press(c, conn, action, card_id, user=OLLY):
    payload = {"type": "block_actions", "user": {"id": user}, "trigger_id": trigger(), "response_url": "https://hooks.slack.com/actions/T1/1/x",
               "actions": [{"action_id": action, "block_id": "inr_file", "value": card_id}]}
    return send(c, conn, {"payload": json.dumps(payload)}, ctype="application/x-www-form-urlencoded")


def cards(c, who=AS_O):
    return c.get("/api/cards", headers=who).json()["cards"]


def first_card(c, who=AS_O):
    return c.get(f"/api/cards/{cards(c, who)[0]['id']}", headers=who).json()


# ---------- setting it up ----------

def test_a_slack_connector_can_be_added_before_the_slack_app_exists(client, slack):
    made = add(client, kind="slack", channel=CHANNEL)
    assert made["secrets"] == {"signing_secret": {"set": False, "hint": ""}, "bot_token": {"set": False, "hint": ""}}
    url, manifest = made["notes"]
    assert url == {"label": "Request URL", "value": "{origin}/hooks/slack/" + made["id"], "show": True}
    assert manifest["value"].count(url["value"]) == 3 and "users:read.email" in manifest["value"]
    # Slack checks the Request URL while it makes the app, before it gives out the signing secret. That one check is
    # answered. Nothing else Slack sends is accepted until the secret is saved.
    r = send(client, made, {"type": "url_verification", "challenge": "abc"})
    assert r.status_code == 200 and r.json() == {"challenge": "abc"}
    assert send(client, made, {"type": "event_callback", "event": {"type": "message"}}).status_code == 401
    assert send(client, made, {"command": "/idea", "text": "x", "user_id": "U1"}, ctype="application/x-www-form-urlencoded").status_code == 401
    assert send(client, made, b"not json").status_code == 401
    assert not cards(client)
    r = client.post(f"{BASE}/{made['id']}/test", headers=AS_O)
    assert r.status_code == 502 and "bot token" in r.json()["detail"]


def test_the_request_url_uses_public_url_when_set(tmp_path, monkeypatch, slack):
    with make_client(tmp_path, monkeypatch, PUBLIC_URL="https://ideas.example.com") as c:
        made = connect(c)
        assert made["notes"][0]["value"] == f"https://ideas.example.com/hooks/slack/{made['id']}"


def test_secrets_never_come_back(client, slack):
    made = connect(client)
    text = json.dumps(made) + client.get(BASE, headers=AS_O).text
    assert SECRET not in text and TOKEN not in text
    assert made["secrets"]["bot_token"] == {"set": True, "hint": "1111"}


def test_test_names_the_workspace_and_channel(client, slack):
    conn = connect(client)
    r = client.post(f"{BASE}/{conn['id']}/test", headers=AS_O)
    assert r.status_code == 200 and r.json()["detail"] == "Connected to Acme as inrface. Ideas come from #ideas."
    slack["member"] = False
    r = client.post(f"{BASE}/{conn['id']}/test", headers=AS_O)
    assert r.status_code == 502 and "isn't in #ideas" in r.json()["detail"]
    slack["fail"]["auth.test"] = "invalid_auth"
    assert "refused the bot token" in client.post(f"{BASE}/{conn['id']}/test", headers=AS_O).json()["detail"]


def test_a_bad_channel_id_is_refused(client, slack):
    r = client.post(BASE, json={"kind": "slack", "values": {"channel": "#ideas"}}, headers=AS_O)
    assert r.status_code == 422 and "Ideas channel ID" in r.json()["detail"]


# ---------- the signature ----------

def test_slack_checks_the_address(client, slack):
    conn = connect(client)
    r = send(client, conn, {"type": "url_verification", "challenge": "abc123"})
    assert r.status_code == 200 and r.json() == {"challenge": "abc123"}


@pytest.mark.parametrize("how", ["wrong secret", "old", "no headers", "changed body"])
def test_unsigned_requests_do_nothing(client, slack, how):
    conn = connect(client)
    body = {"type": "event_callback", "event_id": "Ev1", "event": {"type": "message", "channel": CHANNEL, "user": PAT, "text": "sneaky", "ts": "1.1"}}
    if how == "wrong secret":
        r = send(client, conn, body, secret="6" * 32)
    elif how == "old":
        r = send(client, conn, body, ts=int(time.time()) - 600)
    elif how == "no headers":
        r = client.post(f"/hooks/slack/{conn['id']}", json=body)
    else:
        raw = json.dumps(body).encode()
        ts = str(int(time.time()))
        sig = "v0=" + hmac.new(SECRET.encode(), b"v0:" + ts.encode() + b":" + raw, hashlib.sha256).hexdigest()
        r = client.post(f"/hooks/slack/{conn['id']}", content=raw.replace(b"sneaky", b"other!"),
                        headers={"Content-Type": "application/json", "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig})
    assert r.status_code == 401
    assert cards(client) == [] and slack["calls"] == []


def test_unknown_off_or_other_connectors_are_not_found(client, slack):
    conn = connect(client)
    hook = add(client)  # a webhook connector: not Slack
    assert send(client, {"id": "0" * 32}, {"type": "url_verification"}).status_code == 404
    assert send(client, {"id": "nope"}, {"type": "url_verification"}).status_code == 404
    assert send(client, hook, {"type": "url_verification"}).status_code == 404
    client.put(f"{BASE}/{conn['id']}", json={"enabled": False}, headers=AS_O)
    assert message(client, conn, "while it's off").status_code == 404
    assert cards(client) == []


def test_a_huge_or_garbled_body_is_refused(client, slack):
    conn = connect(client)
    assert client.post(f"/hooks/slack/{conn['id']}", content=b"x" * (300 * 1024)).status_code == 413
    assert send(client, conn, b"{not json").status_code == 400
    assert send(client, conn, b"\xff\xfe", ctype="application/x-www-form-urlencoded").status_code == 400
    assert send(client, conn, {"something": "else"}, ctype="application/x-www-form-urlencoded").status_code == 400


def test_the_hook_does_not_need_the_app_header_or_a_login(tmp_path, monkeypatch, slack):
    # With real sign-in on, the Slack address still answers: Slack can't sign in, it signs its requests instead.
    with make_client(tmp_path, monkeypatch) as c:
        conn = connect(c)
    with make_client(tmp_path, monkeypatch, AUTH_MODE="access", CF_ACCESS_TEAM="team", CF_ACCESS_AUD="aud") as c:
        assert c.get("/api/cards").status_code == 401
        assert send(c, conn, {"type": "url_verification", "challenge": "ok"}).json() == {"challenge": "ok"}


# ---------- ideas coming in ----------

def test_a_message_in_the_ideas_channel_becomes_a_card(client, slack, pushes):
    conn = connect(client)
    assert message(client, conn, "Weekly digest email &amp; a <https://example.com/x|mock-up> for <@U0OLLY>").status_code == 200
    card = first_card(client)
    assert card["original_text"] == "Weekly digest email & a mock-up (https://example.com/x) for @someone"
    assert card["author_name"] == "Pat" and card["source"] == "slack" and card["status"] == "New" and not card["external"]
    assert card["thread"] == {"kind": "slack", "label": "Slack thread", "url": "https://acme.slack.com/archives/C0IDEAS0001/p1699999999000100"}
    # The app answers in the thread with the three buttons, each carrying the card's id.
    post, = slack["posts"]
    assert post["channel"] == CHANNEL and post["thread_ts"] == "1699999999.000100"
    assert [(b["action_id"], b["value"]) for b in post["blocks"][1]["elements"]] == [("inr_approve", card["id"]), ("inr_defer", card["id"]), ("inr_clarify", card["id"])]
    assert ("owner@example.com", "New idea from Pat", card["title"], card["id"]) in pushes
    # Pat is a giver here too, so the card is theirs in the web app.
    assert [x["id"] for x in cards(client, AS_P)] == [card["id"]]


def test_the_thread_message_links_to_the_card_when_public_url_is_set(tmp_path, monkeypatch, slack):
    with make_client(tmp_path, monkeypatch, PUBLIC_URL="https://ideas.example.com") as c:
        conn = connect(c)
        message(c, conn, "An idea")
        assert f"<https://ideas.example.com/#card={first_card(c)['id']}|Open the card>" in slack["posts"][0]["blocks"][0]["text"]["text"]


def test_the_same_message_only_makes_one_card(client, slack):
    conn = connect(client)
    message(client, conn, "Once", event_id="Ev1")
    message(client, conn, "Once", event_id="Ev1")          # Slack sending the event again
    message(client, conn, "Once", event_id="Ev2")          # the same message under another event id
    assert len(cards(client)) == 1 and len(slack["posts"]) == 1


def test_messages_that_are_not_ideas_are_ignored(client, slack):
    conn = connect(client)
    message(client, conn, "from the app itself", bot_id="B1", ts="1.1")
    message(client, conn, "edited", subtype="message_changed", ts="1.2")
    message(client, conn, "joined", subtype="channel_join", ts="1.3")
    message(client, conn, "another channel", channel="C0RANDOM001", ts="1.4")
    message(client, conn, "   ", ts="1.5")
    message(client, conn, "reply to something that isn't a card", thread_ts="0.9", ts="1.6")
    assert cards(client) == [] and slack["posts"] == []


def test_someone_not_listed_in_inrface_can_still_send_an_idea(client, slack, pushes):
    conn = connect(client)
    message(client, conn, "Dark mode please", user=DANA)
    card = first_card(client)
    assert card["author_name"] == "Dana (Slack)" and card["author_email"] == f"slack:{DANA}" and not card["external"]
    assert ("owner@example.com", "New idea from Dana (Slack)", "Dark mode please", card["id"]) in pushes
    assert cards(client)[0]["author_name"] == "Dana (Slack)"
    # Their address is not kept: only people already listed here are remembered by email.
    assert b"dana@elsewhere.example" not in dump(client)


def test_an_unconfirmed_or_foreign_slack_account_is_not_matched_to_a_person(client, slack):
    conn = connect(client)
    slack["users"]["U0FAKE1"] = {"real_name": "Olly?", "is_email_confirmed": False, "profile": {"email": "owner@example.com"}}
    slack["users"]["U0FAKE2"] = {"real_name": "Olly??", "is_stranger": True, "profile": {"email": "owner@example.com"}}
    message(client, conn, "First", user="U0FAKE1", ts="1.1")
    message(client, conn, "Second", user="U0FAKE2", ts="1.2")
    assert sorted(x["author_name"] for x in cards(client)) == ["Olly? (Slack)", "Olly?? (Slack)"]
    card_id = cards(client)[0]["id"]
    press(client, conn, "inr_approve", card_id, user="U0FAKE1")
    assert {x["status"] for x in cards(client)} == {"New"}
    assert "Only a receiver on Main" in slack["responses"][-1]["text"]


def test_when_slack_cannot_say_who_someone_is_they_are_nobody(client, slack):
    conn = connect(client)
    message(client, conn, "An idea")
    card_id = cards(client)[0]["id"]
    slack["fail"]["users.info"] = "ratelimited"
    press(client, conn, "inr_approve", card_id)
    assert cards(client)[0]["status"] == "New"


def test_the_idea_command(client, slack):
    conn = connect(client)
    form = {"command": "/idea", "text": "Export to &lt;b&gt;CSV&lt;/b&gt; @channel", "user_id": PAT, "channel_id": "C0RANDOM001", "response_url": "https://hooks.slack.com/commands/T1/1/x"}
    r = send(client, conn, form, ctype="application/x-www-form-urlencoded")
    assert r.status_code == 200 and r.content == b""
    card = first_card(client)
    assert card["original_text"] == "Export to <b>CSV</b> @channel" and card["author_name"] == "Pat"
    # It is posted in the ideas channel, with what was typed made harmless, and that message is the thread.
    post, = slack["posts"]
    assert post["channel"] == CHANNEL and "thread_ts" not in post
    assert post["blocks"][0]["text"]["text"] == f"*Idea from <@{PAT}>*\n> Export to &lt;b&gt;CSV&lt;/b&gt; @channel"
    assert card["thread"]["url"].endswith("p1700000000000001")
    assert slack["responses"][-1]["response_type"] == "ephemeral" and "Added to Inrface" in slack["responses"][-1]["text"]
    # A reply under that message lands on the card.
    message(client, conn, "And XLSX", user=DANA, ts="1700000001.000001", thread_ts=post["ts"])
    assert first_card(client)["comments"][-1]["text"] == "And XLSX"


def test_the_idea_command_with_nothing_typed(client, slack):
    conn = connect(client)
    r = send(client, conn, {"command": "/idea", "text": "  ", "user_id": PAT}, ctype="application/x-www-form-urlencoded")
    assert r.json()["response_type"] == "ephemeral" and "/idea" in r.json()["text"]
    assert cards(client) == []


def test_the_command_still_makes_the_card_when_the_channel_cannot_be_reached(client, slack):
    conn = connect(client)
    slack["fail"]["chat.postMessage"] = "not_in_channel"
    send(client, conn, {"command": "/idea", "text": "Keep me", "user_id": PAT, "response_url": "https://hooks.slack.com/commands/T1/1/x"}, ctype="application/x-www-form-urlencoded")
    assert first_card(client)["thread"] is None
    assert "couldn't be posted in the ideas channel" in slack["responses"][-1]["text"] and "/invite" in slack["responses"][-1]["text"]
    assert "ideas channel" in client.get(BASE, headers=AS_O).json()["connectors"][0]["last_error"]


def shortcut(c, conn, channel, ts, text="Bulk rename", author=DANA, sender=PAT, **msg):
    payload = {"type": "message_action", "callback_id": "inr_send", "trigger_id": trigger(), "user": {"id": sender}, "channel": {"id": channel},
               "response_url": "https://hooks.slack.com/actions/T1/2/y", "message": {"type": "message", "user": author, "text": text, "ts": ts, **msg}}
    return send(c, conn, {"payload": json.dumps(payload)}, ctype="application/x-www-form-urlencoded")


def test_the_shortcut_sends_someone_elses_message(client, slack):
    conn = connect(client)
    assert shortcut(client, conn, "C0RANDOM001", "1650000000.000200").status_code == 200
    card = first_card(client)
    assert card["author_name"] == "Dana (Slack)" and card["original_text"] == "Bulk rename"
    post, = slack["posts"]
    lead = post["blocks"][0]["text"]["text"]
    assert post["channel"] == CHANNEL and f"*Idea from <@{DANA}>*, sent by <@{PAT}>" in lead and "See the original message" in lead
    # Pressing it again on the same message makes nothing new.
    shortcut(client, conn, "C0RANDOM001", "1650000000.000200")
    assert len(cards(client)) == 1 and slack["responses"][-1]["text"] == "That message is already in Inrface."


def test_the_shortcut_on_a_message_that_is_already_a_card(client, slack):
    conn = connect(client)
    message(client, conn, "Bulk rename", user=DANA, ts="1650000000.000300")
    shortcut(client, conn, CHANNEL, "1650000000.000300")
    assert len(cards(client)) == 1 and slack["responses"][-1]["text"] == "That message is already in Inrface."


def test_too_many_ideas_in_a_minute(client, slack, monkeypatch):
    from innerface import slack as mod
    monkeypatch.setattr(mod, "IDEAS_PER_MINUTE", 2)
    conn = connect(client)
    for i in range(4):
        message(client, conn, f"Idea {i}", ts=f"2.{i}")
    assert len(cards(client)) == 2


# ---------- replies ----------

def test_thread_replies_become_comments_and_are_not_echoed_back(client, slack, pushes):
    conn = connect(client)
    message(client, conn, "Dark mode please", user=DANA, ts="5.1")
    slack["posts"].clear()
    message(client, conn, "Especially at night", user=DANA, ts="5.2", thread_ts="5.1")
    message(client, conn, "Agreed", user=OLLY, ts="5.3", thread_ts="5.1")
    card = first_card(client)
    assert [(x["author_name"], x["text"]) for x in card["comments"]] == [("Dana (Slack)", "Especially at night"), ("Olly", "Agreed")]
    assert slack["posts"] == []
    assert ("owner@example.com", "Dana (Slack) commented", "Dark mode please: Especially at night", card["id"]) in pushes


def test_a_comment_in_the_web_app_is_posted_in_the_thread(client, slack):
    conn = connect(client)
    message(client, conn, "Dark mode please", user=DANA, ts="5.1")
    card_id = cards(client)[0]["id"]
    assert client.post(f"/api/cards/{card_id}/comments", json={"kind": "comment", "text": "Nice <!channel> idea"}, headers={**AS_O, **H}).status_code == 200
    assert slack["posts"][-1]["thread_ts"] == "5.1" and slack["posts"][-1]["text"] == "*Olly:* Nice &lt;!channel&gt; idea"


# ---------- filing from Slack ----------

def test_approve_and_defer(client, slack, pushes):
    conn = connect(client)
    message(client, conn, "Dark mode please", ts="5.1")
    card_id = cards(client)[0]["id"]
    r = press(client, conn, "inr_approve", card_id)
    assert r.status_code == 200 and r.content == b""
    assert cards(client)[0]["status"] == "Immediate roadmap"
    assert slack["posts"][-1]["thread_ts"] == "5.1" and slack["posts"][-1]["text"] == "Olly moved this to *Immediate roadmap*."
    press(client, conn, "inr_approve", card_id)
    assert slack["responses"][-1]["text"] == "It's already in Immediate roadmap."
    press(client, conn, "inr_defer", card_id)
    assert cards(client)[0]["status"] == "Far down the line"
    # Approving files the idea. It never starts a build.
    assert first_card(client)["build_started_at"] is None and first_card(client)["links"] == []


@pytest.mark.parametrize("who", [PAT, DANA, "U0NOBODY"])
def test_only_receivers_can_press_the_buttons(client, slack, who):
    conn = connect(client)
    message(client, conn, "Dark mode please", ts="5.1")
    card_id = cards(client)[0]["id"]
    for action in ("inr_approve", "inr_defer", "inr_clarify"):
        press(client, conn, action, card_id, user=who)
        assert "Only a receiver on Main" in slack["responses"][-1]["text"]
    assert cards(client)[0]["status"] == "New" and slack["views"] == []


def test_a_receiver_on_another_project_cannot_file_here(client, slack):
    conn = connect(client)
    slack["users"]["U0QUINN"] = {"real_name": "Quinn", "is_email_confirmed": True, "profile": {"email": "quinn@example.com"}}
    message(client, conn, "Dark mode please", ts="5.1")
    press(client, conn, "inr_approve", cards(client)[0]["id"], user="U0QUINN")
    assert cards(client)[0]["status"] == "New"


def test_buttons_only_work_on_cards_from_this_connector(client, slack, gh):
    conn = connect(client)
    other = client.post("/api/cards/dump", json={"text": "Made in the web app", "as_is": True}, headers={**AS_P, **H}).json()["ids"][0]
    press(client, conn, "inr_approve", other)
    assert cards(client)[0]["status"] == "New" and "isn't in Inrface any more" in slack["responses"][-1]["text"]
    press(client, conn, "inr_approve", "not-an-id")
    assert "isn't in Inrface any more" in slack["responses"][-1]["text"]


def test_a_card_being_built_cannot_be_refiled_from_slack(client, slack, gh):
    conn = connect(client)
    message(client, conn, "Dark mode please", ts="5.1")
    card_id = cards(client)[0]["id"]
    scope(client, card_id)
    assert client.post(f"/api/cards/{card_id}/build", headers={**AS_O, **H}).status_code == 200
    assert slack["posts"][-1]["text"] == "Olly moved this to *In dev for testing*."
    press(client, conn, "inr_defer", card_id)
    assert cards(client)[0]["status"] == "In dev for testing" and slack["responses"][-1]["text"] == "This idea is already being built."
    # When GitHub says it shipped, the thread hears about it.
    gh["progress"].update(state="closed", state_reason="completed")
    from innerface import config, service
    assert service.sync_builds(config.get_settings()) == 1
    assert slack["posts"][-1]["text"] == "This is now *Done / Live*."


def test_clarify_asks_in_the_thread_and_the_reply_answers(client, slack, pushes):
    conn = connect(client)
    message(client, conn, "Dark mode please", user=DANA, ts="5.1")
    card_id = cards(client)[0]["id"]
    press(client, conn, "inr_clarify", card_id)
    view = slack["views"][-1]["view"]
    assert slack["views"][-1]["trigger_id"].startswith("trig") and view["callback_id"] == "inr_clarify" and view["private_metadata"] == card_id

    def submit(text, user=OLLY):
        payload = {"type": "view_submission", "user": {"id": user},
                   "view": {"callback_id": "inr_clarify", "private_metadata": card_id, "state": {"values": {"q": {"text": {"type": "plain_text_input", "value": text}}}}}}
        return send(client, conn, {"payload": json.dumps(payload)}, ctype="application/x-www-form-urlencoded")

    assert submit("").json() == {"response_action": "errors", "errors": {"q": "Type your question first"}}
    assert "Only a receiver" in submit("Let me in", user=DANA).json()["errors"]["q"]
    r = submit("Everywhere, or just the editor?")
    assert r.status_code == 200 and r.content == b""
    card = first_card(client)
    assert card["status"] == "Needs info" and card["note"] == "Everywhere, or just the editor?"
    assert slack["posts"][-1]["thread_ts"] == "5.1"
    assert slack["posts"][-1]["text"] == f"<@{DANA}> *Olly asks:* Everywhere, or just the editor?\nReply in this thread to answer."
    # Dana answers in the thread.
    message(client, conn, "Everywhere", user=DANA, ts="5.4", thread_ts="5.1")
    card = first_card(client)
    assert card["status"] == "Answered" and card["comments"][-1]["type"] == "reply" and card["comments"][-1]["author_name"] == "Dana (Slack)"
    assert ("owner@example.com", "Dana (Slack) answered", "Dark mode please: Everywhere", card_id) in pushes


def test_a_question_from_the_web_app_reaches_the_thread(client, slack):
    conn = connect(client)
    message(client, conn, "Dark mode please", ts="5.1")   # from Pat, who is listed here
    card_id = cards(client)[0]["id"]
    r = client.patch(f"/api/cards/{card_id}", json={"status": "Needs info", "message": "Which screens?"}, headers={**AS_O, **H})
    assert r.status_code == 200, r.text
    assert slack["posts"][-1]["text"] == f"<@{PAT}> *Olly asks:* Which screens?\nReply in this thread to answer."


def test_a_reply_on_a_merged_card_goes_to_the_card_it_became(client, slack):
    conn = connect(client)
    message(client, conn, "Dark mode", ts="5.1")
    message(client, conn, "Night theme", ts="6.1")
    newer, older = [x["id"] for x in cards(client)]
    assert client.post(f"/api/cards/{older}/merge", json={"into": newer}, headers={**AS_O, **H}).status_code == 200
    message(client, conn, "Still want this", ts="5.2", thread_ts="5.1")
    assert first_card(client)["comments"][-1]["text"] == "Still want this"


def test_removing_the_connector_keeps_cards_and_stops_posting(client, slack):
    conn = connect(client)
    message(client, conn, "Dark mode please", user=DANA, ts="5.1")
    card_id = cards(client)[0]["id"]
    assert client.delete(f"{BASE}/{conn['id']}", headers={**AS_O, **H}).status_code == 200
    slack["posts"].clear()
    client.post(f"/api/cards/{card_id}/comments", json={"kind": "comment", "text": "Hello?"}, headers={**AS_O, **H})
    assert slack["posts"] == [] and first_card(client)["author_name"] == "Dana (Slack)"
    assert message(client, conn, "Gone", ts="7.1").status_code == 404


def test_other_projects_receivers_cannot_see_the_connector(client, slack):
    conn = connect(client)
    assert client.get(BASE, headers=AS_Q).status_code == 404
    assert client.post(f"{BASE}/{conn['id']}/test", headers=AS_Q).status_code == 404


def test_markup_from_slack_reads_as_plain_text(client, slack):
    from innerface import config, slack as mod
    s = config.get_settings()
    assert mod.plain(s, "<!here> see <#C1|general> and <mailto:a@b.co|a@b.co> &lt;3 <!subteam^S1|@design>") == "@here see #general and a@b.co (mailto:a@b.co) <3 @design"
    assert mod.plain(s, None) == ""


# ---------- from the security review ----------

def test_the_notification_text_is_made_harmless_too(client, slack):
    conn = connect(client)
    send(client, conn, {"command": "/idea", "text": "&lt;!channel&gt; &lt;https://evil.example|click&gt;", "user_id": PAT}, ctype="application/x-www-form-urlencoded")
    post, = slack["posts"]
    assert post["text"] == "Added to Inrface: &lt;!channel&gt; &lt;https://evil.example|click&gt;"
    assert "<!channel>" not in json.dumps(post) and "<https://evil.example" not in json.dumps(post)


def test_a_copied_request_does_nothing_the_second_time(client, slack):
    conn = connect(client)
    form = {"command": "/idea", "text": "Only once", "user_id": PAT, "response_url": "https://hooks.slack.com/commands/T1/1/x"}
    now = int(time.time())
    for _ in range(3):
        r = send(client, conn, form, ctype="application/x-www-form-urlencoded", ts=now)
        assert r.status_code == 200 and r.content == b""
    assert len(cards(client)) == 1 and len(slack["posts"]) == 1
    # The same words sent a second later are a new request.
    send(client, conn, form, ctype="application/x-www-form-urlencoded", ts=now + 1)
    assert len(cards(client)) == 2
    # A copied button press doesn't file twice either.
    card_id = cards(client)[0]["id"]
    payload = {"payload": json.dumps({"type": "block_actions", "user": {"id": OLLY}, "response_url": "https://hooks.slack.com/actions/T1/1/x",
                                      "actions": [{"action_id": "inr_approve", "block_id": "inr_file", "value": card_id}]})}
    slack["responses"].clear()
    send(client, conn, payload, ctype="application/x-www-form-urlencoded", ts=now)
    send(client, conn, payload, ctype="application/x-www-form-urlencoded", ts=now)
    assert cards(client)[0]["status"] == "Immediate roadmap"
    assert slack["responses"] == []  # the copy was dropped, not answered with "It's already in…"
    press(client, conn, "inr_approve", card_id)
    assert slack["responses"][-1]["text"] == "It's already in Immediate roadmap."


def test_too_many_replies_in_a_minute(client, slack, monkeypatch):
    from innerface import slack as mod
    monkeypatch.setattr(mod, "REPLIES_PER_MINUTE", 2)
    conn = connect(client)
    message(client, conn, "Dark mode please", ts="5.1")
    for i in range(4):
        message(client, conn, f"Reply {i}", user=DANA, ts=f"6.{i}", thread_ts="5.1")
    assert [x["text"] for x in first_card(client)["comments"]] == ["Reply 0", "Reply 1"]
    # Replies don't use up the room for new ideas.
    message(client, conn, "Another idea", ts="7.1")
    assert len(cards(client)) == 2


def test_an_address_slack_does_not_call_confirmed_is_not_matched(client, slack, caplog):
    conn = connect(client)
    slack["users"]["U0MAYBE"] = {"real_name": "Olly", "profile": {"email": "owner@example.com"}}  # no is_email_confirmed at all
    message(client, conn, "An idea", ts="1.1")
    with caplog.at_level("INFO", logger="innerface.slack"):
        press(client, conn, "inr_approve", cards(client)[0]["id"], user="U0MAYBE")
    assert cards(client)[0]["status"] == "New" and "Only a receiver on Main" in slack["responses"][-1]["text"]
    assert "doesn't mark the email address as confirmed" in caplog.text and "owner@example.com" not in caplog.text


def test_what_one_workspace_says_about_a_person_is_not_used_for_another(client, slack):
    here, there = connect(client), connect(client, channel="C0OTHER0001")
    message(client, here, "An idea", ts="1.1")
    message(client, there, "Another", ts="2.1", channel="C0OTHER0001")
    by_conn = {x["original_text"]: x["id"] for x in cards(client)}
    # In the first workspace U0SAME is Olly, a receiver.
    slack["users"]["U0SAME"] = {"real_name": "Olly O", "is_email_confirmed": True, "profile": {"email": "owner@example.com"}}
    press(client, here, "inr_approve", by_conn["An idea"], user="U0SAME")
    assert {x["original_text"]: x["status"] for x in cards(client)}["An idea"] == "Immediate roadmap"
    # In the second, the same id belongs to somebody else. What the first one said must not carry over.
    slack["users"]["U0SAME"] = {"real_name": "Mallory", "is_email_confirmed": True, "profile": {"email": "mallory@elsewhere.example"}}
    press(client, there, "inr_approve", by_conn["Another"], user="U0SAME")
    assert {x["original_text"]: x["status"] for x in cards(client)}["Another"] == "New"
    assert "Only a receiver on Main" in slack["responses"][-1]["text"]


def test_when_too_much_is_waiting_slack_is_told_to_try_again(client, slack, monkeypatch):
    from innerface import service
    conn = connect(client)
    now = int(time.time())
    with monkeypatch.context() as m:
        m.setattr(service, "_submit", lambda fn, *a: False)
        assert message(client, conn, "Wait for me", ts="1.1").status_code == 503
        form = {"command": "/idea", "text": "Me too", "user_id": PAT}
        assert send(client, conn, form, ctype="application/x-www-form-urlencoded", ts=now).status_code == 503
    assert cards(client) == []
    # The very same requests are taken when they come again.
    assert message(client, conn, "Wait for me", ts="1.1").status_code == 200
    assert send(client, conn, form, ctype="application/x-www-form-urlencoded", ts=now).status_code == 200
    assert sorted(x["original_text"] for x in cards(client)) == ["Me too", "Wait for me"]


def test_once_the_secret_is_saved_an_unsigned_check_is_refused(client, slack):
    conn = connect(client)
    r = client.post(f"/hooks/slack/{conn['id']}", json={"type": "url_verification", "challenge": "abc"})
    assert r.status_code == 401
    assert send(client, conn, {"type": "url_verification", "challenge": "abc"}, secret="6" * 32).status_code == 401
