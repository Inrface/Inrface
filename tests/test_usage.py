"""The usage page: what a project's receivers see about how it is being used."""
from innerface import config, db

from test_app import AS_O, AS_P, AS_Q, OWNER, PARTNER, client, dump, gh, make_client, pushes, scope, tool  # noqa: F401
from test_slack import connect, message, press, slack, first_card  # noqa: F401


def usage(c, who=AS_O, project="main", **params):
    r = c.get(f"/api/projects/{project}/usage", params=params, headers=who)
    assert r.status_code == 200, r.text
    return r.json()


def by_source(u):
    return {x["key"]: x["count"] for x in u["ideas"]["by_source"]}


def progress(u):
    return {x["key"]: x["count"] for x in u["progress"]}


def actions(u):
    return {r["key"]: r["counts"] for r in u["actions"]["rows"]}


def test_only_a_projects_receivers_see_it(client, pushes):
    assert client.get("/api/projects/main/usage", headers=AS_P).status_code == 404   # a giver here
    assert client.get("/api/projects/main/usage", headers=AS_Q).status_code == 404   # not in this project
    assert client.get("/api/projects/nope/usage", headers=AS_O).status_code == 404
    assert client.get("/api/projects/main/usage?days=1", headers=AS_O).status_code == 422
    u = usage(client)
    assert u["ideas"]["total"] == 0 and u["times"]["to_build"] == {"hours": None, "count": 0}
    assert u["people"] == {"givers": 0, "receivers": 0}


def test_ideas_are_counted_by_where_they_came_from(client, pushes, slack):
    dump(client, "Typed in the web app")
    tool(client, "submit_idea", who=PARTNER, text="Said to an AI app")
    tool(client, "submit_idea", who=PARTNER, text="Another from an AI app")
    message(client, connect(client), "Posted in Slack")
    u = usage(client)
    assert by_source(u) == {"web": 1, "mcp": 2, "slack": 1, "api": 0}
    assert u["ideas"]["total"] == 4 and u["ideas"]["previous"] == 0
    assert actions(u)["sent"] == {"web": 1, "mcp": 2, "slack": 1}
    assert u["people"] == {"givers": 1, "receivers": 0} and u["actions"]["unrecorded"] == 0


def test_it_says_where_receivers_did_their_work(client, pushes, gh, slack):
    a, b, c = dump(client, "One"), dump(client, "Two"), dump(client, "Three")
    # One is handled in the web app, one from an AI app, and one from the Slack thread.
    scope(client, a[0])
    assert client.post(f"/api/cards/{a[0]}/build", headers=AS_O).status_code == 200
    tool(client, "ask_question", card_id=b[0], question="Which numbers?")
    tool(client, "update_scope", card_id=b[0], summary="Numbers", acceptance=["Shows them"], open_questions=[])
    # Asked for from the AI app, started with a press in the web app: that build still counts as the AI app's.
    tool(client, "start_build", card_id=b[0], user_confirmed=True)
    assert client.post(f"/api/cards/{b[0]}/build", headers=AS_O).status_code == 200
    conn = connect(client)
    message(client, conn, "From Slack")
    card = first_card(client)
    press(client, conn, "inr_approve", card["id"])
    u = usage(client)
    got = actions(u)
    assert got["scoped"] == {"web": 1, "mcp": 1, "slack": 0}
    assert got["built"] == {"web": 1, "mcp": 1, "slack": 0}
    assert got["asked"] == {"web": 0, "mcp": 1, "slack": 0}
    assert got["filed"] == {"web": 0, "mcp": 0, "slack": 1}
    assert progress(u) == {"received": 4, "scoped": 2, "built": 2, "done": 0}
    assert u["people"] == {"givers": 1, "receivers": 1}
    # Work done in a web request after an AI app's call is counted as the web app again.
    scope(client, c[0])
    assert actions(usage(client))["scoped"]["web"] == 2


def test_times_are_the_middle_value_in_hours(client, pushes, gh):
    s = config.get_settings()
    ids = [dump(client, f"Idea {i}")[0] for i in range(3)]
    for cid in ids:
        scope(client, cid)
        assert client.post(f"/api/cards/{cid}/build", headers=AS_O).status_code == 200
    with db.connect(s) as c:
        # Sent 10, 20 and 60 hours ago; first looked at 2, 4 and 6 hours later; built now; one done 3 hours after that.
        for cid, hours, looked in zip(ids, (10, 20, 60), (2, 4, 6)):
            c.execute("UPDATE cards SET created_at=? WHERE id=?", (db.ago(hours * 3600), cid))
            c.execute("UPDATE activity SET created_at=? WHERE card_id=? AND kind='scoped'", (db.ago((hours - looked) * 3600), cid))
        c.execute("UPDATE cards SET build_started_at=?, status='Done / Live' WHERE id=?", (db.ago(3 * 3600), ids[0]))
        c.execute("INSERT INTO activity(id, card_id, project_id, actor_email, kind, detail, via, created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (db.new_id(), ids[0], "main", "github", "status", "Done / Live", "github", db.now()))
    t = usage(client)["times"]
    assert t["first_response"] == {"hours": 4.0, "count": 3}
    assert t["to_build"]["count"] == 3 and t["to_build"]["hours"] == 20.0
    assert t["build_to_done"] == {"hours": 3.0, "count": 1}
    assert progress(usage(client))["done"] == 1


def test_the_period_and_the_one_before(client, pushes):
    s = config.get_settings()
    old, older, recent = dump(client, "Last month")[0], dump(client, "Long ago")[0], dump(client, "This week")[0]
    with db.connect(s) as c:
        c.execute("UPDATE cards SET created_at=? WHERE id=?", (db.ago(40 * 86400), old))
        c.execute("UPDATE cards SET created_at=? WHERE id=?", (db.ago(200 * 86400), older))
    u = usage(client)
    assert u["days"] == 30 and u["ideas"]["total"] == 1 and u["ideas"]["previous"] == 1
    assert usage(client, days=365)["ideas"]["total"] == 3


def test_merged_and_rejected_ideas(client, pushes):
    a, b, c = dump(client, "Keep")[0], dump(client, "Duplicate")[0], dump(client, "No")[0]
    assert client.post(f"/api/cards/{b}/merge", json={"into": a}, headers=AS_O).status_code == 200
    assert client.patch(f"/api/cards/{c}", json={"status": "Rejected"}, headers=AS_O).status_code == 200
    u = usage(client)
    assert u["ideas"]["total"] == 3 and u["rejected"] == 1


def test_older_activity_is_filled_in_where_it_can_be(tmp_path, monkeypatch, pushes):
    with make_client(tmp_path, monkeypatch) as c:
        cid = dump(c, "Before this was recorded")[0]
        r = tool(c, "submit_idea", who=PARTNER, text="From an AI app")
        scope(c, cid)
        s = config.get_settings()
        with db.connect(s) as conn:
            conn.execute("UPDATE activity SET via=''")
    # Starting again fills in where each idea was sent from. What a receiver did before stays unknown.
    with make_client(tmp_path, monkeypatch) as c:
        u = usage(c)
        assert actions(u)["sent"] == {"web": 1, "mcp": 1, "slack": 0}
        assert actions(u)["scoped"] == {"web": 0, "mcp": 0, "slack": 0} and u["actions"]["unrecorded"] == 1
        with db.connect(config.get_settings()) as conn:
            assert conn.execute("SELECT via FROM activity WHERE card_id=? AND kind='created'", (r["id"],)).fetchone()[0] == "mcp"


def test_it_names_nobody(client, pushes):
    dump(client, "An idea with a secret plan")
    text = client.get("/api/projects/main/usage", headers=AS_O).text
    assert OWNER not in text and PARTNER not in text.lower() and "secret plan" not in text
