"""Builds asked for from an AI app: they wait for a press in the app."""
import json

from innerface import config, db, voice

from test_app import AS_O, AS_P, AS_Q, OUTSIDER, OWNER, PARTNER, client, dump, gh, pushes, scope, tool  # noqa: F401

def ready(c, text="A weekly report"):
    cid = dump(c, text)[0]
    scope(c, cid)
    return cid


def ask(c, cid, who=OWNER, **kw):
    return tool(c, "start_build", who=who, card_id=cid, user_confirmed=True, **kw)


def card(c, cid, who=AS_O):
    return c.get(f"/api/cards/{cid}", headers=who).json()


def test_asking_from_an_ai_app_sends_nothing(client, pushes, gh):
    cid = ready(client)
    pushes.clear()
    r = ask(client, cid)
    assert r["started"] is False and r["waiting_for_approval"] and r["would_go_to"] == ["GitHub"]
    assert "Nothing has been sent" in r["tell_the_user"]
    assert not gh["issues"]
    assert pushes == [(OWNER, "Start this build?", "A weekly report", cid, "build")]   # the alert carries Start build / Not now buttons
    got = card(client, cid)
    assert got["build_ask"]["by_name"] == "Olly" and got["status"] != "In dev for testing" and not got["build_started_at"]
    assert "build_asked_by" not in got
    # The AI app is told the same when it reads the card or the list.
    assert "Nothing has been sent" in tool(client, "get_card", card_id=cid)["build_waiting"]
    assert "build_waiting" in tool(client, "list_cards")["cards"][0]


def test_a_press_in_the_app_starts_it(client, pushes, gh):
    cid = ready(client)
    ask(client, cid)
    r = client.post(f"/api/cards/{cid}/build", headers=AS_O)
    assert r.status_code == 200 and len(gh["issues"]) == 1
    got = card(client, cid)
    assert got["build_ask"] is None and got["status"] == "In dev for testing"
    # Asking again after it started sends nothing more.
    again = ask(client, cid)
    assert again["already_started"] and len(gh["issues"]) == 1


def test_givers_never_see_or_answer_a_request(client, pushes, gh):
    cid = ready(client)
    ask(client, cid)
    assert card(client, cid, AS_P)["build_ask"] is None
    assert [x["build_ask"] for x in client.get("/api/cards", headers=AS_P).json()["cards"]] == [None]
    assert "build_waiting" not in tool(client, "get_card", who=PARTNER, card_id=cid)
    assert client.post(f"/api/cards/{cid}/build-ask/decline", headers=AS_P).status_code == 403
    assert client.post(f"/api/cards/{cid}/build-ask/decline", headers=AS_Q).status_code == 404
    assert card(client, cid)["build_ask"]
    assert "error" in ask(client, cid, who=PARTNER)


def test_not_now_leaves_the_idea_as_it_was(client, pushes, gh):
    cid = ready(client)
    ask(client, cid)
    assert client.post(f"/api/cards/{cid}/build-ask/decline", headers=AS_O).status_code == 200
    got = card(client, cid)
    assert got["build_ask"] is None and not gh["issues"] and got["status"] == "New"
    with db.connect(config.get_settings()) as c:
        kinds = [r["kind"] for r in c.execute("SELECT kind FROM activity WHERE card_id=? ORDER BY created_at", (cid,))]
    assert kinds[-2:] == ["build_asked", "build_declined"]
    # It can be asked for again.
    assert ask(client, cid)["waiting_for_approval"] and card(client, cid)["build_ask"]


def test_asking_twice_sends_one_alert(client, pushes, gh):
    cid = ready(client)
    pushes.clear()
    ask(client, cid)
    ask(client, cid)
    assert len(pushes) == 1
    # Later on, asking again sends the alert again.
    with db.connect(config.get_settings()) as c:
        c.execute("UPDATE cards SET build_asked_at=? WHERE id=?", (db.ago(voice.ALERT_EVERY + 5), cid))
    ask(client, cid)
    assert len(pushes) == 2


def test_a_request_is_dropped_after_a_day(client, pushes, gh):
    cid = ready(client)
    ask(client, cid)
    with db.connect(config.get_settings()) as c:
        c.execute("UPDATE cards SET build_asked_at=? WHERE id=?", (db.ago(voice.ASK_SECONDS + 60), cid))
    assert card(client, cid)["build_ask"] is None
    assert "build_waiting" not in tool(client, "get_card", card_id=cid)


def test_it_says_at_once_when_an_idea_cant_be_built(client, pushes, gh):
    cid = dump(client, "Not scoped yet", as_is=True)[0]
    pushes.clear()
    r = ask(client, cid)
    assert "Scope this idea first" in r["error"] and not pushes
    assert card(client, cid)["build_ask"] is None
    assert "error" in tool(client, "start_build", card_id=cid, user_confirmed=False)
