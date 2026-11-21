"""The setup check: what it says for a good install and for each way one goes wrong."""
import io

import httpx
import pytest

from innerface import check, config, github

AUD = "ab12cd34" + "0" * 56
OTHER = "ffff0000" + "1" * 56
URL = "https://ideas.example.org"

CONFIG = """
[people.olly]
email = "olly@example.org"
name = "Olly"

[projects.main]
name = "Main"
receivers = ["olly"]
repo = "acme/app"
github_token_env = "TEST_GH_TOKEN"
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    def set_up(text=CONFIG, **over):
        cfg = tmp_path / "innerface.toml"
        cfg.write_text(text)
        base = dict(AUTH_MODE="access", CF_ACCESS_TEAM="team", CF_ACCESS_AUD=AUD, PUBLIC_URL=URL, INNERFACE_CONFIG=str(cfg),
                    DATA_DIR=str(tmp_path), TEST_GH_TOKEN="ghtoken", PUSH_CONTACT="a@example.org", INNERFACE_SECRET_KEY="k" * 40,
                    AI_PROVIDER="none")
        base.update(over)
        for k in ("DEV_USER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_MODEL", "AI_DAILY_LIMIT", "GITHUB_POLL_SECONDS"):
            monkeypatch.delenv(k, raising=False)
        for k, v in base.items():
            monkeypatch.setenv(k, v)
    monkeypatch.setattr(github, "look", lambda s, p: {"repo": p.repo, "label": "found", "code": "yes"})
    yield set_up
    config.get_settings.cache_clear()


def cloudflare(kid=AUD, team="team", certs=200, page=302, oauth=True, seen=None):
    """A stand-in for Cloudflare: the team's keys, the sign-in redirect and the MCP sign-in answer."""
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.host.endswith(".cloudflareaccess.com"):
            return httpx.Response(certs, json={"keys": []})
        if request.url.path == "/mcp":
            if oauth:
                return httpx.Response(401, headers={"www-authenticate": f'Bearer realm="OAuth", resource_metadata="{URL}/.well-known/x/mcp"'})
            return httpx.Response(302, headers={"location": f"https://{team}.cloudflareaccess.com/cdn-cgi/access/login/x?kid={kid}"})
        if page == 302:
            return httpx.Response(302, headers={"location": f"https://{team}.cloudflareaccess.com/cdn-cgi/access/login/ideas.example.org?kid={kid}&redirect_url=%2F"})
        return httpx.Response(page, text="<html>")
    return httpx.Client(transport=httpx.MockTransport(handler))


def levels(lines):
    return [l.level for l in lines]


def said(lines, level, words):
    return any(l.level == level and words in l.text for l in lines)


def test_a_good_install_has_nothing_to_fix(env):
    env()
    seen = []
    lines = check.run(http=cloudflare(seen=seen))
    assert check.FIX not in levels(lines) and check.NOTE not in levels(lines)
    assert said(lines, check.OK, "asks people to sign in through the right Access application")
    assert said(lines, check.OK, "AI apps can sign in")
    assert said(lines, check.OK, "the token can read acme/app")
    out = io.StringIO()
    assert check.report(lines, out) == 0 and "Nothing to fix." in out.getvalue()
    # It asks, and sends nothing of its own: no token, no audience, no cookie.
    for r in seen:
        assert "authorization" not in r.headers and "cookie" not in r.headers and AUD not in str(r.url)


def test_it_never_prints_the_whole_audience_or_a_token(env):
    env()
    out = io.StringIO()
    check.report(check.run(http=cloudflare(kid=OTHER)), out)
    text = out.getvalue()
    assert AUD not in text and OTHER not in text and "ghtoken" not in text and "k" * 40 not in text
    assert "ffff0000" in text and "ab12cd34" in text


def test_a_different_access_application_is_named(env):
    env()
    lines = check.run(http=cloudflare(kid=OTHER))
    assert said(lines, check.FIX, "covered by a different Access application (its tag starts ffff0000, and CF_ACCESS_AUD starts ab12cd34)")
    out = io.StringIO()
    assert check.report(lines, out) == 1 and "1 thing to fix." in out.getvalue()


def test_an_address_with_no_sign_in_is_caught(env):
    env()
    lines = check.run(http=cloudflare(page=200))
    assert said(lines, check.FIX, "doesn't ask anyone to sign in")
    assert not said(lines, check.OK, "AI apps can sign in")


def test_a_dead_tunnel_points_at_its_log(env):
    env()
    lines = check.run(http=cloudflare(page=530))
    assert any(l.level == check.FIX and "docker compose logs tunnel" in l.todo for l in lines)


def test_another_team_is_caught(env):
    env()
    assert said(check.run(http=cloudflare(team="other")), check.FIX, "behind a different Cloudflare team (other)")


def test_managed_oauth_off_is_caught(env):
    env()
    lines = check.run(http=cloudflare(oauth=False))
    assert said(lines, check.OK, "asks people to sign in")
    assert said(lines, check.FIX, "AI apps can't sign in")


def test_an_unknown_team_is_caught(env):
    env()
    assert said(check.run(http=cloudflare(certs=404)), check.FIX, 'Cloudflare has no team called "team"')


@pytest.mark.parametrize("over, words", [
    (dict(CF_ACCESS_TEAM="team.cloudflareaccess.com"), "CF_ACCESS_TEAM should be the team name alone"),
    (dict(CF_ACCESS_TEAM="https://team"), "CF_ACCESS_TEAM should be the team name alone"),
    (dict(CF_ACCESS_AUD="not-a-tag"), "CF_ACCESS_AUD doesn't look like"),
])
def test_misshapen_cloudflare_settings_stop_before_the_network(env, over, words):
    env(**over)
    seen = []
    lines = check.run(http=cloudflare(seen=seen))
    assert said(lines, check.FIX, words) and not seen


def test_unreachable_address_and_network(env):
    env()

    def down(request):
        if request.url.host.endswith(".cloudflareaccess.com"):
            return httpx.Response(200, json={"keys": []})
        raise httpx.ConnectError("no route")
    lines = check.run(http=httpx.Client(transport=httpx.MockTransport(down)))
    assert said(lines, check.FIX, f"{URL} can't be reached")

    def offline(request):
        raise httpx.ConnectError("no route")
    assert said(check.run(http=httpx.Client(transport=httpx.MockTransport(offline))), check.FIX, "Cloudflare can't be reached from here")


def test_settings_problems_are_said_in_plain_words(env, tmp_path, monkeypatch):
    env(text="[people.a]\nname='A'\n")
    lines = check.run(http=cloudflare())
    assert levels(lines) == [check.FIX] and "people.a needs an email" in lines[0].text

    env(CF_ACCESS_AUD="")
    assert "CF_ACCESS_TEAM and CF_ACCESS_AUD are required" in check.run(http=cloudflare())[0].text

    env(AI_DAILY_LIMIT="lots")
    assert "whole numbers" in check.run(http=cloudflare())[0].text

    # Docker leaves a folder where the settings file should be when the file was missing at start.
    env()
    (tmp_path / "innerface.toml").unlink()
    (tmp_path / "innerface.toml").mkdir()
    assert "is a folder, not a file" in check.run(http=cloudflare())[0].text


def test_example_people_and_missing_extras(env):
    env(text=CONFIG.replace("olly@example.org", "alex@example.com"), INNERFACE_SECRET_KEY="", PUSH_CONTACT="", PUBLIC_URL="",
        AI_PROVIDER="anthropic")
    lines = check.run(http=cloudflare())
    assert said(lines, check.FIX, "alex@example.com is still in the settings file from the example")
    assert said(lines, check.NOTE, "INNERFACE_SECRET_KEY is empty")
    assert said(lines, check.NOTE, "PUSH_CONTACT is empty")
    assert said(lines, check.NOTE, "PUBLIC_URL is empty")
    assert said(lines, check.FIX, "ANTHROPIC_API_KEY is empty")


def test_github_token_missing_refused_and_label_missing(env, monkeypatch):
    env(TEST_GH_TOKEN="")
    lines = check.run(http=cloudflare())
    assert said(lines, check.FIX, "Main names the repo acme/app, but TEST_GH_TOKEN is empty in .env")

    env()

    def refused(s, p):
        raise github.GitHubError("GitHub refused the request. Check the token's access to this repo.")
    monkeypatch.setattr(github, "look", refused)
    assert said(check.run(http=cloudflare()), check.FIX, "Main (acme/app): GitHub refused the request")

    monkeypatch.setattr(github, "look", lambda s, p: {"repo": p.repo, "label": "missing", "code": "no"})
    lines = check.run(http=cloudflare())
    assert said(lines, check.NOTE, 'the repo has no "ready-to-build" label yet') and check.FIX not in levels(lines)
    assert said(lines, check.NOTE, "Main: the token can't read the code")

    monkeypatch.setattr(github, "look", lambda s, p: {"repo": p.repo, "label": "found", "code": "yes"})
    assert said(check.run(http=cloudflare()), check.OK, "Main: the token can read the code")


def test_a_repo_and_token_saved_in_settings_are_the_ones_checked(env, monkeypatch):
    env(TEST_GH_TOKEN="")
    from innerface import db, vault
    s = config.get_settings.__wrapped__()
    db.init(s)
    with db.connect(s) as c:
        c.execute("INSERT INTO project_github(project_id, repo, token, build_label, updated_by, updated_at) VALUES (?,?,?,?,?,?)",
                  ("main", "acme/other", vault.seal(s, "saved-token-0123456789"), "", "olly@example.org", db.now()))
    looked = []
    monkeypatch.setattr(github, "look", lambda s, p: looked.append((p.repo, p.github_token)) or {"repo": p.repo, "label": "found", "code": "yes"})
    lines = check.run(http=cloudflare())
    assert looked == [("acme/other", "saved-token-0123456789")] and check.FIX not in levels(lines)


def test_offline_and_dev_mode_skip_the_network(env, monkeypatch):
    env()
    monkeypatch.setattr(github, "look", lambda s, p: pytest.fail("offline must not call GitHub"))
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: pytest.fail("offline must not open a connection"))
    assert check.FIX not in levels(check.run(offline=True))

    env(AUTH_MODE="dev", CF_ACCESS_AUD="", CF_ACCESS_TEAM="")
    lines = check.run(offline=True)
    assert said(lines, check.NOTE, "AUTH_MODE is dev")


def test_look_reads_and_never_writes(monkeypatch):
    project = config.Project(id="p", name="P", givers=(), receivers=("a@x",), repo="acme/app", github_token_env="T")
    monkeypatch.setenv("T", "tok")
    calls = []

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, path, params=None):
            calls.append(path)
            if "/labels/" in path:
                return httpx.Response(404, json={})
            return httpx.Response(200, json={"full_name": "acme/app"} if path == "/repos/acme/app" else [])
        def post(self, *a, **k):
            raise AssertionError("the setup check must not write to GitHub")

    monkeypatch.setattr(github.httpx, "Client", FakeClient)
    s = type("S", (), {"github_api": "https://api.github.com"})()
    assert github.look(s, project) == {"repo": "acme/app", "label": "missing", "code": "yes"}
    assert calls == ["/repos/acme/app", "/repos/acme/app/issues", "/repos/acme/app/pulls", "/repos/acme/app/labels/ready-to-build", "/repos/acme/app/commits"]


def test_the_command_line(env, capsys):
    env(AUTH_MODE="dev", CF_ACCESS_AUD="", CF_ACCESS_TEAM="")
    assert check.main(["--offline"]) == 0
    assert "Inrface setup check" in capsys.readouterr().out
    assert check.main(["--what"]) == 2
