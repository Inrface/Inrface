"""After an idea ships: the note to whoever had it, the week-by-week list, the catch-up for AI apps, and the
read-only roadmap link for someone without a login."""
import re

from innerface import config, db, service

from test_app import AS_O, AS_P, AS_Q, OWNER, PARTNER, client, dump, gh, pushes, scope, tool  # noqa: F401

LIVE = "https://app.example.com"


def card(c, cid, who=AS_O):
    return c.get(f"/api/cards/{cid}", headers=who).json()


def file(c, cid, status, who=AS_O):
    r = c.patch(f"/api/cards/{cid}", json={"status": status}, headers=who)
    assert r.status_code == 200, r.text


def set_live(c, url=LIVE):
    return c.put("/api/projects/main/github", json={"live_url": url}, headers=AS_O)


def share(c, who=AS_O, **body):
    return c.post("/api/projects/main/share", json=body, headers=who)


# ----- the note to whoever had the idea -----

def test_the_giver_hears_their_idea_is_live_with_a_way_to_try_it(client, pushes, gh):
    assert set_live(client).json()["live_url"] == LIVE
    cid = dump(client, "A weekly report")[0]
    scope(client, cid)
    client.post(f"/api/cards/{cid}/build", headers=AS_O)
    gh["progress"].update(state="closed", pr_merged=True)
    pushes.clear()
    service.sync_builds(config.get_settings())
    told = {p[0]: p for p in pushes}
    assert told[PARTNER][1] == "Your idea is live" and "ready for you to try" in told[PARTNER][2] and told[PARTNER][3] == cid
    assert told[OWNER][1] == "Done"
    c = card(client, cid, AS_P)
    assert c["status"] == "Done / Live" and c["shipped_at"]
    assert [p["live_url"] for p in client.get("/api/me", headers=AS_P).json()["projects"]] == [LIVE]


def test_filing_as_done_by_hand_tells_the_giver_too_and_says_so_without_a_link(client, pushes):
    cid = dump(client, "Dark mode")[0]
    pushes.clear()
    file(client, cid, "Done / Live")
    assert [(p[0], p[1]) for p in pushes] == [(PARTNER, "Your idea is live")]
    assert "has shipped" in pushes[0][2]


def test_the_day_it_went_live_survives_a_later_note_and_goes_when_it_is_moved_back(client, pushes):
    cid = dump(client, "Dark mode")[0]
    file(client, cid, "Done / Live")
    first = card(client, cid)["shipped_at"]
    client.patch(f"/api/cards/{cid}", json={"message": "Nice work"}, headers=AS_O)
    assert card(client, cid)["shipped_at"] == first
    file(client, cid, "Immediate roadmap")
    assert card(client, cid)["shipped_at"] is None


def test_a_receiver_sets_a_link_for_one_idea_and_a_giver_cannot(client, pushes):
    cid = dump(client, "Dark mode")[0]
    assert client.put(f"/api/cards/{cid}/try-link", json={"url": "https://app.example.com/settings#dark"}, headers=AS_P).status_code == 403
    assert client.put(f"/api/cards/{cid}/try-link", json={"url": "https://app.example.com/settings#dark"}, headers=AS_O).status_code == 200
    assert card(client, cid, AS_P)["try_url"].endswith("#dark")
    for bad in ("http://app.example.com", "javascript:alert(1)", "https://", "https://a b.example.com", "https://user:pw@example.com", "//example.com", 'https://example.com/"onclick'):
        assert client.put(f"/api/cards/{cid}/try-link", json={"url": bad}, headers=AS_O).status_code == 422, bad
        assert set_live(client, bad).status_code == 422, bad
    assert client.put(f"/api/cards/{cid}/try-link", json={"url": ""}, headers=AS_O).json() == {"try_url": ""}


# ----- what shipped, week by week -----

def test_what_shipped_is_listed_by_week_for_everyone_in_the_project(client, pushes):
    set_live(client)
    new, old, open_ = dump(client, "Dark mode")[0], dump(client, "CSV export")[0], dump(client, "Still open")[0]
    file(client, new, "Done / Live")
    file(client, old, "Done / Live")
    with db.connect(config.get_settings()) as c:
        c.execute("UPDATE cards SET shipped_at=? WHERE id=?", (db.ago(20 * 86400), old))
    for who in (AS_O, AS_P):
        d = client.get("/api/shipped", headers=who).json()
        assert d["count"] == 2 and len(d["weeks"]) == 2
        assert [i["title"] for w in d["weeks"] for i in w["items"]] == [card(client, new)["title"], card(client, old)["title"]]
        assert all(i["try_url"] == LIVE and i["from"] == "Pat" for w in d["weeks"] for i in w["items"])
        assert d["weeks"][0]["week_of"] > d["weeks"][1]["week_of"]
        assert "Week of" in d["text"] and "Try it: " + LIVE in d["text"] and "Still open" not in d["text"]
    assert client.get("/api/shipped?weeks=1", headers=AS_O).json()["count"] == 1
    # Someone in another project sees none of it.
    assert client.get("/api/shipped", headers=AS_Q).json()["count"] == 0
    assert open_


def test_an_ai_app_can_read_out_what_shipped(client, pushes):
    cid = dump(client, "Dark mode")[0]
    file(client, cid, "Done / Live")
    out = tool(client, "whats_shipped", who=PARTNER)
    assert out["count"] == 1 and out["weeks"][0]["shipped"][0]["yours"] is True
    assert "try_it_at" not in out["weeks"][0]["shipped"][0]
    set_live(client)
    assert tool(client, "whats_shipped", who=PARTNER)["weeks"][0]["shipped"][0]["try_it_at"] == LIVE
    assert tool(client, "get_card", who=PARTNER, card_id=cid)["try_it_at"] == LIVE


# ----- the catch-up -----

def test_a_receiver_asks_whats_new(client, pushes, gh):
    waiting, building, done = dump(client, "Dark mode")[0], dump(client, "A weekly report")[0], dump(client, "CSV export")[0]
    scope(client, building)
    client.post(f"/api/cards/{building}/build", headers=AS_O)
    file(client, done, "Done / Live")
    out = tool(client, "whats_new")
    assert out["covers"] == "the last 7 days"
    assert [c["id"] for c in out["needs_them"]["cards"]] == [waiting]
    assert out["new_ideas"]["count"] == 2  # the one waiting and the one being built; the shipped one is told as shipped
    assert [c["id"] for c in out["being_built"]["cards"]] == [building]
    assert [c["id"] for c in out["went_live"]["cards"]] == [done]
    # Asked again in the same conversation, the answer is the same, not empty.
    again = tool(client, "whats_new")
    assert again["went_live"]["count"] == 1 and again["covers"] == "since you last asked"


def test_a_later_catch_up_starts_where_the_last_one_ended(client, pushes):
    old = dump(client, "Dark mode")[0]
    tool(client, "whats_new")
    with db.connect(config.get_settings()) as c:  # an hour passes
        c.execute("UPDATE users SET caught_up_at=?, caught_up_from=? WHERE email=?", (db.ago(3600), db.ago(8 * 86400), OWNER))
        c.execute("UPDATE cards SET created_at=? WHERE id=?", (db.ago(7200), old))
    fresh = dump(client, "CSV export")[0]
    out = tool(client, "whats_new")
    assert [c["id"] for c in out["new_ideas"]["cards"]] == [fresh]
    assert out["needs_them"]["count"] == 2  # what waits on them is always said, however old
    assert tool(client, "whats_new", days=30)["new_ideas"]["count"] == 2


def test_a_giver_hears_about_their_own_ideas_and_nothing_private(client, pushes):
    asked, moved, live = dump(client, "Dark mode")[0], dump(client, "CSV export")[0], dump(client, "A weekly report")[0]
    client.patch(f"/api/cards/{asked}", json={"status": "Needs info", "message": "Which pages?"}, headers=AS_O)
    file(client, moved, "6-month roadmap")
    file(client, live, "Done / Live")
    mine = dump(client, "Olly's own idea", who=AS_O)[0]
    out = tool(client, "whats_new", who=PARTNER)
    assert [c["id"] for c in out["needs_them"]["cards"]] == [asked]
    assert out["needs_them"]["cards"][0]["question_waiting"] == "Which pages?"
    assert [c["id"] for c in out["their_ideas_that_moved"]["cards"]] == [moved]
    assert [c["id"] for c in out["went_live"]["cards"]] == [live]
    assert [c["id"] for c in out["new_ideas"]["cards"]] == [mine]
    assert OWNER not in str(out) and PARTNER not in str(out)
    quiet = tool(client, "whats_new", who="quinn@example.com")
    assert quiet["tell_the_user"].startswith("Nothing new")


# ----- showing the roadmap to someone outside -----

def test_a_roadmap_link_shows_titles_and_nothing_else(client, pushes, gh):
    filed, building, done, new, no = (dump(client, t)[0] for t in ("Dark mode", "A weekly report", "CSV export", "Not filed yet", "Turned down"))
    file(client, filed, "Immediate roadmap")
    scope(client, building, summary="Weekly report", scope="SECRET-SCOPE email only", acceptance=["Sends on Monday"])
    client.post(f"/api/cards/{building}/build", headers=AS_O)
    client.post(f"/api/cards/{building}/comments", json={"kind": "comment", "text": "SECRET-COMMENT"}, headers=AS_P)
    file(client, done, "Done / Live")
    file(client, no, "Rejected")
    r = share(client, label="Acme Ltd")
    assert r.status_code == 201
    path = r.json()["path"]
    assert re.fullmatch(r"/share/[A-Za-z0-9_-]{40,}", path)
    page = client.get(path)  # no login at all
    assert page.status_code == 200
    assert page.headers["x-robots-tag"] == "noindex, nofollow" and page.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in page.headers["content-security-policy"]
    html = page.text
    for title in ("Dark mode", "A weekly report", "CSV export"):
        assert card(client, {"Dark mode": filed, "A weekly report": building, "CSV export": done}[title])["title"] in html
    for hidden in ("Not filed yet", "Turned down", "SECRET", "Pat", "Olly", "example.com", "github.com", "acme/app", "Weekly report"):
        assert hidden not in html, hidden
    assert "<script" not in html
    # The link itself is stored only as a hash, and listed without it.
    listed = client.get("/api/projects/main/share", headers=AS_O).json()["links"]
    assert listed[0]["label"] == "Acme Ltd" and listed[0]["opens"] == 1 and "path" not in listed[0]
    with db.connect(config.get_settings()) as c:
        assert path.split("/")[-1] not in str([tuple(x) for x in c.execute("SELECT * FROM share_links").fetchall()])


def test_summaries_show_only_when_asked_for_and_text_is_escaped(client, pushes):
    cid = dump(client, "Dark mode")[0]
    scope(client, cid, title="Dark <b>mode</b>", summary="Easier on the eyes & <script>alert(1)</script>")
    file(client, cid, "Immediate roadmap")
    plain = client.get(share(client).json()["path"]).text
    assert "Easier on the eyes" not in plain and "Dark &lt;b&gt;mode&lt;/b&gt;" in plain
    full = client.get(share(client, summaries=True).json()["path"]).text
    assert "Easier on the eyes &amp; &lt;script&gt;" in full and "<script" not in full
    # Text that had personal details removed by machine keeps its summary back.
    with db.connect(config.get_settings()) as c:
        c.execute("UPDATE cards SET flagged=1 WHERE id=?", (cid,))
    assert "Easier on the eyes" not in client.get(share(client, summaries=True).json()["path"]).text


def test_a_link_stops_when_it_is_switched_off_or_runs_out(client, pushes):
    made = share(client, days=7).json()
    assert client.get(made["path"]).status_code == 200
    with db.connect(config.get_settings()) as c:
        c.execute("UPDATE share_links SET expires_at=? WHERE id=?", (db.ago(60), made["id"]))
    gone = client.get(made["path"])
    assert gone.status_code == 404 and "isn't working" in gone.text
    other = share(client, days=0).json()
    assert other["expires_at"] is None and client.get(other["path"]).status_code == 200
    assert client.post(f"/api/projects/main/share/{other['id']}/revoke", headers=AS_O).status_code == 200
    assert client.get(other["path"]).status_code == 404
    assert client.get("/share/" + "a" * 43).status_code == 404
    assert client.get("/share/short").status_code == 404
    assert client.post(f"/api/projects/main/share/{other['id']}/revoke", headers=AS_O).status_code == 404


def test_only_a_receiver_of_that_project_makes_or_sees_links(client, pushes):
    made = share(client).json()
    for who in (AS_P, AS_Q):
        assert share(client, who=who).status_code == 404
        assert client.get("/api/projects/main/share", headers=who).status_code == 404
        assert client.post(f"/api/projects/main/share/{made['id']}/revoke", headers=who).status_code == 404
    assert share(client, days=3).status_code == 422
    assert client.post("/api/projects/main/share", json={}, headers={"X-Dev-User": OWNER}).status_code == 403  # no custom header
    for _ in range(19):
        assert share(client).status_code == 201
    assert share(client).status_code == 422


def test_everything_the_page_loads_sits_under_its_own_path(client, pushes):
    # An install with a sign-in in front of it opens /share/ and nothing else, so the page can't lean on other files.
    html = client.get(share(client).json()["path"]).text
    loaded = re.findall(r'(?:href|src)="([^"]+)"', html)
    assert loaded and all(u.startswith("/share/-/") for u in loaded)
    for u in loaded:
        r = client.get(u)
        assert r.status_code == 200 and r.headers.get("cache-control") != "no-store", u
    assert client.get("/share/-/app.js").status_code == 404
    assert client.get("/share/-/..%2Fapp.js").status_code == 404
    assert all(u.startswith("/share/-/") for u in re.findall(r'(?:href|src)="([^"]+)"', client.get("/share/" + "a" * 43).text))
