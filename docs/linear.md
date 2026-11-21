# Inrface and Linear

With a Linear connector, **Start build** opens an issue in one Linear team. Nothing is read back from Linear.

[TOC]

## Set it up

1. In Linear, open **Settings**, then **Security and access**, and create a **personal API key**. Give it permission to create issues. If Linear offers it, limit the key to the one team.
2. Find your team's key: the short code in front of its issue numbers, such as `ENG` in `ENG-123`.
3. In Inrface, open **Settings → Connectors → Chat and tracker connectors**, choose **Linear** and press **Add connector**.
4. Enter the API key and the team key. Optionally enter the name of an existing Linear label to put on each issue.
5. Save, then press **Test**. Inrface names the workspace and team it reached.

| Setting | Notes |
|---|---|
| API key | A personal API key. Shown only as its last four characters after saving |
| Team key | One to ten letters or digits. Saved in capitals |
| Label | Optional. Must already exist in Linear, on that team or for the whole workspace. If it doesn't, issues are opened without it and Test says so |

Issues are opened as the person who owns the API key.

## What Start build sends

- **Title**: the card's title.
- **Description**: the whole scope in Markdown (summary, who it's for, the problem, scope, done-when checklist, what isn't decided yet, what the code says, how to build it, then the discussion and the idea's original words as quoted background), then a line naming the Inrface card and who started the build. If `PUBLIC_URL` is set on the server, that line links back to the card.
- **Label**: the one you named, if it exists.

The issue lands in the team's default state. The card then shows the issue's identifier, such as `ENG-45`, as a link.

## What it doesn't do

- **No status comes back.** When the work ships, move the card to **Done / Live** in Inrface by hand. Only GitHub reports progress back.
- **No changes after sending.** The scope is locked in Inrface once a build starts. Edit the issue in Linear.
- **No comments are copied** in either direction.

## If it fails

| Message | What to check |
|---|---|
| Linear refused the API key | The key was deleted, mistyped, or can't create issues |
| Linear has no team with the key … | The team key is wrong, or the API key can't see that team |
| Linear is asking us to slow down | Linear's rate limit. Wait a minute and press **Send to Linear too** on the card |
| Couldn't reach Linear | A network problem between your server and `api.linear.app` |

A failed send changes nothing in Linear. The card keeps a **Send to … too** button, and pressing it never creates a second issue for a place that already has one.

## For developers

The connector calls Linear's GraphQL API at `https://api.linear.app/graphql`, with the key in the `Authorization` header. It makes at most three calls per build: find the team, find the label, create the issue. The code is `innerface/linear.py`.
