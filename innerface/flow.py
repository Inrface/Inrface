"""The build flow: how code moves from a merged pull request to live, as this project's team describes it.

A receiver names the stages and picks, for each, the branch it is judged by. A card is in a stage once the commit its
pull request merged as is on that branch. The last stage is live: when the card gets there it becomes Done / Live.
Without a flow, a card is done when its issue closes with the pull request merged, as before.

Only the web app changes a flow. Branch names are chosen from the ones GitHub shows the token, so a team whose key
can't see the repo finds out here and not later.
"""
import json
import logging

from . import db, github
from .config import Project, Settings

log = logging.getLogger("innerface")

MAX_STAGES = 6
MAX_NAME = 40


def _error(status: int, message: str):
    from .service import ServiceError
    return ServiceError(status, message)


def stages(s: Settings, project_id: str) -> list[dict]:
    with db.connect(s) as c:
        row = c.execute("SELECT stages FROM project_flow WHERE project_id=?", (project_id,)).fetchone()
    try:
        rows = json.loads(row["stages"]) if row else []
    except ValueError:
        return []
    return [{"name": str(x["name"]), "branch": str(x["branch"])} for x in rows if isinstance(x, dict) and x.get("name") and x.get("branch")]


def view(s: Settings, project: Project) -> dict:
    """What the Settings screen shows: the stages, and the branches GitHub shows the token (None, with the reason, if none)."""
    out = {"project_id": project.id, "stages": stages(s, project.id), "branches": None, "problem": ""}
    if not project.repo or not project.github_token:
        out["problem"] = "Add the GitHub repo and a token above first. Then the branches show up here."
        return out
    try:
        out["branches"] = github.branches(s, project)
    except github.GitHubError as e:
        out["problem"] = str(e)
    return out


def save(s: Settings, user: dict, project: Project, rows: list[dict]) -> dict:
    clean, names, branches_used = [], set(), set()
    for x in rows:
        name = " ".join(str(x.get("name", "")).split())
        branch = str(x.get("branch", "")).strip()
        if not name or len(name) > MAX_NAME or any(ord(ch) < 32 for ch in name):
            raise _error(422, f"Each stage needs a name of up to {MAX_NAME} characters")
        if not branch:
            raise _error(422, f"Pick a branch for {name}")
        if name.lower() in names:
            raise _error(422, f"Two stages are called {name}")
        if branch in branches_used:
            raise _error(422, f"{branch} is used by two stages. Each stage needs its own branch.")
        names.add(name.lower())
        branches_used.add(branch)
        clean.append({"name": name, "branch": branch})
    if len(clean) > MAX_STAGES:
        raise _error(422, f"A flow can have up to {MAX_STAGES} stages")
    if clean:
        seen = view(s, project)
        if seen["branches"] is None:
            raise _error(422, seen["problem"])
        missing = [x["branch"] for x in clean if x["branch"] not in seen["branches"]]
        if missing:
            raise _error(422, f"GitHub doesn't show a branch called {missing[0]}. Pick one from the list.")
    with db.connect(s) as c:
        c.execute("INSERT INTO project_flow(project_id, stages, updated_by, updated_at) VALUES (?,?,?,?) "
                  "ON CONFLICT(project_id) DO UPDATE SET stages=excluded.stages, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                  (project.id, json.dumps(clean), user["email"], db.now()))
    log.info("build flow for %s changed by %s: %d stages", project.id, user["email"], len(clean))
    return view(s, project)


def reached(s: Settings, project: Project, pr_number: int, flow: list[dict]) -> int:
    """The furthest stage the pull request's code has reached, as a position in the flow, or -1 if none yet."""
    sha = github.merged_commit(s, project, pr_number)
    if not sha:
        return -1
    for i in range(len(flow) - 1, -1, -1):
        if github.branch_has(s, project, sha, flow[i]["branch"]):
            return i
    return -1
