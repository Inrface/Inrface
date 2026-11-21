"""The free edition: its limits hold, and what it leaves out isn't there."""
import importlib.util
import pathlib

from innerface import config, plans

from test_app import AS_O, AS_P, AS_Q, client, make_client, pushes  # noqa: F401
from test_after import share
from test_connectors import BASE, LINEAR_KEY, add
from test_slack import CHANNEL, SECRET, TOKEN


def test_the_app_says_which_edition_it_is(client):
    me = client.get("/api/me", headers=AS_O).json()
    assert me["plan"]["name"] == "free" and me["plan"]["limits"] == {"projects": None, "share_links": 1, "trackers": 1, "chat_channels": 1}
    assert me["plan"]["features"]["share_links"] is True and me["build_phrase"] is False
    # Sign-offs are part of the hosted version: the switches aren't here, and the code for them isn't either.
    assert me["plan"]["features"]["approvals"] is False
    assert client.get("/api/projects/main/approvals", headers=AS_O).status_code == 404


def test_there_is_no_setting_that_changes_it(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch, PLAN="premier") as c:
        assert c.get("/api/me", headers=AS_O).json()["plan"]["name"] == "free" and plans.limit(config.get_settings(), "share_links") == 1


def test_a_project_has_one_roadmap_link_at_a_time(client, pushes):
    first = share(client, label="Acme").json()
    refused = share(client, label="Two")
    assert refused.status_code == 402 and refused.json()["detail"] == "The free edition has room for 1 roadmap link a project. Switch one off first."
    assert client.post("/api/projects/side/share", json={}, headers=AS_Q).status_code == 201
    assert client.post(f"/api/projects/main/share/{first['id']}/revoke", headers=AS_O).status_code == 200
    assert share(client, label="Two").status_code == 201


def test_a_project_has_one_tracker_and_one_chat_channel(client, pushes):
    add(client, "linear", api_key=LINEAR_KEY, team="ENG")
    refused = client.post(BASE, json={"kind": "linear", "values": {"api_key": LINEAR_KEY, "team": "WEB"}}, headers=AS_O)
    assert refused.status_code == 402 and refused.json()["detail"] == "The free edition has room for 1 tracker a project."
    add(client, "slack", channel=CHANNEL, signing_secret=SECRET, bot_token=TOKEN)
    again = client.post(BASE, json={"kind": "slack", "values": {"channel": "C0IDEAS0002", "signing_secret": SECRET, "bot_token": TOKEN}}, headers=AS_O)
    assert again.status_code == 402 and again.json()["detail"] == "The free edition has room for 1 chat channel a project."
    # A signed webhook has no limit of its own, and another project has its own room.
    add(client)
    add(client)
    add(client, "linear", who=AS_Q, base="/api/projects/side/connectors", api_key=LINEAR_KEY, team="ENG")


def test_what_this_edition_leaves_out_is_not_in_it(client):
    root = pathlib.Path(plans.__file__).parent
    assert importlib.util.find_spec("innerface.phrase") is None and not (root / "phrase.py").exists()
    for path in ("/api/me/build-phrase",):
        assert client.put(path, json={"phrase": "Purple walrus, ship it!"}, headers=AS_O).status_code in (404, 405)
    assert plans.has(config.get_settings(), "signin_addons") is False and plans.has(config.get_settings(), "build_phrase") is False
