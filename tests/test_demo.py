"""Sample ideas for a try-it install."""
import pytest

from innerface import config, demo

from test_app import AS_O, AS_P, make_client


def test_it_fills_an_empty_try_it_install(tmp_path, monkeypatch, capsys):
    with make_client(tmp_path, monkeypatch) as c:
        assert demo.main([]) == 0
        assert "sample ideas" in capsys.readouterr().out
        cards = c.get("/api/cards", headers=AS_O).json()["cards"]
        assert len(cards) == len(demo.IDEAS)
        # Every stage of the board has something in it, and every card opens for both people.
        assert {x["status"] for x in cards} >= {"New", "Needs info", "Answered", "Immediate roadmap", "In dev for testing", "Done / Live", "Rejected"}
        for who in (AS_O, AS_P):
            for x in c.get("/api/cards", headers=who).json()["cards"]:
                assert c.get(f"/api/cards/{x['id']}", headers=who).status_code == 200
        # One pair of ideas says the same thing, and one idea carries what the code says.
        pairs = c.get("/api/projects/main/duplicates", headers=AS_O).json()["pairs"]
        assert [(p["fold_in"]["title"], p["keep"]["title"]) for p in pairs] == [("A night theme", "Dark mode for the dashboard")]
        assert [x["code_check"]["label"] for x in cards if x["code_check"]] == ["Partly built"]
        asked = next(x for x in cards if x["status"] == "Needs info")
        assert c.get(f"/api/cards/{asked['id']}", headers=AS_P).json()["comments"][0]["type"] == "clarification"
        # The usage page has numbers to show, from more than one place.
        u = c.get("/api/projects/main/usage", headers=AS_O).json()
        sources = {x["key"]: x["count"] for x in u["ideas"]["by_source"]}
        assert sources["mcp"] and sources["web"] and u["times"]["to_build"]["count"] and u["times"]["build_to_done"]["count"]
        assert u["ideas"]["previous"] == 1 and u["actions"]["unrecorded"] == 0
        # A scoped sample idea can still be worked on like any other.
        ready = next(x for x in cards if x["status"] == "Immediate roadmap")
        assert c.patch(f"/api/cards/{ready['id']}", json={"status": "6-month roadmap"}, headers=AS_O).status_code == 200


def test_it_never_adds_to_ideas_that_are_already_there(tmp_path, monkeypatch, capsys):
    with make_client(tmp_path, monkeypatch) as c:
        assert c.post("/api/cards/dump", json={"text": "A real idea", "as_is": True}, headers=AS_P).status_code == 200
        assert demo.main([]) == 1
        assert "already ideas here" in capsys.readouterr().err
        assert len(c.get("/api/cards", headers=AS_O).json()["cards"]) == 1
        assert demo.main(["--force"]) == 2


def test_it_refuses_on_an_install_with_sign_in(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch):
        s = config.get_settings()
    real = type(s)(**{**s.__dict__, "auth_mode": "access"})
    with pytest.raises(demo.DemoError, match="try-it install"):
        demo.fill(real)
