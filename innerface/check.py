"""Setup check: reads the settings the way the server does and says in plain words what to fix.

    docker compose run --rm app python -m innerface.check

It only reads. It asks Cloudflare and GitHub the same questions the server will, and it never prints
a secret: the most it shows of the Access audience is its first eight characters, as the log does.
Add --offline to skip everything that needs the network.
"""
import re
import sys
import tempfile
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import httpx

from . import db, github, service
from .config import ConfigError, Settings, get_settings

OK, FIX, NOTE = "OK", "FIX", "NOTE"


@dataclass(frozen=True)
class Line:
    level: str
    text: str
    todo: str = ""


_TEAM = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_AUD = re.compile(r"^[0-9a-f]{64}$")
_LOGS = "docker compose logs tunnel"


def _settings() -> tuple[Settings | None, list[Line]]:
    get_settings.cache_clear()
    try:
        s = get_settings()
    except ConfigError as e:
        return None, [Line(FIX, str(e))]
    except ValueError:
        return None, [Line(FIX, "AI_DAILY_LIMIT and GITHUB_POLL_SECONDS must be whole numbers.")]
    lines = [Line(OK, f"Settings file: {_count(len(s.people), 'person', 'people')}, {_count(len(s.projects), 'project')}.")]
    examples = sorted(e for e in s.people if e.endswith("@example.com"))
    if examples and s.auth_mode != "dev":
        lines.append(Line(FIX, f"{' and '.join(examples)} {'is' if len(examples) == 1 else 'are'} still in the settings file from the example.",
                          "Put in the address each person signs in to Cloudflare with." if s.auth_mode == "access" else "Put in the address each person signs in with."))
    return s, lines


def _count(n: int, one: str, many: str = "") -> str:
    return f"{n} {one if n == 1 else many or one + 's'}"


def _data(s: Settings) -> list[Line]:
    if not s.data_dir.exists():
        return [Line(NOTE, f"The data folder {s.data_dir} doesn't exist yet. The server makes it when it starts.")]
    try:
        with tempfile.TemporaryFile(dir=s.data_dir):
            pass
    except OSError:
        return [Line(FIX, f"The data folder {s.data_dir} can't be written to.",
                     "In Docker it is the innerface-data volume and needs no setup. Outside Docker, give the folder to the user that runs the server.")]
    return [Line(OK, "The data folder can be written to.")]


def _extras(s: Settings) -> list[Line]:
    lines = []
    if not s.secret_key:
        lines.append(Line(NOTE, "INNERFACE_SECRET_KEY is empty, so tokens saved from the Settings screen are stored as they are.",
                          "Make a key with: openssl rand -base64 48"))
    if not s.push_contact:
        lines.append(Line(NOTE, "PUSH_CONTACT is empty, so phone alerts stay off.", "Set it to an email address you read."))
    if s.ai_provider == "anthropic" and not s.anthropic_api_key:
        lines.append(Line(FIX, "AI_PROVIDER is anthropic but ANTHROPIC_API_KEY is empty."))
    if s.ai_provider == "openai" and not (s.openai_api_key and s.openai_model):
        lines.append(Line(FIX, "AI_PROVIDER is openai but OPENAI_API_KEY or OPENAI_MODEL is empty."))
    return lines


def _access(s: Settings, http: httpx.Client | None) -> list[Line]:
    if s.auth_mode == "dev":
        return [Line(NOTE, "AUTH_MODE is dev: there is no sign-in, and anyone who can reach the port is trusted.",
                     "Fine for trying it on your own machine. Never put it on the internet like this.")]
    if s.auth_mode == "builtin":
        return _builtin(s)
    lines, shaped = [], True
    if not _TEAM.match(s.access_team):
        shaped = False
        lines.append(Line(FIX, "CF_ACCESS_TEAM should be the team name alone.",
                          "It is the part before .cloudflareaccess.com, with no https:// and no dots. Zero Trust > Settings shows it."))
    if not _AUD.match(s.access_aud):
        shaped = False
        lines.append(Line(FIX, "CF_ACCESS_AUD doesn't look like an Application Audience tag, which is 64 letters and numbers.",
                          "Copy it from Zero Trust > Access > Applications > your application."))
    if not shaped or http is None:
        return lines
    team_host = f"{s.access_team}.cloudflareaccess.com"
    try:
        r = http.get(f"https://{team_host}/cdn-cgi/access/certs")
    except httpx.HTTPError:
        return lines + [Line(FIX, "Cloudflare can't be reached from here, so nobody could be signed in.",
                             "Check this machine's internet connection, then run the check again.")]
    if r.status_code != 200:
        return lines + [Line(FIX, f'Cloudflare has no team called "{s.access_team}".',
                             "Check CF_ACCESS_TEAM against Zero Trust > Settings.")]
    lines.append(Line(OK, f'Cloudflare knows the team "{s.access_team}".'))
    if not s.public_url:
        return lines + [Line(NOTE, "PUBLIC_URL is empty, so the address can't be checked and links back to cards are left out.",
                             "Set it to the address people open, like https://ideas.example.com")]
    return lines + _address(s, http, team_host)


def _builtin(s: Settings) -> list[Line]:
    from . import connect, signin
    lines = [Line(NOTE, "AUTH_MODE is builtin: the app signs people in itself, with nothing in front of it.",
                  "This way of signing in is new and has not had an outside security review. Cloudflare Access (AUTH_MODE=access) is still the safer choice.")]
    found = signin.providers(s)
    if not found:
        lines.append(Line(FIX, "There is no way to sign in, so nobody could get in.",
                          "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET. docs/sign-in.md shows where to get them."))
    for p in found.values():
        lines.append(Line(OK, f"People can sign in with {p.label}. It has to allow the return address {signin.callback_url(s, p.id)}"))
    hosts = sorted({u.split("/")[2] for u in s.connector_redirects})
    lines.append(Line(OK, f"AI apps connect at {connect.resource_url(s)} and can be sent back to {', '.join(hosts)}." if hosts
                      else "No AI app can connect: CONNECTOR_REDIRECTS is empty."))
    return lines


def _address(s: Settings, http: httpx.Client, team_host: str) -> list[Line]:
    """Ask for the address the way a browser with no session would, then the way an AI app would."""
    url, mine = s.public_url, s.access_aud[:8]
    try:
        r = http.get(url + "/", headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0 (Inrface setup check)"})
    except httpx.HTTPError:
        return [Line(FIX, f"{url} can't be reached.",
                     f"Check that the tunnel has a public hostname for it pointing at http://app:8080, and read: {_LOGS}")]
    to = urlsplit(r.headers.get("location", ""))
    if r.status_code in (301, 302, 303, 307) and to.hostname and to.hostname.endswith(".cloudflareaccess.com"):
        if to.hostname != team_host:
            return [Line(FIX, f"{url} is behind a different Cloudflare team ({to.hostname.split('.')[0]}).",
                         "CF_ACCESS_TEAM must be the team whose Access application covers this address.")]
        kid = (parse_qs(to.query).get("kid") or [""])[0]
        if kid != s.access_aud:
            return [Line(FIX, f"{url} is covered by a different Access application (its tag starts {kid[:8] or '?'}, and CF_ACCESS_AUD starts {mine}).",
                         "Either copy that application's AUD tag into CF_ACCESS_AUD, or, if it is a catch-all for other sites, "
                         "make sure the Inrface application names this exact address so it wins. Everyone would be refused until this matches.")]
        lines = [Line(OK, f"{url} asks people to sign in through the right Access application.")]
    elif r.status_code >= 500:
        return [Line(FIX, f"{url} answers with an error ({r.status_code}) and no sign-in.",
                     f"No Access application covers the address, and the tunnel isn't reaching the app. Read: {_LOGS}")]
    else:
        return [Line(FIX, f"{url} doesn't ask anyone to sign in.",
                     "No Access application covers this address. Add it to your application in Zero Trust > Access > Applications. "
                     "Until then the app refuses everyone, because requests arrive without Cloudflare's token.")]
    try:
        m = http.post(url + "/mcp", json={})
    except httpx.HTTPError:
        return lines + [Line(NOTE, "The AI app sign-in couldn't be checked just now.")]
    asks = m.headers.get("www-authenticate", "")
    if m.status_code == 401 and asks.lower().startswith("bearer") and "resource_metadata" in asks:
        lines.append(Line(OK, f"AI apps can sign in at {url}/mcp."))
    else:
        lines.append(Line(FIX, "AI apps can't sign in: Managed OAuth looks switched off for this address.",
                          "Open the Access application, go to Advanced settings and turn on Managed OAuth with dynamic client registration."))
    return lines


def _github(s: Settings, offline: bool) -> list[Line]:
    lines = []
    saved = db.db_path(s).exists()
    for project in s.projects.values():
        if saved:
            try:
                project = service.github_project(s, project)
            except Exception:  # an older or half-made database: go by the settings file alone
                pass
        if not project.repo:
            lines.append(Line(NOTE, f"{project.name} has no GitHub repo, so its builds go only to connectors added under Settings → Connectors."))
            continue
        if not project.github_token:
            named = f"{project.github_token_env} is empty in .env" if project.github_token_env else "no token is set for it"
            lines.append(Line(FIX, f"{project.name} names the repo {project.repo}, but {named}.",
                              "Make a fine-grained GitHub token for that repo alone, with Issues read and write, Pull requests read and Contents read."))
            continue
        if offline:
            continue
        try:
            seen = github.look(s, project)
        except github.GitHubError as e:
            lines.append(Line(FIX, f"{project.name} ({project.repo}): {e}"))
            continue
        lines.append(Line(OK, f"{project.name}: the token can read {seen['repo']}, its issues and its pull requests."))
        if project.code_access != "off":
            if seen["code"] == "yes":
                lines.append(Line(OK, f"{project.name}: the token can read the code, so people can ask their AI app what is already built."))
            elif seen["code"] == "no":
                lines.append(Line(NOTE, f"{project.name}: the token can't read the code, so AI apps can't check what is already built.",
                                  "Give the token read access to Contents, or set who can ask about the code to Nobody under Settings → Connectors → GitHub and build flow."))
        if seen["label"] == "missing":
            lines.append(Line(NOTE, f'{project.name}: the repo has no "{project.build_label}" label yet.',
                              "The Check connection button under Settings → Connectors → GitHub and build flow makes it, and proves the token may open issues."))
    return lines


def run(offline: bool = False, http: httpx.Client | None = None) -> list[Line]:
    s, lines = _settings()
    if s is None:
        return lines
    lines += _data(s)
    if offline:
        lines += _access(s, None)
    elif http is not None:
        lines += _access(s, http)
    else:
        with httpx.Client(timeout=15.0, follow_redirects=False) as c:
            lines += _access(s, c)
    return lines + _github(s, offline) + _extras(s)


def report(lines: list[Line], out=None) -> int:
    out = out or sys.stdout
    print("Inrface setup check\n", file=out)
    for l in lines:
        print(f"  {l.level:<5} {l.text}", file=out)
        if l.todo:
            print(f"        {l.todo}", file=out)
    fixes = sum(l.level == FIX for l in lines)
    print(file=out)
    print(f"{_count(fixes, 'thing')} to fix." if fixes else "Nothing to fix.", file=out)
    return 1 if fixes else 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if any(a not in ("--offline",) for a in args):
        print("Usage: python -m innerface.check [--offline]", file=sys.stderr)
        return 2
    return report(run(offline="--offline" in args))


if __name__ == "__main__":
    sys.exit(main())
