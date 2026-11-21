"""The build flow: stages a team defines, each judged by one of its branches."""
import pytest

from innerface import config, github, service

from test_app import AS_O, AS_P, AS_Q, H, client, dump, gh, pushes, scope  # noqa: F401

SHA = "a" * 40
PR = "https://github.com/acme/app/pull/7"
STAGES = [{"name": "Testing", "branch": "dev_2"}, {"name": "Live", "branch": "master"}]


@pytest.fixture
def repo(monkeypatch):
    """The branches GitHub shows, which branches hold the merged commit, and whether the pull request merged."""
    state = {"branches": ["master", "dev_2", "feature/x"], "holds": set(), "sha": SHA, "gone": set()}
    monkeypatch.setattr(github, "branches", lambda s, p: list(state["branches"]) if state["branches"] is not None
                        else (_ for _ in ()).throw(github.GitHubError(github.NO_BRANCHES)))
    monkeypatch.setattr(github, "merged_commit", lambda s, p, n: state["sha"])

    def has(s, p, sha, branch):
        if branch in state["gone"]:
            raise github.GitHubError("gone")
        return branch in state["holds"]

    monkeypatch.setattr(github, "branch_has", has)
    return state


def flow(c, stages=STAGES, who=AS_O, project="main"):
    return c.put(f"/api/projects/{project}/flow", json={"stages": stages}, headers=who)


def built(client, gh):
    cid = dump(client)[0]
    scope(client, cid)
    client.post(f"/api/cards/{cid}/build", headers=AS_O)
    gh["progress"].update(pr_url=PR, state="closed", pr_merged=True)
    return cid


def card(c, cid):
    return c.get(f"/api/cards/{cid}", headers=AS_P).json()


def test_a_flow_is_chosen_from_the_branches_github_shows(client, repo):
    r = client.get("/api/projects/main/flow", headers=AS_O).json()
    assert r["stages"] == [] and r["branches"] == ["master", "dev_2", "feature/x"] and r["problem"] == ""
    assert flow(client).json()["stages"] == STAGES
    assert client.get("/api/projects/main/flow", headers=AS_O).json()["stages"] == STAGES
    # A branch GitHub doesn't show can't be picked, whatever it's called.
    r = flow(client, [{"name": "Live", "branch": "main"}])
    assert r.status_code == 422 and "doesn't show a branch called main" in r.json()["detail"]


def test_a_key_that_cant_see_the_branches_says_so(client, repo):
    repo["branches"] = None
    r = client.get("/api/projects/main/flow", headers=AS_O).json()
    assert r["branches"] is None and "can't see this repo's branches" in r["problem"]
    r = flow(client)
    assert r.status_code == 422 and "can't see this repo's branches" in r.json()["detail"]
    # Taking the flow away needs no GitHub at all.
    assert flow(client, []).status_code == 200


def test_the_rules_for_stages(client, repo):
    assert flow(client, [{"name": "", "branch": "master"}]).status_code == 422
    assert flow(client, [{"name": "Live", "branch": ""}]).status_code == 422
    assert flow(client, [{"name": "Live", "branch": "master"}, {"name": "live", "branch": "dev_2"}]).status_code == 422
    assert flow(client, [{"name": "A", "branch": "master"}, {"name": "B", "branch": "master"}]).status_code == 422
    assert flow(client, [{"name": f"S{i}", "branch": "master"} for i in range(7)]).status_code == 422
    assert flow(client, [{"name": "x" * 41, "branch": "master"}]).status_code == 422


def test_only_a_receiver_sets_it_and_not_an_ai_app(client, repo):
    assert flow(client, who=AS_P).status_code == 404
    assert client.get("/api/projects/main/flow", headers=AS_P).status_code == 404
    assert flow(client, who=AS_Q).status_code == 404
    assert client.put("/api/projects/main/flow", json={"stages": STAGES}).status_code == 403
    from test_app import mcp
    assert not [t for t in mcp(client, "tools/list")["result"]["tools"] if "flow" in t["name"] or "stage" in t["name"]]


def test_a_card_moves_through_the_stages_and_is_live_at_the_last(client, pushes, gh, repo):
    flow(client)
    cid = built(client, gh)
    s = config.get_settings()
    # Merged, but not on any stage's branch yet: still being built, and not done just because the issue closed.
    service.sync_builds(s)
    assert card(client, cid)["status"] == "In dev for testing" and card(client, cid)["build_stage"] == ""
    repo["holds"] = {"dev_2"}
    pushes.clear()
    assert service.sync_builds(s) == 1
    assert card(client, cid)["build_stage"] == "Testing" and card(client, cid)["status"] == "In dev for testing"
    assert any(p[1] == "Moving along" and "now in Testing" in p[2] for p in pushes)
    # Checked again with nothing new: nothing changes, nobody is told again.
    pushes.clear()
    service.sync_builds(s)
    assert not pushes
    repo["holds"] = {"dev_2", "master"}
    service.sync_builds(s)
    assert card(client, cid)["status"] == "Done / Live" and card(client, cid)["build_stage"] == "Live"
    assert any(p[1] == "Done" for p in pushes)


def test_a_card_never_moves_back(client, pushes, gh, repo):
    flow(client)
    cid = built(client, gh)
    repo["holds"] = {"dev_2"}
    service.sync_builds(config.get_settings())
    repo["holds"] = set()
    service.sync_builds(config.get_settings())
    assert card(client, cid)["build_stage"] == "Testing"


def test_a_missing_branch_pauses_without_breaking(client, pushes, gh, repo):
    flow(client)
    cid = built(client, gh)
    repo["gone"] = {"master"}
    service.sync_builds(config.get_settings())
    assert card(client, cid)["status"] == "In dev for testing" and card(client, cid)["build_stage"] == ""


def test_without_a_flow_the_issue_closing_is_still_done(client, pushes, gh, repo):
    cid = built(client, gh)
    service.sync_builds(config.get_settings())
    assert card(client, cid)["status"] == "Done / Live"


def test_an_unmerged_pull_request_has_no_stage(client, pushes, gh, repo):
    flow(client)
    cid = built(client, gh)
    gh["progress"].update(state="open", pr_merged=False)
    repo["holds"] = {"dev_2", "master"}
    service.sync_builds(config.get_settings())
    assert card(client, cid)["build_stage"] == "" and card(client, cid)["status"] == "In dev for testing"


def test_the_stage_shows_in_the_activity_list(client, pushes, gh, repo):
    flow(client)
    cid = built(client, gh)
    repo["holds"] = {"dev_2"}
    service.sync_builds(config.get_settings())
    from innerface import db
    with db.connect(config.get_settings()) as c:
        kinds = [(r["kind"], r["detail"]) for r in c.execute("SELECT kind, detail FROM activity WHERE card_id=?", (cid,))]
    assert ("stage", "Testing") in kinds


# ----- the GitHub calls themselves -----

def _fake(monkeypatch, answers):
    """GitHub stand-in: `answers` maps a path to (status, json). Paths asked for are recorded."""
    import httpx
    from innerface import config
    asked = []

    class Fake:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, path, params=None):
            asked.append((path, params))
            status, data = answers[path]
            return httpx.Response(status, json=data)

    monkeypatch.setenv("T", "tok")
    monkeypatch.setattr(github.httpx, "Client", Fake)
    project = config.Project(id="p", name="P", givers=(), receivers=("a@x",), repo="acme/app", github_token_env="T")
    return project, type("S", (), {"github_api": "https://api.github.com"})(), asked


def test_branches_come_back_by_name_and_a_refused_token_is_explained(monkeypatch):
    project, s, asked = _fake(monkeypatch, {"/repos/acme/app/branches": (200, [{"name": "master"}, {"name": "dev_2"}])})
    assert github.branches(s, project) == ["master", "dev_2"]
    project, s, _ = _fake(monkeypatch, {"/repos/acme/app/branches": (403, {})})
    with pytest.raises(github.GitHubError, match="can't see this repo's branches"):
        github.branches(s, project)
    project, s, _ = _fake(monkeypatch, {"/repos/acme/app/branches": (404, {})})
    with pytest.raises(github.GitHubError, match="can't see this repo's branches"):
        github.branches(s, project)


def test_a_branch_holds_a_commit_only_when_it_is_ahead_or_level(monkeypatch):
    for status, held in (("ahead", True), ("identical", True), ("behind", False), ("diverged", False)):
        project, s, asked = _fake(monkeypatch, {f"/repos/acme/app/compare/{SHA}...release/1": (200, {"status": status})})
        assert github.branch_has(s, project, SHA, "release/1") is held
    project, s, _ = _fake(monkeypatch, {f"/repos/acme/app/compare/{SHA}...gone": (404, {})})
    with pytest.raises(github.GitHubError, match="renamed or deleted"):
        github.branch_has(s, project, SHA, "gone")
    assert github.branch_has(s, project, "not-a-sha", "main") is False


def test_a_merged_pull_requests_commit_is_only_given_once_it_merged(monkeypatch):
    project, s, _ = _fake(monkeypatch, {"/repos/acme/app/pulls/7": (200, {"merged": True, "merge_commit_sha": SHA})})
    assert github.merged_commit(s, project, 7) == SHA
    project, s, _ = _fake(monkeypatch, {"/repos/acme/app/pulls/7": (200, {"merged": False, "merge_commit_sha": SHA})})
    assert github.merged_commit(s, project, 7) == ""
