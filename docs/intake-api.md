# Inrface intake API

**Version 1.1** · Base path `/api/intake/v1`

Other systems can feed Inrface through the intake API, for example a product's website or an internal tool. It accepts exactly two kinds of data:

| | **Ideas** | **Signals** |
|---|---|---|
| What | One feature idea or request | Daily totals per tool: ratings and usage |
| Becomes | A card in the project's **New** column | The **Tools** view, and trends next to related ideas |
| Contains | Text (scrubbed), tool, pseudonymous author reference, link back | Counts only |

**Inrface is not a ticketing system.** Do not send bug reports, support requests, complaints or individual reviews. Handle those in your own support tools. Nobody in Inrface replies to the people who wrote ideas, and there are no assignees or deadlines.

---

## Contents

1. [Setup](#setup)
2. [Authentication](#authentication)
3. [Conventions](#conventions)
4. [Ideas](#ideas)
   - [Send an idea](#send-an-idea)
   - [Check one idea](#check-one-idea)
   - [List changed ideas](#list-changed-ideas)
   - [Erase an idea](#erase-an-idea)
5. [Signals](#signals)
6. [Statuses](#statuses)
7. [Personal data](#personal-data)
8. [Errors and retries](#errors-and-retries)
9. [Testing](#testing)
10. [Changelog](#changelog)

---

## Setup

Each sending system is a **source**. Each source belongs to one project and has its own key.

1. **Create the source.** In Inrface, open **Settings → Connectors → Intake keys** for the project, enter a name (for example `Website`), and tap **Create key**. Copy the key right away. Inrface shows it **once** and stores only a hash of it. Anyone who receives a card from this source sees the source's name.
2. **Let the sender past Cloudflare Access.** This step applies only if the Inrface install sits behind Cloudflare Access.
   - In Cloudflare Zero Trust, go to **Access → Service credentials → Service tokens → Create service token** and copy the Client ID and Client Secret.
   - Open the Inrface Access application and add a policy with **Action: Service Auth**, which includes **Service Token: (that token)**. Don't add IP rules. Cloud platforms call out from shifting addresses.
   - A service token gets a request past Access, but it is not a person. Inrface refuses it everywhere except the intake API.
3. **Store four values as secrets** in the sending system: the base URL, the Inrface key, and the two service-token values. Send them from your **server only**. Never put the key in a browser, mobile app or public repo.

To **rotate** a key, create a second source, switch the sender to the new key, then **Revoke** the old one. Revoking takes effect immediately.

---

## Authentication

Every request carries:

```http
Authorization: Bearer inr_1a2b3c4d.ZmFrZS1zZWNyZXQtZm9yLWRvY3MtMTIzNDU2Nzg5MA
CF-Access-Client-Id: <service token client id>          (behind Cloudflare Access)
CF-Access-Client-Secret: <service token client secret>  (behind Cloudflare Access)
Content-Type: application/json                          (on POST)
```

The key has the form `inr_<8 hex>.<secret>`. The first part identifies the source, and the rest is the secret.

| Response | Meaning |
|---|---|
| `401` | Missing or wrong key, or the request didn't come through Access |
| `403` | The key was revoked, or its project no longer exists |

---

## Conventions

- **JSON in, JSON out.** Text is UTF-8. Arabic and other scripts are fine.
- **Unknown fields are rejected** with `422`. This is deliberate. If a field like `email` or `name` slips into a payload, the request fails loudly instead of quietly storing personal data.
- **Error bodies never echo your values.** A `422` names the field and the problem, so you can log it without logging user text:

  ```json
  {"detail": "email: Extra inputs are not permitted",
   "errors": [{"field": "email", "problem": "Extra inputs are not permitted"}]}
  ```

- **Timestamps** are ISO-8601 in UTC, with milliseconds and a `Z` suffix, for example `2026-11-09T18:37:54.598Z`. Dates are `YYYY-MM-DD`, also in UTC.
- **Rate limit:** 60 requests a minute per key. Over that, you get `429` with `Retry-After: 60`.
- **Idempotent by design.** Ideas are keyed by your `external_id`, and signals by tool and day. You can safely resend after a timeout.

---

## Ideas

### Send an idea

`POST /api/intake/v1/ideas`

| Field | Type | Required | Notes |
|---|---|---|---|
| `external_id` | string | yes | Your ID for this idea, unique within your source. 1–128 characters from `A-Z a-z 0-9 . _ : -`. |
| `text` | string | yes, unless `url` is given | The idea, up to 5000 characters. **Strip personal data first** (see [Personal data](#personal-data)). |
| `title` | string | no | Up to 120 characters. Defaults to the start of `text`. |
| `tool` | string | no | Which part of your product it's about, as a stable key, not a translated label: `quiz`, `lesson-planner`. Lowercase, `[a-z0-9][a-z0-9_.-]{0,63}`. Use the same keys in [signals](#signals) so ideas and ratings line up. |
| `author_ref` | string | no | Up to 128 characters. An **opaque, pseudonymous** reference to the author, such as `HMAC-SHA256(your_secret, user_id)` in hex. Never an email, name or raw user ID. |
| `url` | string | no | An `https://` link back to the original in your system, up to 500 characters. Only people who can sign in to your system can open it. |
| `language` | string | no | A language tag, such as `ar` or `en`. |
| `submitted_at` | string | no | When the user submitted it (ISO-8601). Accepted for your records. |

**Link-only mode:** if your data shouldn't leave your system, send `url` without `text`. The card then says the text stays with you and links to it.

```bash
curl -sS https://ideas.example.org/api/intake/v1/ideas \
  -H "Authorization: Bearer $INRFACE_KEY" \
  -H "CF-Access-Client-Id: $CF_ID" -H "CF-Access-Client-Secret: $CF_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"external_id": "fe_8c1d", "text": "Let me export quizzes as a printable PDF.",
       "tool": "quiz", "author_ref": "9f2c…", "language": "en",
       "url": "https://admin.example.com/feedback?id=8c1d"}'
```

**`201 Created`**: a new card.

```json
{"external_id": "fe_8c1d", "card_id": "5dfeee4218db4956884e46a4f42bf3a0",
 "status": "received", "updated_at": "2026-11-09T18:37:54.598Z",
 "redactions": 0, "flagged": false, "duplicate": false}
```

**`200 OK`** has the same shape with `"duplicate": true`. It means this `external_id` was already received. Nothing changes, and the existing card is returned. Retries are safe.

| Response field | Meaning |
|---|---|
| `redactions` | How many items Inrface's own scrubber replaced. Anything above 0 means something got past your redaction. Worth logging. |
| `flagged` | The card shows a "personal details were removed; some may remain" warning to the people reviewing it. |

### Check one idea

`GET /api/intake/v1/ideas/{external_id}`

```json
{"external_id": "fe_8c1d", "card_id": "5dfe…", "status": "planned", "updated_at": "2026-11-12T09:01:13.120Z"}
```

`404` if this source never sent that `external_id`. A source can only see its own ideas.

### List changed ideas

`GET /api/intake/v1/ideas?updated_since=<ISO-8601>&limit=<1–200, default 100>`

This returns your source's ideas that changed after `updated_since`, oldest change first. Leave out `updated_since` to start from the beginning.

```json
{"ideas": [{"external_id": "fe_8c1d", "card_id": "5dfe…", "status": "planned", "updated_at": "2026-11-12T09:01:13.120Z"}],
 "next_since": "2026-11-12T09:01:13.120Z"}
```

**To keep statuses in sync,** call this on a schedule (every 15–60 minutes is plenty), store `next_since`, and pass it back next time. Repeat until `ideas` comes back empty. An idea can appear again with the same status, because the reviewer edited something else, so update only when the status actually changed. If two ideas were merged as duplicates, the merged one reports the status of the idea it was merged into.

### Erase an idea

`DELETE /api/intake/v1/ideas/{external_id}` → `204 No Content`

Use this when a user deletes their account or their feedback. Inrface removes everything your system sent: the text, `author_ref` and `url`. It also removes the discussion and any internal notes about the idea, unless a build already started. In that case Inrface keeps the scope its own team wrote, so the work stays traceable, and still removes your text. If the idea was merged into another one, the note that copied its words onto that card is removed as well.

Afterwards, `GET` reports `"status": "deleted"`. Erasing twice is fine.

> Text that was already copied into a GitHub issue for a build lives in that repository and is outside the intake API. Inrface copies the reviewer's scope into issues, not the original text, but reviewers can quote it.

---

## Signals

`POST /api/intake/v1/signals`

Signals are **daily totals per tool**. They never contain text or user identifiers. Inrface uses them to show how each tool is doing and whether that's changing. A signal can carry ratings, usage, or both.

```json
{"signals": [
  {"tool": "quiz", "date": "2026-11-09", "positive": 41, "negative": 6, "neutral": 0,
   "rating_count": 47, "rating_sum": 211, "comments": 3, "tool_label": "Quiz",
   "uses": 1250, "failures": 9, "reruns": 140},
  {"tool": "rubric", "date": "2026-11-09", "uses": 12}
]}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `tool` | string | yes | Same keys as ideas' `tool`. |
| `date` | string | yes | The UTC day the totals cover, `YYYY-MM-DD`. |
| `positive` / `negative` / `neutral` | integer | no, default 0 | How many ratings were good, bad or in between. With 1–5 stars: 4–5 positive, 1–2 negative, 3 neutral. With thumbs up/down: neutral is 0. |
| `rating_count` / `rating_sum` | integer | no, default 0 | For an average star rating: how many ratings, and their sum. Leave both at 0 if you only have thumbs. |
| `comments` | integer | no, default 0 | How many ratings came with a written comment. Send the count only, never the comments themselves. |
| `tool_label` | string | no | A human name for the tool, up to 80 characters, shown in Inrface. |
| `uses` | integer or null | no | How many times the tool ran that day. *(v1.1)* |
| `failures` | integer or null | no | How many of those runs errored. Requires `uses`, and can't exceed it. *(v1.1)* |
| `reruns` | integer or null | no | How many runs repeated the same tool for the same user within 10 minutes of a previous run, which often means the first result wasn't good enough. Requires `uses`, and can't exceed it. *(v1.1)* |

**Missing is not zero.** Leave out a usage field, or send `null`, when you don't measure it. Inrface stores it as unknown and leaves that day out of the rate, instead of counting it as zero failures. Send `0` only when you measured it and it was zero. The rating fields are different: they default to `0`.

- Up to 500 signals per request.
- **Sending the same tool and day again replaces it, every field.** A field you leave out on the resend becomes `0` (ratings) or unknown (usage). It is not kept from before. A nightly job that resends the last 7 days is the recommended pattern. It fixes late or corrected data for free.
- Combine across your customers or tenants before sending. Inrface doesn't need per-customer numbers.

Response: `200 {"accepted": 1}`.

In **Ideas → Tools**, Inrface shows, per tool, over the last 30 days against the 30 before:
- the share of positive ratings (a drop of 10 points or more is highlighted)
- uses
- failure rate, `failures ÷ uses` (a rise of 3 points or more is highlighted)
- rerun rate, `reruns ÷ uses` (a rise of 10 points or more is highlighted)
- how many ideas mention the tool

---

## Statuses

Statuses are deliberately generic, so you can show them to your own users without promising features or dates.

| Status | Meaning |
|---|---|
| `received` | Arrived, not reviewed yet |
| `planned` | On the roadmap |
| `later` | Kept for the long term, not soon |
| `building` | Being built |
| `shipped` | Done and live |
| `declined` | Not going ahead |
| `deleted` | Erased at your request |

New statuses may be added. Treat any value you don't recognize as `received`.

---

## Personal data

Responsibility is layered:

1. **You strip personal data before sending.** This is the primary protection. Your system is the only one that knows what's sensitive in your data.
   - Don't send names, emails, phone numbers or account IDs.
   - Remove names you know about from free text, such as user, student or customer names.
   - Use `author_ref` (a keyed hash) if you need to tell authors apart, and link-only mode if the text must not leave your system at all.
2. **Inrface scrubs again** as a backstop:
   - email addresses → `[email]`
   - links → `[link: host]` (the path and query are dropped)
   - runs of 7 or more digits, including Arabic-Indic digits and phone or ID formatting → `[number]`
   - This covers the idea's `title` and `text`, `author_ref`, and a rating's `tool_label`.
   - Any replacement in an idea flags the card for the people reviewing it.
   - The scrubber **cannot** reliably catch people's names, so step 1 matters.
3. **Fields Inrface never accepts** are rejected, not ignored (see [Conventions](#conventions)).
4. **Erasure** is a single call (see [Erase an idea](#erase-an-idea)).

Inrface is self-hosted. Ask whoever runs your install where it's hosted and how long it keeps data before sending anything covered by data-residency rules.

---

## Errors and retries

| Code | Meaning | What to do |
|---|---|---|
| `200` / `201` | Done | – |
| `204` | Erased | – |
| `401` | Key missing or wrong, or no Access service token | Stop. Fix the configuration. Don't retry in a loop. |
| `403` | Key revoked | Stop. Get a new key. |
| `404` | Unknown `external_id` for this source | Don't retry. |
| `422` | Invalid request. `errors[].field` says which field | Record it and don't retry the same payload. |
| `429` | Rate limited | Wait for `Retry-After` seconds, then retry. |
| `5xx`, timeouts | Inrface or the network had a problem | Retry with backoff. Resending is safe. |

A good sender stores whether each idea was forwarded, plus the last error, and has a scheduled sweep that retries the ones that failed with `429` or `5xx`.

---

## Testing

- For local development, run Inrface in dev mode. It skips Cloudflare Access, though the key is still required. The setup steps are in the README.
- Create a source named something like `Website (test)`, and revoke it when you're done.
- The Inrface repository's tests (`tests/test_app.py`, "intake API" section) show every behavior described here.

---

## Changelog

- **v1.1** (2026-11-09): optional usage counts on signals: `uses`, `failures`, `reruns`. Existing senders are unaffected.
- **v1** (2026-11-09): ideas (send, check, list changes, erase), signals, sources with revocable keys.
