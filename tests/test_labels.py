"""Board labels: a team's own names for where an idea sits. Each counts as one of a few core meanings."""
import json

from innerface import config, db, labels

from test_app import AS_O, AS_P, AS_Q, client, dump, gh, pushes, scope  # noqa: F401

SPRINTS = [
    {"name": "Inbox", "kind": "New"}, {"name": "Ask", "kind": "Needs info"},
    {"name": "Next sprint", "kind": "Roadmap"}, {"name": "This sprint", "kind": "Roadmap"}, {"name": "Maybe one day", "kind": "Someday"},
    {"name": "Building", "kind": "Active development"}, {"name": "QA", "kind": "Testing"},
    {"name": "Shipped", "kind": "In production"}, {"name": "No", "kind": "Rejected"},
]


def put(c, rows, who=AS_O):
    return c.put("/api/projects/main/labels", json={"labels": rows}, headers=who)


def card(c, cid, who=AS_P):
    return c.get(f"/api/cards/{cid}", headers=who).json()


def move(c, cid, to):
    return c.patch(f"/api/cards/{cid}", json={"status": to}, headers=AS_O)


def built(client, gh):
    cid = dump(client)[0]
    scope(client, cid)
    client.post(f"/api/cards/{cid}/build", headers=AS_O)
    return cid


def test_there_are_only_a_few_core_meanings(client):
    assert [k for k, _ in labels.KINDS] == ["New", "Needs info", "Roadmap", "Someday", "Active development", "Testing", "In production", "Rejected"]
    assert client.get("/api/me", headers=AS_O).json()["kinds"] == [k for k, _ in labels.KINDS]


def test_a_project_starts_with_the_usual_labels_including_review_and_testing(client):
    got = client.get("/api/me", headers=AS_O).json()["projects"][0]["labels"]
    assert [x["name"] for x in got] == [x["name"] for x in labels.DEFAULT]
    assert {"Under Review", "In Testing", "Being built", "Done / Live"} <= {x["name"] for x in got}
    by = {x["name"]: x for x in got}
    assert by["In Testing"]["kind"] == "Testing" and by["In Testing"]["status"] == "In dev for testing"
    assert by["Far down the line"]["kind"] == "Someday" and by["6-month roadmap"]["kind"] == "Roadmap"


def test_a_card_moved_to_a_label_counts_as_that_labels_meaning(client, pushes):
    cid = dump(client)[0]
    assert move(client, cid, "Under Review").status_code == 200
    c = card(client, cid)
    assert c["status"] == "New" and c["label"] == "Under Review"
    assert move(client, cid, "Not a label").status_code == 422
    # A built-in status named as it always was is that exact status, and sits under the label with that name.
    move(client, cid, "6-month roadmap")
    assert card(client, cid)["status"] == "6-month roadmap" and card(client, cid)["label"] == ""
    # Roadmap and Someday are different meanings, whatever the labels are called.
    move(client, cid, "Far down the line")
    assert card(client, cid)["status"] == "Far down the line"


def test_a_team_can_add_a_sprint_label_and_move_ideas_to_it(client, pushes):
    assert put(client, SPRINTS).status_code == 200
    me = client.get("/api/me", headers=AS_P).json()["projects"][0]["labels"]
    assert [x["name"] for x in me] == [x["name"] for x in SPRINTS]
    cid = dump(client)[0]
    move(client, cid, "This sprint")
    c = card(client, cid)
    assert c["status"] == "Immediate roadmap" and c["label"] == "This sprint"
    assert any("This sprint" in p[1] for p in pushes if p[1].startswith("Moved to"))
    move(client, cid, "Maybe one day")
    assert card(client, cid)["status"] == "Far down the line"


def test_the_building_meanings_are_reached_by_starting_a_build(client, pushes, gh):
    put(client, SPRINTS)
    cid = dump(client)[0]
    r = move(client, cid, "QA")
    assert r.status_code == 422 and "Start build" in r.json()["detail"]
    assert move(client, cid, "Building").status_code == 422
    scope(client, cid)
    client.post(f"/api/cards/{cid}/build", headers=AS_O)
    c = card(client, cid)
    assert c["status"] == "In dev for testing" and c["label"] == ""      # the first label that counts as building
    assert move(client, cid, "QA").status_code == 200
    c = card(client, cid)
    assert c["status"] == "In dev for testing" and c["label"] == "QA"


def test_a_status_change_from_elsewhere_drops_the_label(client, pushes, gh):
    put(client, SPRINTS)
    cid = dump(client)[0]
    move(client, cid, "This sprint")
    scope(client, cid)
    client.post(f"/api/cards/{cid}/build", headers=AS_O)
    assert card(client, cid)["label"] == ""


def test_a_label_taken_away_puts_its_ideas_back_with_their_meaning(client, pushes):
    put(client, SPRINTS)
    cid = dump(client)[0]
    move(client, cid, "This sprint")
    put(client, [x for x in SPRINTS if x["name"] != "This sprint"])
    c = card(client, cid)
    assert c["status"] == "Immediate roadmap" and c["label"] == ""


def test_labels_saved_with_a_built_in_status_are_still_read(client):
    with db.connect(config.get_settings()) as c:
        c.execute("INSERT INTO project_labels(project_id, labels) VALUES ('main', ?)",
                  (json.dumps([{"name": "Inbox", "status": "New"}, {"name": "Q", "status": "Needs info"}, {"name": "Soon", "status": "6-month roadmap"},
                               {"name": "Dev", "status": "In dev for testing"}, {"name": "Live", "status": "Done / Live"}, {"name": "No", "status": "Rejected"}]),))
    got = client.get("/api/projects/main/labels", headers=AS_O).json()["labels"]
    assert [(x["name"], x["kind"]) for x in got] == [("Inbox", "New"), ("Q", "Needs info"), ("Soon", "Roadmap"), ("Dev", "Active development"),
                                                       ("Live", "In production"), ("No", "Rejected")]


def test_the_rules_for_labels(client):
    assert put(client, SPRINTS[:-1]).status_code == 422               # nowhere for a rejected idea
    assert put(client, [x for x in SPRINTS if x["kind"] != "Active development"]).status_code == 422
    assert put(client, [x for x in SPRINTS if x["kind"] != "Testing"]).status_code == 200   # testing is optional
    assert put(client, [*SPRINTS, {"name": "inbox", "kind": "New"}]).status_code == 422   # same name
    assert put(client, [*SPRINTS, {"name": "", "kind": "New"}]).status_code == 422
    assert put(client, [*SPRINTS, {"name": "x" * 31, "kind": "New"}]).status_code == 422
    assert put(client, [*SPRINTS, {"name": "Odd", "kind": "Whatever"}]).status_code == 422
    assert put(client, [*SPRINTS, {"name": "Odd", "kind": "In dev for testing"}]).status_code == 422   # a built-in status isn't a meaning
    assert put(client, [{"name": f"L{i}", "kind": "New"} for i in range(15)]).status_code == 422


def test_only_a_receiver_changes_them_and_they_can_be_put_back(client):
    assert put(client, SPRINTS, who=AS_P).status_code == 404
    assert put(client, SPRINTS, who=AS_Q).status_code == 404
    assert client.put("/api/projects/main/labels", json={"labels": SPRINTS}).status_code == 403
    put(client, SPRINTS)
    assert [x["name"] for x in client.get("/api/projects/main/labels", headers=AS_O).json()["labels"]][0] == "Inbox"
    r = client.put("/api/projects/main/labels", json={"labels": None}, headers=AS_O)
    assert r.status_code == 200 and [x["name"] for x in r.json()["labels"]] == [x["name"] for x in labels.DEFAULT]
