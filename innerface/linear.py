"""Linear, push only: Start build opens an issue in one team. Nothing is read back.
Reference: docs/linear.md"""
import logging

import httpx

from .config import Settings
from .connectors import Conn, ConnectorError, Field, Kind, Sent, register

log = logging.getLogger("innerface.linear")

API = "https://api.linear.app/graphql"
MAX_DESCRIPTION = 60000

_WHO = """query($key: String!) {
  viewer { name }
  organization { name }
  teams(filter: { key: { eq: $key } }) { nodes { id key name } }
}"""
_LABEL = """query($name: String!) {
  issueLabels(filter: { name: { eqIgnoreCase: $name } }) { nodes { id name team { id } } }
}"""
_CREATE = """mutation($input: IssueCreateInput!) {
  issueCreate(input: $input) { success issue { id identifier url } }
}"""


def _query(conn: Conn, query: str, variables: dict) -> dict:
    # A personal API key goes in the header as it is, with no "Bearer".
    r = httpx.post(API, json={"query": query, "variables": variables}, timeout=20.0,
                   headers={"Authorization": conn.secrets["api_key"], "Content-Type": "application/json", "User-Agent": "Inrface"})
    if r.status_code in (401, 403):
        raise ConnectorError("Linear refused the API key. Check it, and that it can create issues.")
    if r.status_code == 429:
        raise ConnectorError("Linear is asking us to slow down. Try again in a minute.")
    try:
        body = r.json()
    except ValueError:
        body = {}
    errors = body.get("errors") if isinstance(body, dict) else None
    if errors or r.status_code >= 400 or not isinstance(body.get("data"), dict):
        first = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
        log.warning("linear request failed: %s %s", r.status_code, str(first.get("message"))[:200])
        # Only repeat Linear's wording when it marks the message as meant for the person.
        shown = str(first.get("message"))[:200] if (first.get("extensions") or {}).get("userError") else ""
        raise ConnectorError("Linear couldn't do that" + (f": {shown}" if shown else " just now."))
    return body["data"]


def _team(conn: Conn) -> tuple[dict, dict]:
    data = _query(conn, _WHO, {"key": conn.values["team"]})
    teams = (data.get("teams") or {}).get("nodes") or []
    if not teams:
        raise ConnectorError(f"Linear has no team with the key {conn.values['team']}, or this API key can't see it.")
    return teams[0], data


def _label_id(conn: Conn, team_id: str) -> str | None:
    name = conn.values.get("label")
    if not name:
        return None
    nodes = (_query(conn, _LABEL, {"name": name}).get("issueLabels") or {}).get("nodes") or []
    # A label belongs to one team or to the whole workspace. Prefer this team's.
    usable = [n for n in nodes if not n.get("team") or n["team"].get("id") == team_id]
    usable.sort(key=lambda n: n.get("team") is None)
    return usable[0]["id"] if usable else None


def test(s: Settings, conn: Conn) -> str:
    team, data = _team(conn)
    line = f"Connected to {(data.get('organization') or {}).get('name') or 'Linear'} as {(data.get('viewer') or {}).get('name') or 'this key'}. Issues go to {team['name']} ({team['key']})."
    if conn.values.get("label") and not _label_id(conn, team["id"]):
        line += f" The label \"{conn.values['label']}\" doesn't exist there yet, so issues will be opened without it."
    return line


def send_build(s: Settings, conn: Conn, build: dict) -> Sent:
    team, _ = _team(conn)
    footer = f"\n---\nOpened from Inrface card `{build['card']['id']}` by {build['requested_by']}."
    if build["card_url"]:
        footer += f" [Open the card]({build['card_url']})"
    issue = {"teamId": team["id"], "title": build["card"]["title"][:250], "description": (build["spec"] + footer)[:MAX_DESCRIPTION]}
    label = _label_id(conn, team["id"])
    if label:
        issue["labelIds"] = [label]
    made = _query(conn, _CREATE, {"input": issue}).get("issueCreate") or {}
    got = made.get("issue") or {}
    if not made.get("success") or not got.get("identifier"):
        raise ConnectorError("Linear didn't create the issue.")
    return Sent(label=got["identifier"], url=got.get("url") or "", external_id=got.get("id") or "")


def _clean(s: Settings, values: dict) -> dict:
    return {**values, "team": values.get("team", "").upper()}


register(Kind(
    id="linear", label="Linear", group="tracker",
    summary="Start build opens an issue in one Linear team. Nothing is read back from Linear.",
    docs="/docs/linear",
    fields=(
        Field("api_key", "API key", secret=True, placeholder="lin_api_...", pattern=r"[A-Za-z0-9_.\-]{20,255}",
              help="A personal API key from Linear: Settings, then Security and access."),
        Field("team", "Team key", placeholder="ENG", pattern=r"[A-Za-z0-9]{1,10}", max=10,
              help="The short code in front of issue numbers, like ENG in ENG-123."),
        Field("label", "Label", required=False, max=80, pattern=r"[^\x00-\x1f]{1,80}",
              help="Optional. An existing Linear label to put on each issue."),
    ),
    test=test, send_build=send_build, clean=_clean,
))
