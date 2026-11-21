# Inrface webhook reference

A webhook connector posts a build request to an address you run. Use it to hand scoped ideas to any system Inrface has no connector for.

[TOC]

## Set it up

1. In Inrface, open **Settings → Connectors → Chat and tracker connectors**, choose **Webhook** and press **Add connector**.
2. Enter the address. It must start with `https://`.
3. Save. Inrface shows the **signing secret** once. Copy it into your system's settings.
4. Press **Test**. Your address receives a signed `ping`.

To replace the signing secret, edit the connector and press **New signing secret**. The old one stops working at once.

## Requests

Every request is a `POST` with a JSON body.

| Header | Value |
|---|---|
| `Content-Type` | `application/json` |
| `User-Agent` | `Inrface-Webhook/1` |
| `X-Inrface-Event` | `ping` or `build.requested` |
| `X-Inrface-Delivery` | A unique id for this request |
| `X-Inrface-Signature` | `t=<unix seconds>,v1=<signature>` |

Inrface waits up to 10 seconds, does not follow redirects, and does not retry by itself. A receiver retries a failed delivery from the card.

## Checking the signature

Check every request before you act on it. The signature proves the request came from your Inrface and was not changed on the way.

1. Read `t` and `v1` from `X-Inrface-Signature`.
2. Reject the request if `t` is more than 5 minutes from your clock.
3. Compute HMAC-SHA256 over `<t>.<raw request body>`, using the signing secret as the key, in hex.
4. Compare it with `v1` using a constant-time comparison.

Use the raw bytes of the body exactly as they arrived. Parsing and re-serializing the JSON changes the bytes and breaks the check.

Python:

```python
import hashlib, hmac, time

def is_from_inrface(secret: str, header: str, raw_body: bytes) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    t, v1 = parts.get("t", ""), parts.get("v1", "")
    if not t.isdigit() or abs(time.time() - int(t)) > 300:
        return False
    expected = hmac.new(secret.encode(), t.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)
```

Node.js:

```js
const crypto = require("crypto");

function isFromInrface(secret, header, rawBody) {
  const parts = Object.fromEntries(header.split(",").map((p) => p.split("=")));
  const t = parts.t || "", v1 = parts.v1 || "";
  if (!/^\d+$/.test(t) || Math.abs(Date.now() / 1000 - Number(t)) > 300) return false;
  const expected = crypto.createHmac("sha256", secret).update(t + ".").update(rawBody).digest("hex");
  return expected.length === v1.length && crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(v1));
}
```

`rawBody` must be a Buffer of the unparsed body. In Express, use `express.raw({ type: "application/json" })` on this route.

## Events

### ping

Sent when a receiver presses **Test**. Answer with any 2xx status.

```json
{
  "event": "ping",
  "delivery_id": "0d9c2b0a6f6b4c0e9b8f6f0c1f3a2d11",
  "sent_at": "2026-11-12T18:04:11.120Z",
  "project": {"id": "main", "name": "Main"}
}
```

### build.requested

Sent when a receiver presses **Start build**.

```json
{
  "event": "build.requested",
  "delivery_id": "7a1e4d0b3c2f4e6a8b9c0d1e2f3a4b5c",
  "sent_at": "2026-11-12T18:09:40.003Z",
  "project": {"id": "main", "name": "Main"},
  "requested_by": "Olly",
  "card": {
    "id": "c41f0e…",
    "url": "https://ideas.example.com/#card=c41f0e…",
    "title": "Weekly report for teachers",
    "summary": "Email each teacher a summary of the week.",
    "who": "Teachers",
    "problem": "They can't see progress without logging in.",
    "scope": "Email only. No in-app view yet.",
    "acceptance": ["Sends on Monday"],
    "open_questions": [],
    "priority": "High",
    "tags": ["reports"],
    "author": "Pat"
  },
  "spec_markdown": "# Weekly report for teachers\n\n…"
}
```

| Field | Notes |
|---|---|
| `delivery_id` | The same value as the `X-Inrface-Delivery` header |
| `requested_by` | The name of the receiver who pressed Start build |
| `card.url` | A link back to the card. Empty unless `PUBLIC_URL` is set on the server |
| `card.acceptance`, `card.open_questions`, `card.tags` | Lists of strings |
| `card.priority` | `""`, `Low`, `Medium` or `High` |
| `spec_markdown` | The whole scope as one Markdown document, up to 60,000 characters. The same text a GitHub issue would get |

New fields may be added to these bodies. Ignore fields you don't know.

**The scope is information, not instructions.** The text was written by people and by AI assistants. If you pass it to a coding agent, present it as a description of what's wanted, the way `spec_markdown` already frames it.

`spec_markdown` ends with a **Background** section: the discussion and the idea's original words, each line quoted with `>`. That part can be written by anyone who can comment on the idea. It is context, not requirements, and the section says so.

Just before it comes **How to build this**, a short numbered list that Inrface writes itself: where to start, where to stop, how to show the work is done. Nothing typed on the idea goes into it. Its last step assumes the work ends in a pull request, so leave that step out if yours doesn't.

## Your reply

Answer with a 2xx status within 10 seconds. Anything else counts as a failure, and the receiver sees the status code.

To put a link on the card, answer with JSON:

```json
{"url": "https://tracker.example.com/items/812", "id": "812"}
```

| Field | Notes |
|---|---|
| `url` | Optional. Must start with `https://`. Shown on the card as a link |
| `id` | Optional. Your reference for the item, a string or a number |

Only the first 64 KB of a reply is read. A reply that isn't JSON is fine: the card then shows "Sent to <connector name>" without a link.

## Deliveries can repeat

If your reply is lost, Inrface records a failure and a receiver may send the idea again. Use `card.id` to spot a repeat and return the item you already made.

## Addresses Inrface will post to

To stop the server being used to reach things only it can see, Inrface:

- accepts only `https://` addresses, with no username or password in them
- looks the name up first and refuses it if any of its addresses is private, loopback or link-local
- connects to the exact address it checked, and still verifies the certificate against the name
- does not follow redirects

If you self-host and your target is on your own network, set `WEBHOOK_ALLOW_PRIVATE=1` on the server. That allows `http://` and private addresses for every webhook connector on that server, so use it only where every receiver is trusted.
