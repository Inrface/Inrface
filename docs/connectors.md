# Inrface connectors

A connector links one project to one outside system. Receivers add them under **Settings → Connectors → Chat and tracker connectors**.

[TOC]

## What a connector does

There are two sorts. Some take builds: when a receiver presses **Start build** on a scoped idea, Inrface sends the scope to every place the project builds:

- the project's GitHub repo, if one is linked, as a labeled issue
- each build connector that is switched on

Others bring ideas in, and never receive a build.

| Kind | Sort | What it does | Guide |
|---|---|---|---|
| Webhook | Takes builds | Start build posts the scope, signed, to an address you choose | [Webhook reference](/docs/webhook) |
| Linear | Takes builds | Start build opens an issue in one Linear team | [Linear setup](/docs/linear) |
| Slack | Brings ideas in | Messages in a channel, `/idea` and a message shortcut become cards. Receivers file them from the thread | [Slack setup](/docs/slack) |

A project can have up to 12 connectors, and more than one of the same kind.

## What happens on Start build

A build starts when a receiver presses **Start build** in the app. An AI app can only ask: the request waits on the idea until a receiver presses the button.

1. Inrface sends the scope to each place in turn.
2. Each place that accepts it is recorded on the card as a link, such as `Issue #12` or `ENG-45`.
3. The card moves to **In dev for testing** and its scope is locked, as long as at least one place accepted it.
4. If a place fails, the others still go ahead. The card shows a **Send to … too** button for the ones that are missing.

Pressing Start build again never sends anything twice. It only retries places that don't have the idea yet. The same button appears if you add a connector after a build has started.

If every place fails, nothing changes on the card and you see the first error.

## Status coming back

Only GitHub reports progress back to the card: a merged pull request or a closed issue moves it to **Done / Live**. Build connectors are one-way. If an idea goes only to a connector, move the card to Done by hand when the work ships.

A Slack connector works in both directions, but only for conversation: replies, questions and status changes. See [Slack setup](/docs/slack).

## Secrets

- A secret you type in, such as an API key, is never shown again. Settings shows only its last four characters.
- To change a secret, edit the connector and type the new one. Leaving the box blank keeps the saved one.
- A secret that Inrface makes for you, such as a webhook signing secret, is shown once when it is made.
- Set `INNERFACE_SECRET_KEY` on the server to encrypt saved secrets in the database. See [Encrypting saved secrets](#encrypting-saved-secrets).

Only receivers on a project can see or change its connectors.

## Testing a connector

**Test** makes one real, harmless request: a signed `ping` for a webhook, a read of your team for Linear, a read of the workspace and channel for Slack. Settings then shows when the connector last worked, or the last error.

## Turning one off

**Turn off** keeps the settings but stops builds going there, or ideas coming in. **Remove** deletes the connector and its secrets. Cards, and links already on cards, stay.

## Encrypting saved secrets

Without a key, connector secrets and a GitHub token saved from Settings are stored in the database as they are. Anyone who gets a copy of the database or a backup can read them.

To encrypt them:

1. Make a key: `openssl rand -base64 48`
2. Add it to `.env` as `INNERFACE_SECRET_KEY=<the key>` and restart.

At startup Inrface encrypts anything already saved. From then on the database holds only encrypted values, and the key lives only in `.env`.

Keep the key somewhere safe. If it is lost or changed, the saved secrets can't be read: Settings marks each affected connector, and you type its secrets in again.

## The API

The Settings screen uses these. They need a signed-in receiver and the `X-Requested-With: Inrface` header on anything but a GET.

| Method and path | What it does |
|---|---|
| `GET /api/projects/{project}/connectors` | The kinds available, with their fields, and the project's connectors |
| `POST /api/projects/{project}/connectors` | Add one: `{"kind": "linear", "name": "Linear", "values": {"api_key": "...", "team": "ENG"}}` |
| `PUT /api/projects/{project}/connectors/{id}` | Change `name`, `values`, `enabled`, or pass `"regenerate": true` for a new generated secret |
| `DELETE /api/projects/{project}/connectors/{id}` | Remove it |
| `POST /api/projects/{project}/connectors/{id}/test` | Try it. Returns `{"ok": true, "detail": "..."}` or a 502 with the reason |

A connector in a response looks like this. Secrets appear only as `set` and `hint`.

```json
{
  "id": "5f0c…",
  "project_id": "main",
  "kind": "linear",
  "kind_label": "Linear",
  "name": "Linear",
  "enabled": true,
  "values": {"team": "ENG", "label": "from-inrface"},
  "secrets": {"api_key": {"set": true, "hint": "9xQ2"}},
  "unreadable": false,
  "created_at": "2026-11-12T18:04:11.120Z",
  "last_ok_at": "2026-11-12T18:05:02.431Z",
  "last_error": ""
}
```

Create and update responses also carry `reveal`: any secret Inrface just generated, shown that one time.

A connector may also carry `notes`: things to copy into the other system, such as Slack's Request URL. Each is `{"label", "value", "show"}`. `{origin}` in a value stands for the address the app is open at, and is used when `PUBLIC_URL` isn't set.

## Adding a new kind

Each kind is one small Python module in `innerface/`. It describes its settings as a list of fields and supplies functions:

- `test(settings, conn)` returns a short line saying what worked. Every kind has one.
- `send_build(settings, conn, build)` sends the scope and returns a `Sent(label, url, external_id)`. Leave it out for a kind that only brings ideas in.
- `to_thread(settings, conn, link, event)` is optional. It is called when something happens to a card that has a `thread` link from this connector: a question, a comment or a status change.
- `notes(settings, connector_id)` is optional. It returns things for the receiver to copy into the other system.

Saving, permissions, the Settings form and keeping secrets out of responses are shared. `innerface/linear.py` is a complete example in about 100 lines. Register the module at the bottom of `innerface/connectors.py`.

Raise `ConnectorError` with a message that is safe to show a receiver. Never put a secret, or the outside system's raw reply, in that message.
