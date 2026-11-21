"""SQLite storage. One short-lived connection per use."""
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .config import Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  email TEXT PRIMARY KEY, last_seen_at TEXT
);
CREATE TABLE IF NOT EXISTS cards (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
  who TEXT NOT NULL DEFAULT '', problem TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL DEFAULT '',
  open_questions TEXT NOT NULL DEFAULT '[]', acceptance TEXT NOT NULL DEFAULT '[]',
  original_text TEXT NOT NULL DEFAULT '', transcript TEXT NOT NULL DEFAULT '[]',
  source TEXT NOT NULL, author_email TEXT NOT NULL, project_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'New', priority TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '',
  merged_into TEXT REFERENCES cards(id),
  issue_number INTEGER, issue_url TEXT NOT NULL DEFAULT '', pr_url TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cards_created ON cards(created_at);
CREATE INDEX IF NOT EXISTS cards_project ON cards(project_id, created_at);
CREATE TABLE IF NOT EXISTS comments (
  id TEXT PRIMARY KEY, card_id TEXT NOT NULL REFERENCES cards(id),
  author_email TEXT NOT NULL, text TEXT NOT NULL, type TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS comments_card ON comments(card_id, created_at);
CREATE TABLE IF NOT EXISTS activity (
  id TEXT PRIMARY KEY, card_id TEXT, project_id TEXT NOT NULL, actor_email TEXT NOT NULL,
  kind TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS activity_created ON activity(created_at);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, owner_email TEXT NOT NULL, project_id TEXT NOT NULL, card_id TEXT REFERENCES cards(id),
  saved INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL REFERENCES conversations(id),
  role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS push_subscriptions (
  endpoint TEXT PRIMARY KEY, email TEXT NOT NULL, p256dh TEXT NOT NULL, auth TEXT NOT NULL, created_at TEXT NOT NULL,
  session_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS project_github (
  project_id TEXT PRIMARY KEY, repo TEXT NOT NULL DEFAULT '', build_label TEXT NOT NULL DEFAULT '',
  token TEXT NOT NULL DEFAULT '', updated_by TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS project_runner (
  project_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, interval_minutes INTEGER NOT NULL DEFAULT 5,
  max_builds INTEGER NOT NULL DEFAULT 1, key_id TEXT NOT NULL DEFAULT '', key_hash TEXT NOT NULL DEFAULT '',
  key_hint TEXT NOT NULL DEFAULT '', last_seen_at TEXT NOT NULL DEFAULT '',
  updated_by TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS project_labels (
  project_id TEXT PRIMARY KEY, labels TEXT NOT NULL DEFAULT '[]',
  updated_by TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS project_flow (
  project_id TEXT PRIMARY KEY, stages TEXT NOT NULL DEFAULT '[]',
  updated_by TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL,
  key_hash TEXT NOT NULL, key_hint TEXT NOT NULL,
  created_by TEXT NOT NULL, created_at TEXT NOT NULL, last_used_at TEXT, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS signals (
  source_id TEXT NOT NULL, project_id TEXT NOT NULL, tool TEXT NOT NULL, day TEXT NOT NULL,
  positive INTEGER NOT NULL DEFAULT 0, negative INTEGER NOT NULL DEFAULT 0, neutral INTEGER NOT NULL DEFAULT 0,
  rating_count INTEGER NOT NULL DEFAULT 0, rating_sum INTEGER NOT NULL DEFAULT 0, comments INTEGER NOT NULL DEFAULT 0,
  tool_label TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
  PRIMARY KEY (source_id, tool, day)
);
CREATE INDEX IF NOT EXISTS signals_project ON signals(project_id, day);
CREATE TABLE IF NOT EXISTS connectors (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
  config TEXT NOT NULL DEFAULT '{}', secrets TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
  created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  last_ok_at TEXT, last_error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS connectors_project ON connectors(project_id);
CREATE TABLE IF NOT EXISTS card_links (
  id TEXT PRIMARY KEY, card_id TEXT NOT NULL REFERENCES cards(id),
  connector_id TEXT NOT NULL,  -- a connectors.id, or 'github' for the project's repo
  kind TEXT NOT NULL,
  role TEXT NOT NULL,          -- 'build': where a build request went. 'thread': where the idea is discussed.
                               -- 'origin': the outside message it was made from, when that isn't the thread.
  state TEXT NOT NULL DEFAULT 'sent',  -- 'pending' while the request is on its way
  label TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', external_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE(card_id, connector_id, role)
);
CREATE INDEX IF NOT EXISTS card_links_external ON card_links(connector_id, role, external_id);
-- One outside message makes at most one card, however many times it is delivered or sent.
CREATE UNIQUE INDEX IF NOT EXISTS card_links_once ON card_links(connector_id, role, external_id) WHERE role IN ('thread', 'origin');
-- People seen in Slack. The address is kept only when it belongs to someone listed in innerface.toml.
-- Kept per connector: what one workspace says about a person is never used for another.
CREATE TABLE IF NOT EXISTS slack_users (
  connector_id TEXT NOT NULL, user_id TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', fetched_at TEXT NOT NULL,
  PRIMARY KEY (connector_id, user_id)
);
-- Two ideas a receiver looked at and said are not the same thing, so they stop being suggested as a pair.
-- Stored once per pair, with the smaller id first.
CREATE TABLE IF NOT EXISTS not_same (
  a TEXT NOT NULL REFERENCES cards(id), b TEXT NOT NULL REFERENCES cards(id), project_id TEXT NOT NULL,
  by_email TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (a, b)
);
-- Links that show a project's roadmap to someone without a login. Only a hash of the link's secret is kept.
CREATE TABLE IF NOT EXISTS share_links (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, label TEXT NOT NULL DEFAULT '',
  summaries INTEGER NOT NULL DEFAULT 0, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
  expires_at TEXT, revoked_at TEXT, last_opened_at TEXT, opens INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS share_links_project ON share_links(project_id);
CREATE TABLE IF NOT EXISTS dir_people (
  email TEXT PRIMARY KEY, name TEXT NOT NULL, added_by TEXT NOT NULL, added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dir_projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dir_members (
  project_id TEXT NOT NULL, email TEXT NOT NULL, role TEXT NOT NULL, set_by TEXT NOT NULL, set_at TEXT NOT NULL,
  PRIMARY KEY (project_id, email)
);
CREATE TABLE IF NOT EXISTS dir_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, actor_email TEXT NOT NULL, what TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_usage (
  email TEXT NOT NULL, day TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (email, day)
);

-- Built-in sign-in. What signs someone in is stored only as a hash: a browser's session, an AI app's tokens, the
-- one-use code that makes a connection, and the ids of sign-ins and requests that are under way.
-- Kept as they are: what a sign-in needs for its ten minutes (nonce, verifier, where to go next), what an AI app
-- sent when it registered (its secret too, if it asked for one), and what its pending request asked for.
CREATE TABLE IF NOT EXISTS signin_states (
  state_hash TEXT PRIMARY KEY,
  browser_hash TEXT NOT NULL,
  provider TEXT NOT NULL,
  nonce TEXT NOT NULL,
  verifier TEXT NOT NULL,
  next TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  email TEXT NOT NULL,
  provider TEXT NOT NULL,
  agent TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  seen_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_email ON sessions(email);
CREATE TABLE IF NOT EXISTS oauth_clients (
  client_id TEXT PRIMARY KEY,
  info TEXT NOT NULL,
  created_at TEXT NOT NULL,
  used_at TEXT
);
CREATE TABLE IF NOT EXISTS oauth_requests (
  id_hash TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  params TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_codes (
  code_hash TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  email TEXT NOT NULL,
  params TEXT NOT NULL,
  expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_grants (
  id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  client_name TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL,
  scopes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  used_at TEXT NOT NULL,
  code_hash TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS oauth_grants_email ON oauth_grants(email);
CREATE TABLE IF NOT EXISTS oauth_tokens (
  token_hash TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  grant_id TEXT NOT NULL REFERENCES oauth_grants(id) ON DELETE CASCADE,
  expires_at REAL NOT NULL,
  spent INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS oauth_tokens_grant ON oauth_tokens(grant_id);
"""


def now() -> str:
    return ago(0)


def ago(seconds: float) -> str:
    """The time this many seconds back, in the same form as now(), so the two compare as text."""
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id() -> str:
    return uuid.uuid4().hex


def db_path(s: Settings):
    return s.data_dir / "innerface.db"


@contextmanager
def connect(s: Settings):
    conn = sqlite3.connect(db_path(s), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


# Columns added after the first release. SQLite can only add them one at a time.
CARD_COLUMNS = {
    "source_id": "TEXT",            # set for ideas that came in through the intake API
    "source_name": "TEXT NOT NULL DEFAULT ''",
    "external_id": "TEXT",
    "author_ref": "TEXT NOT NULL DEFAULT ''",
    "source_url": "TEXT NOT NULL DEFAULT ''",
    "tool": "TEXT NOT NULL DEFAULT ''",
    "flagged": "INTEGER NOT NULL DEFAULT 0",
    "redactions": "INTEGER NOT NULL DEFAULT 0",
    "erased_at": "TEXT",
    "build_started_at": "TEXT",     # set once a build request has gone to at least one place
    "code_check": "TEXT NOT NULL DEFAULT ''",  # what an AI app found in the project's code, as JSON
    "build_asked_at": "TEXT",       # an AI app asked for a build, and it waits for a press in the app
    "build_asked_by": "TEXT NOT NULL DEFAULT ''",
    "shipped_at": "TEXT",           # when it became Done / Live; cleared if it is moved back
    "label": "TEXT NOT NULL DEFAULT ''",  # the team's own name for where it sits, when it isn't the first label for its status
    "build_stage": "TEXT NOT NULL DEFAULT ''",  # the stage of the project's build flow its code has reached
    "try_url": "TEXT NOT NULL DEFAULT ''",  # where to try this one once it is live, if not the project's usual address
}


# When this person last asked an AI app what's new, and how far back that answer reached.
USER_COLUMNS = {"caught_up_at": "TEXT", "caught_up_from": "TEXT"}


# When an idea is merged into another, its comments move with it. This says which idea each one came from,
# so a merge can be undone.
COMMENT_COLUMNS = {"moved_from": "TEXT"}


# Where an action was taken: web, mcp (an AI app), slack, api (another system) or github. Empty on rows
# written before this was recorded.
ACTIVITY_COLUMNS = {"via": "TEXT NOT NULL DEFAULT ''"}


# Usage counts (intake v1.1). NULL means the sender didn't report it, which is different from 0.
SIGNAL_COLUMNS = {"uses": "INTEGER", "failures": "INTEGER", "reruns": "INTEGER"}


# Who may ask an AI app about the repo's code. Empty means the settings file decides.
# live_url is where people can try what the project builds. It sits here because this is the one table of
# per-project settings.
GITHUB_COLUMNS = {"code_access": "TEXT NOT NULL DEFAULT ''", "live_url": "TEXT NOT NULL DEFAULT ''"}

# session_id: the browser sign-in an alert subscription was saved under, so signing out stops the alerts.
# used_at: when an AI app last made or renewed a connection. code_hash: the code a connection was made with.
PUSH_COLUMNS = {"session_id": "TEXT NOT NULL DEFAULT ''"}
OAUTH_CLIENT_COLUMNS = {"used_at": "TEXT"}
OAUTH_GRANT_COLUMNS = {"code_hash": "TEXT NOT NULL DEFAULT ''"}


def _add_columns(c, table: str, columns: dict) -> None:
    have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns.items():
        if name not in have:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _addon_columns() -> dict:
    """Columns that a part not in every edition keeps on the users table."""
    try:
        from . import phrase
    except ImportError:
        return {}
    return phrase.COLUMNS


def _addon_schema() -> str:
    """Tables that a part not in every edition keeps."""
    try:
        from . import approvals
    except ImportError:
        return ""
    return approvals.SCHEMA


def init(s: Settings) -> None:
    s.data_dir.mkdir(parents=True, exist_ok=True)
    with connect(s) as c:
        c.execute("PRAGMA journal_mode=WAL")
        # This table is only a short-lived copy of what Slack says; an older shape of it is simply started again.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(slack_users)")}
        if cols and "connector_id" not in cols:
            c.execute("DROP TABLE slack_users")
        c.executescript(SCHEMA + _addon_schema())
        _add_columns(c, "cards", CARD_COLUMNS)
        _add_columns(c, "users", USER_COLUMNS | _addon_columns())
        _add_columns(c, "comments", COMMENT_COLUMNS)
        _add_columns(c, "signals", SIGNAL_COLUMNS)
        _add_columns(c, "activity", ACTIVITY_COLUMNS)
        _add_columns(c, "project_github", GITHUB_COLUMNS)
        _add_columns(c, "push_subscriptions", PUSH_COLUMNS)
        _add_columns(c, "oauth_clients", OAUTH_CLIENT_COLUMNS)
        _add_columns(c, "oauth_grants", OAUTH_GRANT_COLUMNS)
        # Apps that hold a connection today have been used, whenever they registered.
        c.execute("UPDATE oauth_clients SET used_at=created_at WHERE used_at IS NULL AND client_id IN (SELECT client_id FROM oauth_grants)")
        # Older rows: an idea's source says where it was sent from, and the actor says the rest where it can.
        c.execute("""UPDATE activity SET via = (SELECT CASE cards.source WHEN 'mcp' THEN 'mcp' WHEN 'slack' THEN 'slack'
                                                  WHEN 'intake' THEN 'api' ELSE 'web' END FROM cards WHERE cards.id = activity.card_id)
                     WHERE kind='created' AND via='' AND card_id IN (SELECT id FROM cards)""")
        c.execute("UPDATE activity SET via='github' WHERE via='' AND actor_email='github'")
        c.execute("UPDATE activity SET via='api' WHERE via='' AND actor_email LIKE 'source:%'")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS cards_external ON cards(source_id, external_id) WHERE source_id IS NOT NULL")
        c.execute("""UPDATE cards SET shipped_at = COALESCE((SELECT MAX(created_at) FROM activity WHERE activity.card_id = cards.id
                                                              AND kind='status' AND detail='Done / Live'), updated_at)
                     WHERE status='Done / Live' AND shipped_at IS NULL""")
        # Builds started before destinations other than GitHub existed.
        c.execute("UPDATE cards SET issue_url='' WHERE issue_url='pending'")
        c.execute("UPDATE cards SET build_started_at=updated_at WHERE issue_number IS NOT NULL AND build_started_at IS NULL")
        c.execute("""INSERT OR IGNORE INTO card_links(id, card_id, connector_id, kind, role, state, label, url, external_id, created_at)
                     SELECT lower(hex(randomblob(16))), id, 'github', 'github', 'build', 'sent', 'Issue #' || issue_number, issue_url,
                            CAST(issue_number AS TEXT), build_started_at FROM cards WHERE issue_number IS NOT NULL""")
        for email in s.people:
            c.execute("INSERT OR IGNORE INTO users(email) VALUES (?)", (email,))
    # People and projects added from the app are laid over the settings file.
    from . import directory
    directory.refresh(s)
