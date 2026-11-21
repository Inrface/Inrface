"""The small slice of the GitHub REST API that Inrface needs: open an issue, read back its progress, and fetch the code."""
import contextlib
import io
import logging
import re
import time
from urllib.parse import quote

import httpx

from .config import Project, Settings

log = logging.getLogger("innerface.github")
_SHA = re.compile(r"^[0-9a-f]{40,64}$")


class GitHubError(Exception):
    """The message is safe to show."""


class CodeBlocked(GitHubError):
    """GitHub answered, and the token isn't allowed to read the code."""


def _client(s: Settings, project: Project) -> httpx.Client:
    if not project.repo:
        raise GitHubError(f"{project.name} isn't linked to a GitHub repo.")
    token = project.github_token
    if not token:
        raise GitHubError(f"The GitHub token for {project.name} isn't set up.")
    return httpx.Client(
        base_url=s.github_api,
        timeout=20.0,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Inrface",
        },
    )


def _check(r: httpx.Response, action: str) -> dict | list:
    if r.status_code in (401, 403):
        log.warning("github %s refused: %s", action, r.status_code)
        raise GitHubError("GitHub refused the request. Check the token's access to this repo.")
    if r.status_code == 404:
        raise GitHubError("GitHub couldn't find that repo, or the token can't see it.")
    if r.status_code >= 400:
        log.warning("github %s failed: %s", action, r.status_code)
        raise GitHubError("GitHub couldn't do that just now.")
    return r.json()


def create_issue(s: Settings, project: Project, title: str, body: str) -> tuple[int, str]:
    try:
        with _client(s, project) as c:
            r = c.post(f"/repos/{project.repo}/issues", json={"title": title[:250], "body": body[:60000], "labels": [project.build_label]})
    except httpx.HTTPError:
        raise GitHubError("Couldn't reach GitHub. Try again in a minute.")
    d = _check(r, "create issue")
    return int(d["number"]), str(d["html_url"])


def issue_progress(s: Settings, project: Project, number: int) -> dict:
    """Return {"state", "state_reason", "pr_url", "pr_merged"} for an issue."""
    try:
        with _client(s, project) as c:
            issue = _check(c.get(f"/repos/{project.repo}/issues/{number}"), "read issue")
            events = _check(c.get(f"/repos/{project.repo}/issues/{number}/timeline", params={"per_page": 100}), "read timeline")
    except httpx.HTTPError:
        raise GitHubError("Couldn't reach GitHub.")
    pr_url, pr_merged = "", False
    for e in events if isinstance(events, list) else []:
        src = (e.get("source") or {}).get("issue") or {}
        pr = src.get("pull_request")
        if e.get("event") == "cross-referenced" and pr:
            # Same repo only, so a mention from somewhere unrelated can't stand in for the build.
            if (src.get("repository") or {}).get("full_name", project.repo).lower() != project.repo.lower():
                continue
            pr_url = src.get("html_url") or pr_url
            pr_merged = pr_merged or bool(pr.get("merged_at"))
    return {"state": issue.get("state"), "state_reason": issue.get("state_reason"), "pr_url": pr_url, "pr_merged": pr_merged}


MAX_BRANCHES = 500
NO_BRANCHES = "Inrface can't see this repo's branches. Check the repo name, and that the token can read this repo's Contents."


def branches(s: Settings, project: Project) -> list[str]:
    """The names of the repo's branches, up to MAX_BRANCHES. Needs Contents read."""
    names: list[str] = []
    try:
        with _client(s, project) as c:
            for page in range(1, MAX_BRANCHES // 100 + 1):
                r = c.get(f"/repos/{project.repo}/branches", params={"per_page": 100, "page": page})
                if _limited(r):
                    raise GitHubError(BUSY)
                if r.status_code in (401, 403, 404):
                    log.warning("github read branches refused: %s", r.status_code)
                    raise GitHubError(NO_BRANCHES)
                rows = _check(r, "read branches")
                if not isinstance(rows, list):
                    break
                names += [str(b["name"]) for b in rows if isinstance(b, dict) and b.get("name")]
                if len(rows) < 100:
                    break
    except httpx.HTTPError:
        raise GitHubError("Couldn't reach GitHub. Try again in a minute.")
    return names


def merged_commit(s: Settings, project: Project, pr_number: int) -> str:
    """The commit a merged pull request left on the branch it went into, or "" if it hasn't merged."""
    try:
        with _client(s, project) as c:
            pr = _check(c.get(f"/repos/{project.repo}/pulls/{int(pr_number)}"), "read pull request")
    except httpx.HTTPError:
        raise GitHubError("Couldn't reach GitHub.")
    sha = str(pr.get("merge_commit_sha") or "") if isinstance(pr, dict) and pr.get("merged") else ""
    return sha if _SHA.match(sha) else ""


def branch_has(s: Settings, project: Project, sha: str, branch: str) -> bool:
    """Whether the commit is on the branch. False if the branch is behind it or has gone its own way; a branch that
    no longer exists raises."""
    if not _SHA.match(sha):
        return False
    try:
        with _client(s, project) as c:
            r = c.get(f"/repos/{project.repo}/compare/{sha}...{quote(branch, safe='/')}", params={"per_page": 1})
    except httpx.HTTPError:
        raise GitHubError("Couldn't reach GitHub.")
    if r.status_code == 404:
        raise GitHubError(f"GitHub couldn't compare with the branch {branch}. It may have been renamed or deleted.")
    d = _check(r, "compare")
    return isinstance(d, dict) and d.get("status") in ("ahead", "identical")


NO_CODE = "GitHub wouldn't show this repo's code. The project's token needs read access to Contents."
BUSY = "GitHub is limiting requests just now. Try again in a few minutes."


def _limited(r: httpx.Response) -> bool:
    return r.status_code == 429 or (r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0")


def _code_probe(c: httpx.Client, project: Project) -> str:
    """Whether the token can read the code: "yes", "no", or "empty" for a repo with no commits."""
    r = c.get(f"/repos/{project.repo}/commits", params={"per_page": 1})
    return "yes" if r.status_code == 200 else "empty" if r.status_code == 409 else "no"


def code_head(s: Settings, project: Project) -> dict:
    """The newest commit on the default branch, as {"sha", "date"}. Needs Contents read."""
    try:
        with _client(s, project) as c:
            r = c.get(f"/repos/{project.repo}/commits", params={"per_page": 1})
    except httpx.HTTPError:
        raise GitHubError("Couldn't reach GitHub. Try again in a minute.")
    if r.status_code == 409:
        raise GitHubError("That repo has no code yet.")
    if _limited(r):
        raise GitHubError(BUSY)
    if r.status_code in (401, 403, 404):
        log.warning("github read commits refused: %s", r.status_code)
        raise CodeBlocked(NO_CODE)
    rows = _check(r, "read commits")
    if not isinstance(rows, list) or not rows or not _SHA.match(str(rows[0].get("sha", ""))):
        raise GitHubError("That repo has no code yet.")
    commit = rows[0].get("commit") or {}
    return {"sha": rows[0]["sha"], "date": str((commit.get("committer") or commit.get("author") or {}).get("date") or "")[:10]}


class _Download(io.RawIOBase):
    """The body of a download, read as it arrives, so a big repo is never held whole in memory."""

    def __init__(self, chunks, limit: int, seconds: float):
        self._chunks, self._limit, self._until = chunks, limit, time.monotonic() + seconds
        self._rest, self._total = b"", 0

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        if not self._rest:
            if time.monotonic() > self._until:
                raise GitHubError("That repo took too long to download.")
            self._rest = next(self._chunks, b"")
            self._total += len(self._rest)
            if self._total > self._limit:
                raise GitHubError("That repo is too big to look through from here.")
        n = min(len(b), len(self._rest))
        b[:n], self._rest = self._rest[:n], self._rest[n:]
        return n


@contextlib.contextmanager
def code_archive(s: Settings, project: Project, sha: str, limit: int, seconds: float = 300.0):
    """The repo at one commit as a stream of a gzipped tar file. At most `limit` bytes are read, for at most `seconds`."""
    if not _SHA.match(sha):
        raise GitHubError("GitHub couldn't do that just now.")
    try:
        with _client(s, project) as c:
            # GitHub answers with a redirect to a short-lived download address. httpx drops the token when the host changes.
            with c.stream("GET", f"/repos/{project.repo}/tarball/{sha}", follow_redirects=True, timeout=60.0) as r:
                if _limited(r):
                    raise GitHubError(BUSY)
                if r.status_code in (401, 403, 404):
                    log.warning("github download refused: %s", r.status_code)
                    raise CodeBlocked(NO_CODE)
                if r.status_code != 200:
                    log.warning("github download failed: %s", r.status_code)
                    raise GitHubError("GitHub couldn't do that just now.")
                yield _Download(r.iter_bytes(65536), limit, seconds)
    except httpx.HTTPError:
        raise GitHubError("Couldn't reach GitHub. Try again in a minute.")


def recent_work(s: Settings, project: Project, limit: int = 15) -> dict:
    """What changed lately: {"pull_requests": [...], "commits": [...]}, newest first. Commits are left out if the token can't read the code."""
    try:
        with _client(s, project) as c:
            pulls = _check(c.get(f"/repos/{project.repo}/pulls", params={"state": "all", "sort": "updated", "direction": "desc", "per_page": limit}), "read pull requests")
            r = c.get(f"/repos/{project.repo}/commits", params={"per_page": limit})
    except httpx.HTTPError:
        raise GitHubError("Couldn't reach GitHub. Try again in a minute.")
    commits = r.json() if r.status_code == 200 else []
    return {
        "pull_requests": [{"number": p.get("number"), "title": str(p.get("title") or "")[:150],
                           "state": "merged" if p.get("merged_at") else str(p.get("state") or ""), "updated": str(p.get("updated_at") or "")[:10]}
                          for p in (pulls if isinstance(pulls, list) else []) if isinstance(p, dict)],
        "commits": [{"what": str((c_.get("commit") or {}).get("message") or "").strip().split("\n")[0][:150],
                     "when": str(((c_.get("commit") or {}).get("committer") or {}).get("date") or "")[:10]}
                    for c_ in (commits if isinstance(commits, list) else []) if isinstance(c_, dict)],
    }


def look(s: Settings, project: Project) -> dict:
    """Confirm the token can read the repo, its issues and its pull requests, changing nothing."""
    try:
        with _client(s, project) as c:
            repo = _check(c.get(f"/repos/{project.repo}"), "read repo")
            _check(c.get(f"/repos/{project.repo}/issues", params={"per_page": 1}), "read issues")
            _check(c.get(f"/repos/{project.repo}/pulls", params={"per_page": 1}), "read pull requests")
            r = c.get(f"/repos/{project.repo}/labels/{quote(project.build_label, safe='')}")
            if r.status_code != 404:
                _check(r, "read label")
            code = _code_probe(c, project)
    except httpx.HTTPError:
        raise GitHubError("Couldn't reach GitHub. Try again in a minute.")
    return {"repo": repo.get("full_name", project.repo), "label": "missing" if r.status_code == 404 else "found", "code": code}


def check(s: Settings, project: Project) -> dict:
    """Confirm the token can see the repo, and make sure the build label exists (which needs Issues write)."""
    try:
        with _client(s, project) as c:
            repo = _check(c.get(f"/repos/{project.repo}"), "read repo")
            r = c.get(f"/repos/{project.repo}/labels/{quote(project.build_label, safe='')}")
            if r.status_code == 404:
                _check(c.post(f"/repos/{project.repo}/labels", json={
                    "name": project.build_label, "color": "0e8a16",
                    "description": "Scoped in Inrface; the build agent picks this up"}), "create label")
                label = "created"
            else:
                _check(r, "read label")
                label = "found"
            _check(c.get(f"/repos/{project.repo}/pulls", params={"per_page": 1}), "read pull requests")
            code = _code_probe(c, project)
    except httpx.HTTPError:
        raise GitHubError("Couldn't reach GitHub. Try again in a minute.")
    return {"repo": repo.get("full_name", project.repo), "label": label, "code": code}
