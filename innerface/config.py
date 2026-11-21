"""Settings. People and projects come from a TOML file; secrets come from the environment."""
import os
import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


PLANS = ("free",)   # this edition has one set of limits: see plans.py


class ConfigError(RuntimeError):
    pass


CODE_ACCESS = ("everyone", "receivers", "off")


@dataclass(frozen=True)
class Person:
    key: str
    email: str
    name: str


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    givers: tuple[str, ...]  # emails
    receivers: tuple[str, ...]  # emails
    repo: str = ""  # "owner/name" on GitHub; empty means no builds
    github_token_env: str = ""
    build_label: str = "ready-to-build"
    context: str = ""  # optional background for the in-app assistant
    token: str = ""  # set from the Settings screen; wins over github_token_env
    code_access: str = "everyone"  # who may ask an AI app about the repo's code: everyone, receivers or off

    def role(self, email: str) -> str | None:
        if email in self.receivers:
            return "receiver"
        if email in self.givers:
            return "giver"
        return None

    @property
    def members(self) -> tuple[str, ...]:
        return self.receivers + tuple(e for e in self.givers if e not in self.receivers)

    @property
    def github_token(self) -> str:
        if self.token:
            return self.token
        return os.environ.get(self.github_token_env, "").strip() if self.github_token_env else ""


@dataclass(frozen=True)
class Settings:
    auth_mode: str
    access_team: str
    access_aud: str
    people: dict  # email -> Person
    projects: dict  # id -> Project
    ai_provider: str
    anthropic_api_key: str
    anthropic_model: str
    openai_api_key: str
    openai_model: str
    data_dir: Path
    ai_daily_limit: int
    push_contact: str
    dev_user: str
    github_api: str = "https://api.github.com"
    github_poll_seconds: int = 300
    secret_key: str = ""  # encrypts saved tokens; see vault.py
    public_url: str = ""  # e.g. https://ideas.example.com, for links back to cards
    webhook_allow_private: bool = False
    # People and projects exactly as the settings file has them. `people` and `projects` above also hold what was
    # added or changed from the app (see directory.py).
    file_people: dict | None = None
    file_projects: dict | None = None
    # Built-in sign-in (AUTH_MODE=builtin). See signin.py and connect.py.
    google_client_id: str = ""
    google_client_secret: str = ""
    session_days: int = 30
    connector_redirects: tuple = ()
    plan: str = PLANS[0]  # see plans.py

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")

    def use(self, people: dict, projects: dict) -> None:
        """Swap in who is who. Each dict is replaced whole and never changed in place, so a request that is
        part-way through goes on reading the one it started with."""
        object.__setattr__(self, "people", people)
        object.__setattr__(self, "projects", projects)

    def name(self, email: str) -> str:
        p = self.people.get(email)
        return p.name if p else "Someone"

    def projects_for(self, email: str) -> list[Project]:
        return [p for p in self.projects.values() if p.role(email)]

    @property
    def ai_enabled(self) -> bool:
        return self.ai_provider != "none"


_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_REPO = re.compile(r"^(?!\.{1,2}/)(?!.*/\.{1,2}$)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ENV = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def load_people_and_projects(path: Path) -> tuple[dict, dict]:
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}. Copy innerface.example.toml and fill it in.")
    except IsADirectoryError:
        # Docker makes an empty folder in place of a mounted file that wasn't there when it started.
        raise ConfigError(f"{path} is a folder, not a file. On the server, stop the app, remove the innerface.toml folder, "
                          "copy innerface.example.toml to innerface.toml and fill it in.")
    except PermissionError:
        raise ConfigError(f"Config file {path} can't be read by the app. On the server, run: chmod 644 innerface.toml")
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Config file {path} isn't valid TOML: {e}")

    people: dict[str, Person] = {}
    by_key: dict[str, Person] = {}
    for key, p in (raw.get("people") or {}).items():
        email = str(p.get("email", "")).strip().lower()
        if "@" not in email:
            raise ConfigError(f"people.{key} needs an email")
        if email in people:
            raise ConfigError(f"{email} is listed twice in people")
        person = Person(key=key, email=email, name=str(p.get("name") or key).strip())
        people[email] = by_key[key] = person
    if not people:
        raise ConfigError("Add at least one person under [people.<key>]")

    def emails(pid: str, field_name: str, keys) -> tuple[str, ...]:
        if not isinstance(keys, list):
            raise ConfigError(f"projects.{pid}.{field_name} must be a list of people keys")
        missing = [k for k in keys if k not in by_key]
        if missing:
            raise ConfigError(f"projects.{pid}.{field_name} names unknown people: {', '.join(missing)}")
        return tuple(dict.fromkeys(by_key[k].email for k in keys))

    projects: dict[str, Project] = {}
    for pid, p in (raw.get("projects") or {}).items():
        if not _SLUG.match(pid):
            raise ConfigError(f"Project id '{pid}' must be lowercase letters, numbers, - or _")
        receivers = emails(pid, "receivers", p.get("receivers", []))
        if not receivers:
            raise ConfigError(f"projects.{pid} needs at least one receiver")
        repo = str(p.get("repo", "")).strip()
        if repo and not _REPO.match(repo):
            raise ConfigError(f"projects.{pid}.repo must look like owner/name")
        token_env = str(p.get("github_token_env", "")).strip()
        if token_env and not _ENV.match(token_env):
            raise ConfigError(f"projects.{pid}.github_token_env must be an environment variable name")
        code_access = str(p.get("code_access") or "everyone").strip().lower()
        if code_access not in CODE_ACCESS:
            raise ConfigError(f"projects.{pid}.code_access must be one of: {', '.join(CODE_ACCESS)}")
        projects[pid] = Project(
            id=pid,
            name=str(p.get("name") or pid).strip(),
            givers=emails(pid, "givers", p.get("givers", [])),
            receivers=receivers,
            repo=repo,
            github_token_env=token_env,
            build_label=str(p.get("build_label") or "ready-to-build").strip()[:50],
            context=str(p.get("context") or "").strip(),
            code_access=code_access,
        )
    if not projects:
        raise ConfigError("Add at least one project under [projects.<id>]")
    return people, projects


# Where an AI app may be sent back to after someone lets it connect. A trailing * allows anything that starts the same.
CONNECTOR_REDIRECTS = ("https://claude.ai/api/mcp/auth_callback", "https://claude.com/api/mcp/auth_callback",
                       "https://chatgpt.com/connector_platform_oauth_redirect", "https://chatgpt.com/connector/oauth/*")
_LOCAL = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$")


def _origin(url: str) -> str:
    """PUBLIC_URL the way a browser writes it: scheme and host in lower case, and no port when it is the usual one.
    Built-in sign-in compares it with what the browser sends, letter for letter."""
    m = re.match(r"^(https?)://([^/\s]+)$", url, re.I)
    if not m:
        return url
    scheme, host = m.group(1).lower(), m.group(2).lower()
    usual = ":443" if scheme == "https" else ":80"
    return f"{scheme}://{host[:-len(usual)] if host.endswith(usual) else host}"


@lru_cache
def get_settings() -> Settings:
    env = os.environ.get
    data_dir = Path(env("DATA_DIR", "./data"))
    config_path = Path(env("INNERFACE_CONFIG", "innerface.toml"))
    people, projects = load_people_and_projects(config_path)
    s = Settings(
        auth_mode=env("AUTH_MODE", "access"),
        access_team=env("CF_ACCESS_TEAM", "").strip(),
        access_aud=env("CF_ACCESS_AUD", "").strip(),
        people=people,
        projects=projects,
        file_people=dict(people),
        file_projects=dict(projects),
        ai_provider=env("AI_PROVIDER", "none").strip().lower(),
        anthropic_api_key=env("ANTHROPIC_API_KEY", "").strip(),
        anthropic_model=env("ANTHROPIC_MODEL", "claude-opus-5-5").strip(),
        openai_api_key=env("OPENAI_API_KEY", "").strip(),
        openai_model=env("OPENAI_MODEL", "").strip(),
        data_dir=data_dir,
        ai_daily_limit=int(env("AI_DAILY_LIMIT", "200")),
        push_contact=env("PUSH_CONTACT", "").strip(),
        dev_user=env("DEV_USER", "").strip().lower(),
        github_api=env("GITHUB_API", "https://api.github.com").rstrip("/"),
        github_poll_seconds=int(env("GITHUB_POLL_SECONDS", "300")),
        secret_key=env("INNERFACE_SECRET_KEY", "").strip(),
        public_url=_origin(env("PUBLIC_URL", "").strip().rstrip("/")),
        webhook_allow_private=env("WEBHOOK_ALLOW_PRIVATE", "").strip().lower() in ("1", "true", "yes"),
        google_client_id=env("GOOGLE_CLIENT_ID", "").strip(),
        google_client_secret=env("GOOGLE_CLIENT_SECRET", "").strip(),
        session_days=int(env("SESSION_DAYS", "30")),
        connector_redirects=tuple(u.strip() for u in env("CONNECTOR_REDIRECTS", "").split(",") if u.strip()) or CONNECTOR_REDIRECTS,
    )
    if s.secret_key and len(s.secret_key) < 32:
        raise ConfigError("INNERFACE_SECRET_KEY must be at least 32 characters. Make one with: openssl rand -base64 32")
    if s.public_url and not re.match(r"^https?://[^/\s]+$", s.public_url):
        raise ConfigError("PUBLIC_URL must look like https://ideas.example.com")
    if s.auth_mode == "access":
        if not s.access_team or not s.access_aud:
            raise ConfigError("CF_ACCESS_TEAM and CF_ACCESS_AUD are required when AUTH_MODE=access")
    elif s.auth_mode == "dev":
        # Dev login trusts anyone who can reach the port, so it can never run alongside a real Access setup.
        if s.access_aud:
            raise ConfigError("AUTH_MODE=dev cannot be combined with CF_ACCESS_AUD")
    elif s.auth_mode == "builtin":
        # The app signs people in itself, so it has to know its own address, and that address has to be private on the wire.
        if not s.public_url:
            raise ConfigError("PUBLIC_URL is required when AUTH_MODE=builtin")
        if not s.secure_cookies and not _LOCAL.match(s.public_url):
            raise ConfigError("PUBLIC_URL must start with https:// when AUTH_MODE=builtin")
        if s.access_aud:
            raise ConfigError("AUTH_MODE=builtin cannot be combined with CF_ACCESS_AUD. Pick one way to sign in.")
        if not 1 <= s.session_days <= 365:
            raise ConfigError("SESSION_DAYS must be between 1 and 365")
        for u in s.connector_redirects:
            if not u.startswith("https://"):
                raise ConfigError(f"CONNECTOR_REDIRECTS must hold https addresses: {u}")
            # A * may only stand for the end of a path. After a bare host it would also match other hosts that start the same way.
            if "*" in u and not (u.endswith("/*") and u.count("*") == 1 and u.count("/") >= 3):
                raise ConfigError(f"In CONNECTOR_REDIRECTS a * can only end a path, as in https://example.com/callback/*: {u}")
    else:
        raise ConfigError("AUTH_MODE must be 'access', 'builtin' or 'dev'")
    if s.ai_provider not in ("none", "anthropic", "openai", "fake"):
        raise ConfigError(f"Unknown AI_PROVIDER: {s.ai_provider}")
    return s
