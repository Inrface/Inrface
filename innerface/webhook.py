"""The generic signed webhook: Start build posts the scope to any address, signed so the
receiver can check it came from this Inrface. Reference: docs/webhook.md"""
import hashlib
import hmac
import json
import time

from . import db, net
from .config import Settings
from .connectors import Conn, ConnectorError, Field, Kind, Sent, register

SIGNATURE_HEADER = "X-Inrface-Signature"
MAX_SPEC = 60000


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """The signature header's value: t=<unix seconds>,v1=<HMAC-SHA256 of "<t>.<body>" in hex>."""
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def _deliver(s: Settings, conn: Conn, event: str, data: dict) -> dict:
    delivery = db.new_id()
    body = json.dumps({"event": event, "delivery_id": delivery, "sent_at": db.now(), **data}, ensure_ascii=False).encode()
    headers = {
        "Content-Type": "application/json", "User-Agent": "Inrface-Webhook/1",
        "X-Inrface-Event": event, "X-Inrface-Delivery": delivery,
        SIGNATURE_HEADER: sign(conn.secrets["signing_secret"], int(time.time()), body),
    }
    try:
        status, reply = net.post(conn.values["url"], body, headers, allow_private=s.webhook_allow_private)
    except net.UnsafeAddress as e:
        raise ConnectorError(str(e))
    if status // 100 != 2:
        raise ConnectorError(f"The webhook answered with status {status}.")
    try:
        parsed = json.loads(reply)
    except ValueError:
        parsed = None
    return parsed if isinstance(parsed, dict) else {}


def _clean(s: Settings, values: dict) -> dict:
    try:
        net.check_url(values.get("url", ""), s.webhook_allow_private)
    except net.UnsafeAddress as e:
        raise ConnectorError(str(e))
    return values


def test(s: Settings, conn: Conn) -> str:
    _deliver(s, conn, "ping", {"project": {"id": conn.project_id, "name": s.projects[conn.project_id].name}})
    return "The webhook answered. It was sent a signed ping."


def send_build(s: Settings, conn: Conn, build: dict) -> Sent:
    card = build["card"]
    reply = _deliver(s, conn, "build.requested", {
        "project": {"id": build["project"].id, "name": build["project"].name},
        "requested_by": build["requested_by"],
        "card": {
            "id": card["id"], "url": build["card_url"], "title": card["title"], "summary": card["summary"],
            "who": card["who"], "problem": card["problem"], "scope": card["scope"],
            "acceptance": card["acceptance"], "open_questions": card["open_questions"],
            "priority": card["priority"], "tags": card["tags"], "author": card["author_name"],
        },
        "spec_markdown": build["spec"][:MAX_SPEC],
    })
    url, ref = reply.get("url"), reply.get("id")
    return Sent(label=f"Sent to {conn.name}", url=url if isinstance(url, str) else "",
                external_id=str(ref) if isinstance(ref, (str, int)) else "")


register(Kind(
    id="webhook", label="Webhook",
    summary="Start build posts the scope to an address you choose, signed so your system can check it came from here.",
    docs="/docs/webhook",
    fields=(
        Field("url", "Address", placeholder="https://example.com/hooks/inrface", max=500,
              help="An https:// address that accepts a POST."),
        Field("signing_secret", "Signing secret", secret=True, generated=True),
    ),
    test=test, send_build=send_build, clean=_clean,
))
