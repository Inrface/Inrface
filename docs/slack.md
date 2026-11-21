# Inrface and Slack

With a Slack connector, ideas come into a project from a Slack workspace, and receivers file them without leaving Slack. Slack never starts a build.

[TOC]

## What it does

- **A channel for ideas.** Every new message in one channel becomes a card. Inrface replies in that message's thread.
- **`/idea` anywhere.** `/idea a weekly summary email` makes a card from any channel. Inrface posts the idea in the ideas channel so it has a thread.
- **Send to Inrface.** A shortcut in a message's **More actions** menu turns that message into a card, credited to whoever wrote it.
- **Buttons for receivers.** The thread has **Approve**, **Defer** and **Clarify**.
- **One conversation.** Replies in the thread are added to the card. Questions, comments and status changes in Inrface are posted in the thread.

| Button | What it does to the card |
|---|---|
| Approve | Moves it to **Immediate roadmap** |
| Defer | Moves it to **Far down the line** |
| Clarify | Asks for a question, then moves the card to **Needs info** and posts the question in the thread |

Approve files the idea. Scoping and **Start build** still happen in Inrface.

## Before you start

- Inrface needs an address Slack can reach over HTTPS.
- You need permission to create and install apps in your Slack workspace.
- **If Inrface is behind a sign-in wall such as Cloudflare Access, open the Slack path first.** Slack checks the address while it creates the app. If it can't get through, creating the app fails and Slack only says to try again. See [Behind Cloudflare Access](#behind-cloudflare-access) and do that before step 1.
- It can all be done from a phone, but it goes between two apps (Inrface and Slack) several times. A computer is easier. Settings keeps what you have done, so you can leave and come back.

## Set it up

In Inrface, open **Settings → Connectors → Chat and tracker connectors**, choose **Slack** and press **Add connector**. Inrface then walks you through five steps, showing each one when the one before it is done. The Test button only appears at the end.

1. **Give it the channel ID.** In Slack, open the ideas channel, tap its name at the top and scroll to the bottom of the details. Copy the **Channel ID**. It starts with `C` (or `G` for a private channel). Paste it into Inrface and save.
2. **Create the Slack app.** Press **Copy the app manifest**. Inrface writes it for you, with this server's address in it. Then:
    1. Go to [api.slack.com/apps](https://api.slack.com/apps) and press **Create New App**.
    2. Choose **From a manifest** and pick your workspace.
    3. Slack shows a box with two tabs, **JSON** and **YAML**. It opens on JSON. **Choose YAML**, because the manifest is YAML. Pasting it in the JSON tab gives an error.
    4. Press **Next**, then **Create**.

    Then press **I've created the app** in Inrface.
3. **Paste the signing secret.** In the Slack app, open **Basic Information** in the left menu and scroll to **App Credentials**. Press **Show** next to **Signing Secret**, copy it, and paste it into Inrface.
4. **Install the app and paste the bot token.** In the Slack app, open **Install App**, press **Install to Workspace** and then **Allow**. Copy the **Bot User OAuth Token**. It starts with `xoxb-`. Paste it into Inrface.
5. **Add the app to the channel, then test.** In the ideas channel, type `/invite @Inrface` and pick the app. The app has to be in the channel, even a public one, or it can't reply there. Press **Test it**. It names the workspace and the channel when it works.

Once a test has worked, the connector is listed as set up, with **Test**, **Edit**, **Turn off** and **Remove**.

| Setting | Notes |
|---|---|
| Ideas channel ID | Required. A public or private channel. Starts with `C` or `G` |
| Signing secret | From **Basic Information**. Until it is saved, Inrface refuses everything Slack sends, except Slack's first check of the address while it creates the app |
| Bot token | From **Install App**. Until it is saved, cards are still made but Inrface can't post in Slack |

Set `PUBLIC_URL` on the server, such as `PUBLIC_URL=https://ideas.example.com`. Messages in Slack then link to the card, and the Request URL is right even if you open Settings from another address.

One project can have more than one Slack connector, each with its own Slack app and channel.

## Behind Cloudflare Access

Slack can't sign in, so let its requests through to one path and nothing else.

1. In Cloudflare Zero Trust, add a new **self-hosted application** for your hostname with the path `hooks/slack`. It covers everything under that path.
2. Give it one policy with the action **Bypass** and the selector **Everyone**.

Your main application keeps its sign-in. The Slack path has no sign-in, and doesn't need one: Inrface checks Slack's signature on every request and refuses anything else.

## Who can do what

Inrface matches a Slack account to a person in `innerface.toml` by the email address on the Slack account.

| Person in Slack | What they can do |
|---|---|
| A receiver on the connector's project | Send ideas, reply, and use Approve, Defer and Clarify |
| A giver on the connector's project | Send ideas and reply |
| Anyone else in the workspace | Send ideas and reply. Shown in Inrface as "Dana (Slack)" |

- Someone matched by email has their role on the connector's project only, even if they have roles on other projects.
- Inrface matches an account only when Slack reports its email address as confirmed. It doesn't match guests from other organisations in shared channels, bots or deactivated accounts. All of these can still send ideas, as anyone else.
- Each connector checks people with its own workspace. What one workspace says about a user ID is never used for another.
- An email address is saved only when it belongs to someone already listed in `innerface.toml`. For everyone else Inrface keeps the Slack user ID and display name.
- If Slack can't be reached to check who someone is, they are treated as anyone else for that request.

**Who you are trusting.** Whoever controls email addresses in your Slack workspace, such as a workspace admin, can make a Slack account that matches a receiver. That account can then file ideas and ask questions from Slack. It can't scope an idea, start a build, change Settings or sign in to Inrface. Connect only a workspace whose admins you trust that far.

## What goes where

| Happens in | Shows up in |
|---|---|
| A new message in the ideas channel | A new card. Inrface replies in the thread with the buttons |
| `/idea` or **Send to Inrface** | A new card, and a post in the ideas channel that becomes its thread |
| A reply in the thread | A comment on the card. If the card is in **Needs info** and the reply isn't from a receiver, the card moves to **Answered** |
| A question asked in Inrface | A post in the thread that mentions the idea's author |
| A comment in Inrface | A post in the thread |
| A status change, including **Start build** and **Done / Live** | A post in the thread |

The card in Inrface shows a **Slack thread** link.

Not copied: edits and deletions of Slack messages, reactions, files and images, and messages from bots.

## Limits

- An idea is cut at 5,000 characters.
- Each connector takes up to 30 new ideas and 120 thread replies a minute. More than that are skipped, and the server log says so.
- A message becomes a card once. Using **Send to Inrface** on a message that already has a card answers "That message is already in Inrface."
- If the server restarts while an idea is being added, that idea can be lost. Send it again with the shortcut.

## What Inrface does with the text

- Slack text is saved as written. It isn't scrubbed the way [intake API](/docs/intake-api) text is, because it comes from people in your own workspace.
- Mentions and links are turned into plain text: `<@U123>` becomes `@Name`.
- Text Inrface posts back into Slack is escaped, so an idea containing `<!channel>` can't notify the channel.
- Nothing from Slack starts a build. Only a receiver pressing **Start build** in Inrface does.
- What a build sends has two parts. The requirements are what a receiver wrote when scoping. The idea's original words and the discussion, thread replies included, follow as quoted background that is marked as not part of the requirements. Read the thread before you start a build, because the coding agent will see it too.

## If it fails

| What you see | What to check |
|---|---|
| Slack won't create the app and only says to try again | It couldn't reach the address. Open the `hooks/slack` path through Cloudflare Access first, then try again |
| Slack says the manifest is invalid | The manifest was pasted into the JSON tab. Choose the **YAML** tab |
| Slack says the Request URL didn't respond or isn't verified | The address isn't reachable from the internet, or a sign-in wall is in the way |
| Messages in the channel don't become cards | The app isn't in the channel, or the channel ID is wrong. Press **Test** |
| Cards appear but nothing is posted in Slack | The bot token is missing or wrong. Settings shows the last error on the connector |
| "Slack refused the bot token" | Copy the token again from **Install App**. It starts with `xoxb-`, and it isn't the signing secret or a token from another page |
| "The app isn't in the channel yet" | In the ideas channel, type `/invite @Inrface` and pick the app |
| "The Slack app is missing a permission" | Add the scope under **OAuth & Permissions**, then reinstall the app |
| "Only a receiver on … can do that" | The email address on that Slack account doesn't match a receiver in `innerface.toml`, or Slack doesn't report the address as confirmed. In the second case the server log has a line saying so |
| A button shows a warning sign | Slack didn't get an answer within 3 seconds. Check the server is up and reachable |

## For developers

Slack sends everything to one address: `POST /hooks/slack/{connector id}`. It handles Events API callbacks (JSON), slash commands and interactions (form-encoded).

- **Signature.** Each request is checked against the connector's signing secret: HMAC-SHA256 over `v0:<timestamp>:<raw body>`, compared in constant time, with timestamps more than 5 minutes off refused.
- **Answers.** A bad signature gets a 401. An unknown, switched-off or non-Slack connector gets a 404. A body over 256 KB gets a 413.
- **Timing.** Slack is answered straight away and the work is done afterwards, apart from opening the Clarify box, which Slack only allows for 3 seconds after the press.
- **Repeats.** Slack re-sends an event if the answer was slow. Each event id is handled once, and a unique index on the message a card came from stops a second card. A request with the same timestamp and signature as one already handled is answered with a 200 and nothing is done, so a captured request can't be played back.
- **Busy.** If more than 500 pieces of work are already waiting, the answer is a 503 and nothing is done with the request. Slack sends events again after a failure.

Scopes the manifest asks for:

| Scope | Why |
|---|---|
| `commands` | The `/idea` command and the message shortcut |
| `chat:write` | Posting in threads |
| `channels:history`, `groups:history` | Reading new messages in the ideas channel and replies in its threads |
| `channels:read`, `groups:read` | Naming the channel when you press **Test** |
| `users:read`, `users:read.email` | Names, and matching people by email address |

The app subscribes to `message.channels` and `message.groups`. It only hears channels it has been invited to. The code is `innerface/slack.py`.
