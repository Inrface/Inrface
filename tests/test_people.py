"""People and projects kept in the app, laid over the settings file."""
from innerface import config

from test_app import AS_O, AS_P, AS_Q, CONFIG, OWNER, PARTNER, client, dump, make_client, mcp, pushes, tool  # noqa: F401

NEW = "sam@example.com"
AS_S = {"X-Dev-User": NEW, "X-Requested-With": "Inrface"}
QUINN = "quinn@example.com"


def put(c, email, role, name="", who=AS_O, project="main"):
    return c.put(f"/api/projects/{project}/people", json={"email": email, "role": role, "name": name}, headers=who)


def roles(c, who=AS_O, project="main"):
    return {p["email"]: p["role"] for p in c.get(f"/api/projects/{project}/people", headers=who).json()["people"]}


def test_a_receiver_adds_someone_and_they_can_get_in(client, pushes):
    assert client.get("/api/me", headers=AS_S).status_code == 403
    r = put(client, " Sam@Example.com ", "giver", "Sam")
    assert r.status_code == 200
    assert roles(client) == {OWNER: "receiver", PARTNER: "giver", NEW: "giver"}
    me = client.get("/api/me", headers=AS_S).json()
    assert me["name"] == "Sam" and [(p["id"], p["role"]) for p in me["projects"]] == [("main", "giver")]
    cid = dump(client, "Sam's idea", who=AS_S)[0]
    assert client.get(f"/api/cards/{cid}", headers=AS_O).json()["author_name"] == "Sam"
    # An AI app signed in as them works too.
    assert tool(client, "list_cards", who=NEW)["cards"][0]["id"] == cid


def test_only_that_projects_receivers_see_or_change_who_is_in_it(client, pushes):
    for who in (AS_P, AS_Q):
        assert client.get("/api/projects/main/people", headers=who).status_code == 404
        assert put(client, NEW, "giver", "Sam", who=who).status_code == 404
        assert put(client, PARTNER, "receiver", who=who).status_code == 404
    assert client.get("/api/projects/main/people", headers=AS_S).status_code == 403
    assert client.put("/api/projects/main/people", json={"email": NEW, "role": "giver", "name": "Sam"}, headers={"X-Dev-User": OWNER}).status_code == 403
    # A giver still sees names and never addresses.
    put(client, NEW, "giver", "Sam")
    seen = client.get("/api/me", headers=AS_P).text
    assert "Sam" in seen and NEW not in seen and OWNER not in seen
    # AI apps have no way to change who is in a project.
    names = {t["name"] for t in mcp(client, "tools/list")["result"]["tools"]}
    assert not [n for n in names if any(w in n for w in ("people", "person", "member", "invite", "project_"))]


def test_a_role_can_be_changed_and_the_last_receiver_cannot_go(client, pushes):
    assert put(client, OWNER, "giver").status_code == 422
    assert put(client, OWNER, "none").status_code == 422
    assert put(client, PARTNER, "receiver").status_code == 200
    assert client.get("/api/projects/main/people", headers=AS_P).status_code == 200
    assert (PARTNER, "Your role changed", "You are now a receiver in Main.", None) in pushes
    # Now Olly can step down, and is told they no longer manage it.
    assert put(client, OWNER, "giver").json()["left"] is True
    assert client.get("/api/projects/main/people", headers=AS_O).status_code == 404
    assert roles(client, AS_P) == {PARTNER: "receiver", OWNER: "giver"}
    assert put(client, PARTNER, "giver", who=AS_P).status_code == 422


def test_taking_someone_out_ends_their_access_and_keeps_their_ideas(client, pushes):
    cid = dump(client, "Pat's idea")[0]
    assert put(client, PARTNER, "none").status_code == 200
    assert client.get("/api/me", headers=AS_P).status_code == 403
    assert client.get(f"/api/cards/{cid}", headers=AS_P).status_code == 403
    assert mcp(client, "tools/call", {"name": "list_cards", "arguments": {}}, who=PARTNER, raw=True).status_code == 403
    assert client.get(f"/api/cards/{cid}", headers=AS_O).json()["author_name"] == "Pat"
    assert put(client, PARTNER, "none").status_code == 404
    # No alert goes to someone who is no longer in the project.
    pushes.clear()
    client.patch(f"/api/cards/{cid}", json={"status": "Done / Live"}, headers=AS_O)
    assert not [p for p in pushes if p[0] == PARTNER]
    # They can be put back, and no name is needed for someone already known.
    assert put(client, PARTNER, "giver").status_code == 200
    assert client.get(f"/api/cards/{cid}", headers=AS_P).status_code == 200


def test_what_is_saved_outlives_a_restart_and_wins_over_the_file(tmp_path, monkeypatch, pushes):
    with make_client(tmp_path, monkeypatch) as c:
        put(c, NEW, "receiver", "Sam")
        put(c, PARTNER, "none")
        c.put("/api/projects/main/name", json={"name": "School app"}, headers=AS_O)
    with make_client(tmp_path, monkeypatch) as c:
        assert roles(c) == {OWNER: "receiver", NEW: "receiver"}
        assert client_names(c) == ["School app"]
        s = config.get_settings()
        assert s.file_projects["main"].name == "Main" and s.projects["main"].repo == "acme/app"
        assert put(c, NEW, "none").status_code == 200
    # The file changes so that the only receiver it names is someone taken out here. A project nobody receives
    # is no use, so the file's own list stands.
    only_pat = CONFIG.replace('receivers = ["olly"]', 'receivers = ["pat"]').replace('givers = ["pat"]', "givers = []")
    with make_client(tmp_path, monkeypatch, config=only_pat) as c:
        assert config.get_settings().projects["main"].receivers == (PARTNER,)
        assert roles(c, AS_P) == {PARTNER: "receiver"}


def client_names(c, who=AS_O):
    return [p["name"] for p in c.get("/api/me", headers=who).json()["projects"]]


def test_the_other_receivers_hear_when_people_change(client, pushes):
    put(client, QUINN, "receiver")
    pushes.clear()
    put(client, NEW, "giver", "Sam")
    assert pushes == [(QUINN, "People changed", "Olly added Sam to Main as a giver.", None)]
    pushes.clear()
    put(client, NEW, "none")
    assert pushes == [(QUINN, "People changed", "Olly took Sam out of Main.", None)]
    log = client.get("/api/projects/main/people", headers=AS_Q).json()["changes"]
    assert [x["what"] for x in log][:2] == ["took Sam out", "added Sam as a giver"] and log[0]["by"] == "Olly"
    assert "@" not in str(log)


def test_names_and_addresses_are_checked(client, pushes):
    assert put(client, NEW, "giver").status_code == 422  # someone new needs a name
    for bad in ("sam", "sam@", "@example.com", "sam@example", "sam smith@example.com", "sam@exa mple.com", "<sam@example.com>", "a@b.c", "slack:U123", "sam@example.com,pat@example.com"):
        assert put(client, bad, "giver", "Sam").status_code == 422, bad
    assert put(client, NEW, "owner", "Sam").status_code == 422
    assert put(client, NEW, "giver", "  Sam   Smith ").status_code == 200
    assert client.get("/api/me", headers=AS_S).json()["name"] == "Sam Smith"
    # A receiver can put a name right.
    assert put(client, NEW, "giver", "Samira").status_code == 200
    assert client.get("/api/me", headers=AS_S).json()["name"] == "Samira"
    assert put(client, NEW, "giver", "x" * 81).status_code == 422


def test_a_receiver_starts_a_project_and_names_it(client, pushes):
    assert client.post("/api/projects", json={"name": "Second thing"}, headers=AS_P).status_code == 403
    made = client.post("/api/projects", json={"name": "Second  thing!"}, headers=AS_O)
    assert made.status_code == 201 and made.json() == {"id": "second-thing", "name": "Second thing!"}
    assert client.post("/api/projects", json={"name": "second THING!"}, headers=AS_O).status_code == 422
    assert client.post("/api/projects", json={"name": "Main"}, headers=AS_Q).status_code == 422
    me = client.get("/api/me", headers=AS_O).json()
    assert [(p["id"], p["role"], p["builds"]) for p in me["projects"]] == [("main", "receiver", True), ("second-thing", "receiver", False)]
    assert put(client, PARTNER, "giver", project="second-thing").status_code == 200
    cid = dump(client, "For the new one", who=AS_P, project_id="second-thing")[0]
    assert client.get(f"/api/cards/{cid}", headers=AS_O).json()["project_id"] == "second-thing"
    assert client.get("/api/projects/second-thing/people", headers=AS_Q).status_code == 404
    # Renaming, by its receiver only.
    assert client.put("/api/projects/second-thing/name", json={"name": "Side"}, headers=AS_O).status_code == 422
    assert client.put("/api/projects/second-thing/name", json={"name": "Next"}, headers=AS_P).status_code == 404
    assert client.put("/api/projects/second-thing/name", json={"name": "Next"}, headers=AS_O).json() == {"id": "second-thing", "name": "Next"}
    assert client_names(client, AS_P) == ["Main", "Next"]
    # A second one with the same words gets its own id.
    assert client.post("/api/projects", json={"name": "Second thing"}, headers=AS_O).json()["id"] == "second-thing-2"
    assert client.post("/api/projects", json={"name": "!!!"}, headers=AS_O).json()["id"] == "project"
