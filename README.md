# Inrface

A private idea inbox. Givers bring ideas as cards. Receivers review them, send questions back, file them on the roadmap, scope them, and start builds. A build opens a labeled GitHub issue for whatever coding agent watches that repo.

Everyone can use it from the web app or from their own AI app (Claude, ChatGPT, Gemini or any other MCP client), including by voice where that app supports it. A giver talks an idea through with their AI app, which asks the questions that make it possible to scope and then sends it in with a write-up next to the giver's own words. A receiver reviews and scopes the same way, and can ask for a build to be started. Inrface serves an MCP endpoint at `/mcp` and tells each person's AI app how to help someone in their role.

Givers see every idea in their projects, with people's names. They don't see each other's email addresses or the conversation someone else had while shaping an idea.

Runs in Docker, with no open ports. Traffic arrives only through a Cloudflare Tunnel, and Cloudflare Access decides who can reach it. The app also verifies Access's signed token on every request, including MCP calls.

## Try it

To look around on your own computer, with made-up people and no sign-in:

```
docker compose -f docker-compose.try.yml up --build
```

Then open `http://localhost:8080`. This mode trusts anyone who can reach it, so it listens on your machine only.

It starts empty. To fill it with sample ideas, run this in a second terminal and reload the page:

```
docker compose -f docker-compose.try.yml exec app python -m innerface.demo
```

## Set up on a server

You need Docker on an always-on machine (a Proxmox VM works well), and a domain on Cloudflare. The steps in short are below. [docs/install.md](docs/install.md) walks through each one and says what to do when one goes wrong.

1. **Tunnel:** Zero Trust > Networks > Tunnels > Create, type Cloudflared. Copy the token. Add a public hostname pointing at `http://app:8080`.
2. **Access:** Zero Trust > Access > Applications > add a self-hosted application for that hostname. Add Google as a login method and allow only the people in your config. Set the session length to the longest you're comfortable with, since the phone app has to sign in again when it expires. Copy the Application Audience (AUD) tag.
3. **MCP sign-in:** on the same application, open Advanced settings and turn on **Managed OAuth**, with dynamic client registration allowed. This lets AI apps sign in through Access. Cloudflare suggests a short access token lifetime (5 to 15 minutes) and a longer grant session (1 to 2 weeks).
4. **People and projects:** copy `innerface.example.toml` to `innerface.toml` and fill it in.
5. **Secrets:** copy `.env.example` to `.env`, fill it in, and run `chmod 600 .env`. For each project with a repo, create a fine-grained GitHub token limited to that repo, with Issues read and write, Pull requests read and Contents read.
6. **Check and start:** `docker compose pull` fetches the ready-made image (or build your own with `docker compose build`). `docker compose run --rm app python -m innerface.check` reads your settings and says in plain words what is missing or wrong. Then `docker compose up -d`. It restarts on its own after a reboot.
7. **Phones:** open the address, then Share > Add to Home Screen. Open it from the home screen and tap Turn on alerts on the Ideas tab. Alerts need iOS 16.4 or later.
8. **AI app:** each person adds a custom connector in their own AI app with the URL `https://<your hostname>/mcp`, and signs in when asked. If a login is refused, the server's log says why (`docker compose logs app`).

Make sure the machine doesn't sleep.

## Signing in without Cloudflare Access

Inrface can also sign people in itself, with nothing in front of it. People sign in with Google, and AI apps connect to the app directly and wait for the person to press **Allow**. Each person gets an **Account** screen that lists the browsers and AI apps signed in as them, with a button to end each one. Other ways to sign in can be added by a separate package.

It runs from its own compose file, `docker-compose.builtin.yml`, so the Cloudflare Access setup above can't be switched off by accident. This way of signing in is new, and nobody outside the project has reviewed it; Cloudflare Access is still the safer choice. [docs/sign-in.md](docs/sign-in.md) has the steps and says what it does and doesn't protect.

## People and projects

`innerface.toml` names the first people and projects. After that, a receiver changes them under **Settings → People**, with no restart:

- **Add someone** to a project as a giver or a receiver, change what they are, or take them out. Taking someone out ends their access at once. Their ideas stay.
- **Start a project.** It gets its own ideas, people, repo and GitHub token.
- **Rename a project.**

A receiver manages the projects they receive for, and every project keeps at least one receiver. The other receivers get an alert on each change, and the panel lists the recent ones. Only receivers see email addresses.

Changes made in the app are saved with the data and win over the file. Nothing is sent to a person you add: the app gives you a short message to pass on. Behind Cloudflare Access, add their address to the Access policy as well, or Cloudflare turns them away before the app sees them. Taking someone out of their last project also signs them out of the app's own sign-in, where that is used.

AI apps can't change who is in a project. That stays in the web app.

## Asking about the code

When a project is linked to a repo, each person's AI app can look through that repo's code while an idea is talked through. Someone can ask "do we have a login screen?" or "is there a second sign-in step?" and get an answer from what is built. When they bring an idea, the AI app checks first whether it already exists or would clash with something, and says so before the idea is sent. Receivers get the same help while scoping, plus a list of recent pull requests and commits.

- It needs the project's GitHub token to have **Contents** read access. **Check connection** under **Settings → Connectors → GitHub and build flow** says whether it does.
- Receivers choose who can ask, under **Settings → Connectors → GitHub and build flow**: everyone in the project (the default), only receivers, or nobody.
- Anything committed to the repo can be read this way by the people you allow, so check what is in it before you leave this on for givers. Files that look like they hold passwords or keys (`.env`, `*.pem`, `*.key`, `credentials.json` and the like) are never read.
- Inrface holds a copy of the default branch in memory and refreshes it when the branch moves. Nothing is written to disk and nothing from the repo is run. Very large repos are held in part.
- The code shows what was built. It can't show how a live site is configured, and the AI app is told to say so.

What the AI app finds doesn't have to be found again. It can save it on the idea as **What the code says**: a verdict (already built, partly built, not built yet, or clashes with what's there), a couple of plain sentences, and the files that matter with a word on why. A giver's AI app sends it along with the idea; a receiver's saves it while reviewing. The card shows who it was looked up for, when, and which commit the code was at. File names are checked against the repo before they are kept, and they are shown only to people who may look at the code themselves. When a build starts, the section goes into the issue under the scope, marked as a head start to check, so the coding agent begins in the right place. A receiver can remove it until then.

## Ideas that belong together

The same idea often arrives twice in different words. Inrface compares the wording of the ideas in a project and points out the ones that read alike. It runs on the server, uses no AI, and sends nothing anywhere.

- **Before an idea is sent,** the giver's AI app checks the board (`find_similar`). If the idea is already there, it says so, with where that idea stands, and offers to add to it instead of filing a second card. Ideas that were built or turned down are found too.
- **After an idea is sent,** the card shows **Might be the same as**, and the app says so when a typed idea reads like one that is already there.
- **Receivers** see the likely pairs above the list and the board, and can merge a pair or mark it **Not the same**, which stops it being suggested. Their AI app can do the same by voice (`find_duplicates`, `merge_cards`, `keep_separate`).

Merging folds one idea into another. The merged idea leaves the list, its comments move across with a note saying what it was, and both people are told. Whoever sent the merged idea keeps getting alerts about the one it became. The surviving card's write-up is not touched, and the conversation someone had while shaping the merged idea stays with that idea, where only they and the receivers can read it. An idea that is already being built can't be folded into another; merge the other one into it. **Split back out** on the merged idea undoes a merge and returns the comments it brought.

The matching is by words, not meaning. It can miss an idea that is put very differently, and it can pair two ideas that only share vocabulary, so it only ever suggests. Only the newest 400 ideas in a project are compared.

## How a build gets picked up

`Start build` opens an issue in the project's repo with the card's scope and the project's `build_label` (default `ready-to-build`). Set up your coding agent in that repo to start on that label and open a pull request. Every few minutes Inrface reads the issue back: a linked pull request shows on the card, and a merged pull request or completed issue marks the card Done / Live.

The issue is laid out in the order a coding agent needs it:

1. The scope a receiver wrote: summary, who it's for, the problem, what's in and out, and the done-when checklist.
2. **Not decided yet**, when something was left open, with a line telling the builder not to settle it by guessing.
3. **What the code says**, if that was saved on the card.
4. **How to build this**: where to start, where to stop, how to show the work is done, and to open a pull request and leave merging to a person. Inrface writes this part itself, the same way every time. Nothing anyone typed on the card goes into it.
5. The discussion and the idea's original words as quoted background, marked as not part of the requirements.

Tell your coding agent to build from the scope.

### Ready to build?

Before a build, the idea shows what its write-up still lacks, above the **Start build** button:

- **To settle**: no summary, nothing that says what's in and out, nothing that says when it's done, things still undecided, a question nobody has answered, or a code check that says it is already built or clashes with what's there.
- **Worth a look**: nobody has checked it against the code, comments were added after the scope was last changed, or another card looks like the same idea.

An AI app gets the same list from the `ready_to_build` MCP tool and is told to say what's open before it asks for a build. It is a checklist of what is filled in, not a judgement of whether the idea is a good one, and it makes no AI call.

Nothing on the list stops a build. A receiver can start one with things open; the confirmation names them, and the issue tells the builder what wasn't decided.

### Builds asked for from an AI app

An AI app can ask for a build (the `start_build` MCP tool), but asking sends nothing. The idea shows **Waiting for your OK** in the app, the receiver who asked gets an alert that opens it, and the build starts when a receiver presses **Start build** there. **Not now** drops the request, and a request nobody answers is dropped after a day.

A token set in `.env` is used only for the repo named in `innerface.toml`. To point a project at another repo under **Settings → Connectors → GitHub and build flow**, save a token for that repo at the same time.

## After an idea ships

When an idea is marked Done / Live, by a merged pull request or by hand, whoever had it gets an alert that says **Your idea is live**, and the card shows the day it shipped. So does anyone whose idea was merged into it.

Set **Where people can try it** under **Settings → Sharing** and the alert and the card carry a **Try it** link. A receiver can set a different link on one idea, for a change that lives on its own page. Links have to start with `https://`.

**Ideas → Shipped** lists what went live, week by week, for everyone in the project. **Copy as text** gives the same list as plain text to paste into a message or an update.

By voice, "what's new?" in an AI app (the `whats_new` MCP tool) gives a catch-up: what is waiting on that person, ideas that came in, what is being built and what went live since they last asked. A giver hears about their own ideas. `whats_shipped` reads out the weekly list.

## Showing the roadmap to someone outside

A receiver can make a **roadmap link** under **Settings → Sharing**. It opens a read-only page for someone with no login, such as a client or an investor.

- The page shows the titles of ideas that are filed on the roadmap, being built, or shipped in the last 90 days. Tick the box when making the link to add each idea's one-line summary.
- It never shows who an idea came from, the conversation, the scope, what the code says, or links to the repo. Ideas that are not filed yet, or were turned down, are left out. An idea whose text had personal details removed shows its title only.
- A link works for 7, 30 or 90 days, or until you switch it off. The address is shown once when the link is made, and Inrface keeps only a hash of it. The list shows how often each link was opened.
- Anyone who has the address can open the page, so send it the way you would send the roadmap itself. An AI app can't make one.

If the install is behind Cloudflare Access, people outside can't reach the page until you open its path. Add a **self-hosted application** for your hostname with the path `share`, and give it one policy with the action **Bypass** and the selector **Everyone**. Everything the page loads sits under that path, and everything else keeps its sign-in.

## Sending builds somewhere else

**Start build** sends the scope to every place a project builds: its GitHub repo, and any connectors added under **Settings → Connectors → Chat and tracker connectors**. A project needs at least one of them.

| Connector | What it does | Guide |
|---|---|---|
| Webhook | Posts the scope, signed, to an address you run | [docs/webhook.md](docs/webhook.md) |
| Linear | Opens an issue in one Linear team | [docs/linear.md](docs/linear.md) |

These are one-way: only GitHub reports progress back to the card. How connectors work, how secrets are kept and how to add a new kind are in [docs/connectors.md](docs/connectors.md). The same pages are served in the app under `/docs/`.

Set `INNERFACE_SECRET_KEY` in `.env` to encrypt tokens and connector secrets saved from the Settings screen. Set `PUBLIC_URL` so issues, webhooks and Slack messages can link back to the card.

## The board's labels

The board's columns are the team's own labels: a sprint, a review step, a place to test. Each counts as one of a few core meanings (New, Needs info, Roadmap, Someday, Active development, Testing, In production, Rejected), which is what the app goes by. Set them under **Settings → Board**. See [docs/board-labels.md](docs/board-labels.md).

Every idea keeps a **History** of who did what and when, open to everyone who can see the idea. A reply to a question comes back as **Answered**, which counts as New.

## Following an idea to live

By default a card is done when its GitHub issue closes with the pull request merged. A team whose code travels through several branches can say how under **Settings → Connectors → GitHub and build flow**: name the stages, pick each one's branch from the list GitHub shows, and each card shows where its code has got to. The last stage is live. Setup is in [docs/build-flow.md](docs/build-flow.md).

## Starting builds automatically

A small script, `runner/inrface_runner.py`, can run on the machine that holds your code and pick up each build issue Inrface opens, handing it to your coding agent on its own branch. It's one file with nothing to install. You set when it checks, and switch it on, under **Settings → Connectors → Build runner**. What it runs and which tokens it uses stays in a file on that machine, never in Inrface. It never merges or deploys. Setup is in [docs/build-runner.md](docs/build-runner.md). A guide written for a coding agent to set it up on your behalf is [docs/build-runner-agent-setup.md](docs/build-runner-agent-setup.md).

## Ideas from Slack

A Slack connector turns messages in one channel into cards. So do the `/idea` command and a **Send to Inrface** message shortcut. Receivers file ideas from the thread with **Approve**, **Defer** and **Clarify**, and the thread and the card share one conversation. Slack never starts a build. Setup, including the one path to open if you use Cloudflare Access, is in [docs/slack.md](docs/slack.md).

## Ideas from other systems

A project can take feature ideas and tool ratings from other systems, such as your product's website, through the intake API. In the app, open **Settings → Connectors → Intake keys**, create a source, and give its key to the sending system. If the install is behind Cloudflare Access, also create an Access **service token** and add a **Service Auth** policy for it to the application. Ideas land in **New** with the source's name. Ratings show under **Ideas → Tools**.

The full reference is [docs/intake-api.md](docs/intake-api.md), also served in the app at `/docs/intake-api`. The intake takes ideas only: bugs and support requests belong in the sending system's own tools.

## Seeing how it's used

Receivers get a **Usage** view under **Ideas**. For each project it shows how many ideas came in and from where (the web app, an AI app, Slack or another system), how far they got, how long the first response and the build took, and where people did their work. It counts ideas and actions, not names. The numbers are worked out from the install's own database when you open the page. Inrface sends nothing back to its authors, here or anywhere else.

## What this edition has

Everything above is in this edition, with these limits: a project can have one roadmap link, one tracker and one chat channel at a time. Starting a build with a spoken phrase, and extra ways to sign in, are not part of it. Pressing **Start build** is never limited.

## Everyday commands

- Update: `git pull && docker compose pull && docker compose up -d`, or `git pull && docker compose up -d --build` if you build from source
- Check the setup: `docker compose run --rm app python -m innerface.check`
- Back up: `scripts/backup.sh`, then copy `backups/` somewhere off the machine. Backups hold private text and the alert key.
- Logs: `docker compose logs -f app`

The data lives in the Docker volume `innerface-data`, not in this folder. To move machines: back up, set up the new machine, start it once, then copy the files in with `docker compose cp` and restart.

## Develop

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest==9.2.0
.venv/bin/python -m pytest -q
AUTH_MODE=dev AI_PROVIDER=none INNERFACE_CONFIG=innerface.example.toml DATA_DIR=./data .venv/bin/uvicorn innerface.main:app --port 8090
```

Pull requests run the tests in GitHub Actions (`.github/workflows/ci.yml`). A passing pull request from a branch of this repo is merged automatically; add the `hold` label, or open it as a draft, to stop that. The merge is a fast-forward, so the commits land on `main` as written: keep them tidy, and rebase onto `main` first if it has moved. A pull request from a fork is tested but never merged automatically.

`AUTH_MODE=dev` trusts anyone who can reach the port and takes the user from the `X-Dev-User` header (an email from the config). It refuses to start if Cloudflare Access settings are present, and `docker-compose.yml` always uses `access`. `tests/e2e.js` drives the whole loop in a browser; its header lists the server command.

The in-app assistant is optional (`AI_PROVIDER`). Without it, ideas typed into the web app save as written, and people do their AI work in their own app over MCP.

## Contributing

Ideas and bug reports are welcome as issues. Code from outside isn't being taken yet; [CONTRIBUTING.md](CONTRIBUTING.md) says why.

## License

Copyright (C) 2026 the Inrface authors

Inrface is free software under the [GNU Affero General Public License, version 3](LICENSE) only. You can use it, change it and host it yourself. If you let other people use a changed version over a network, you must offer them the source of your version under the same license.

It comes with no warranty; the license has the details.
