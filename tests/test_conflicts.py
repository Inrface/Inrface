"""A build whose pull request can't merge: the card says so, and people are told when it's ready again or needs a person."""
from innerface import config, service

from test_app import AS_O, AS_P, client, dump, gh, pushes, scope, OWNER  # noqa: F401


def started(client, gh):
    cid = dump(client)[0]
    scope(client, cid)
    client.post(f"/api/cards/{cid}/build", headers=AS_O)
    gh["progress"].update(pr_url="https://github.com/acme/app/pull/7")
    return cid


def card(c, cid):
    return c.get(f"/api/cards/{cid}", headers=AS_O).json()


def runner_said(gh, id, kind):
    gh["progress"]["conflicts"] = {"id": id, "kind": kind}


def test_the_card_shows_fixing_then_tells_the_receiver_when_it_is_ready_to_test_again(client, pushes, gh):
    cid = started(client, gh)
    s = config.get_settings()
    service.sync_builds(s)
    pushes.clear()
    runner_said(gh, "10", "fixing")
    service.sync_builds(s)
    assert card(client, cid)["conflict"] == "fixing" and not [p for p in pushes if p[1] in ("Ready to test again", "Needs a person")]
    pushes.clear()
    service.sync_builds(s)   # the same comment again: nothing new
    assert not pushes
    runner_said(gh, "11", "fixed")
    service.sync_builds(s)
    assert card(client, cid)["conflict"] == ""
    assert [p for p in pushes if p[1] == "Ready to test again"] == [(OWNER, "Ready to test again", card(client, cid)["title"] + ": the conflicts are fixed.", cid)]


def test_a_fix_that_finished_between_two_checks_is_still_announced(client, pushes, gh):
    cid = started(client, gh)
    s = config.get_settings()
    pushes.clear()
    runner_said(gh, "12", "fixed")
    service.sync_builds(s)
    assert any(p[1] == "Ready to test again" for p in pushes) and card(client, cid)["conflict"] == ""


def test_when_the_agent_gives_up_the_card_says_a_person_is_needed_and_the_receiver_is_alerted(client, pushes, gh):
    cid = started(client, gh)
    s = config.get_settings()
    service.sync_builds(s)
    pushes.clear()
    runner_said(gh, "20", "stuck")
    service.sync_builds(s)
    assert card(client, cid)["conflict"] == "stuck"
    assert [p[:2] for p in pushes if p[1] == "Needs a person"] == [(OWNER, "Needs a person")]


def test_a_merged_pull_request_clears_it(client, pushes, gh):
    cid = started(client, gh)
    s = config.get_settings()
    runner_said(gh, "30", "stuck")
    service.sync_builds(s)
    assert card(client, cid)["conflict"] == "stuck"
    gh["progress"].update(state="closed", state_reason="completed", pr_merged=True)
    service.sync_builds(s)
    got = card(client, cid)
    assert got["status"] == "Done / Live" and got["conflict"] == ""
