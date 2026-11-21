"""An idea's history: who did what, in order, open to everyone who can see the idea."""
from test_app import AS_O, AS_P, AS_Q, client, dump, gh, pushes, scope  # noqa: F401


def history(c, cid, who=AS_O):
    return c.get(f"/api/cards/{cid}", headers=who).json()["history"]


def test_an_idea_keeps_a_history_everyone_on_the_project_can_read(client, pushes, gh):
    cid = dump(client, "A weekly report", as_is=True)[0]
    scope(client, cid)
    client.patch(f"/api/cards/{cid}", json={"status": "Immediate roadmap"}, headers=AS_O)
    client.post(f"/api/cards/{cid}/build", headers=AS_O)
    rows = history(client, cid)
    assert [(r["who"], r["kind"]) for r in rows] == [("Pat", "created"), ("Olly", "scoped"), ("Olly", "status"), ("Olly", "build")]
    assert rows[2]["what"] == "Immediate roadmap" and rows[0]["at"] <= rows[3]["at"]
    # A giver sees the same names and steps, and nobody sees an address.
    seen = history(client, cid, AS_P)
    assert [r["kind"] for r in seen] == [r["kind"] for r in rows] and "@" not in str(seen)
    assert client.get(f"/api/cards/{cid}", headers=AS_Q).status_code == 404


def test_comment_words_stay_in_the_comments_and_an_answer_is_marked(client, pushes):
    cid = dump(client, "A weekly report", as_is=True)[0]
    client.patch(f"/api/cards/{cid}", json={"status": "Needs info", "message": "Which day?"}, headers=AS_O)
    client.post(f"/api/cards/{cid}/comments", json={"kind": "comment", "text": "Mondays, secret plan"}, headers=AS_P)
    got = client.get(f"/api/cards/{cid}", headers=AS_O).json()
    assert got["status"] == "Answered" and "secret" not in str(got["history"])
    assert [r["kind"] for r in got["history"]][-2:] == ["status", "answered"]
