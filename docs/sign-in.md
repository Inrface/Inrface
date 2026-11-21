# Sign in without Cloudflare Access

Inrface can sign people in itself. People sign in with Google, the app keeps a session for each browser, and AI apps connect to the app directly. Nothing has to stand in front of it.

This way of signing in is new. The project has gone over it once for weak spots and fixed what it found, and nobody outside the project has reviewed it. Signing in through Cloudflare Access, as [the install guide](/docs/install) sets up, puts Cloudflare's own sign-in in front of the app and is still the safer choice. Use this where Cloudflare Access isn't an option.

[TOC]

## What stays the same

Who may come in is still decided by the projects. Someone who signs in with an address that is in no project is turned away. A receiver adds people under **Settings → People**, and nothing else has to be set up for them.

The app never sees a password. Google says who signed in, and the app trusts only an address Google has confirmed.

## What you need

- An `https://` address that reaches the app. The Cloudflare tunnel from the install guide gives you one without an Access application. Any reverse proxy with a certificate works too.
- A Google account to create the sign-in client with.

If the address already has a Cloudflare Access application in front of it, remove that application or use another address. Otherwise people sign in twice, and AI apps talk to Cloudflare's sign-in and never reach this one.

## 1. Make a Google sign-in client

In the Google Cloud console, create an **OAuth client ID** of type **Web application**.

1. Under **Authorized redirect URIs**, add `https://<your address>/signin/google/back`. It has to match exactly.
2. Copy the client ID and the client secret.

Inrface asks Google for the email address and nothing else. While the Google app is in testing, only the test users you list in the console can sign in.

## 2. Fill in `.env`

```
PUBLIC_URL=https://ideas.example.com
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
```

`PUBLIC_URL` is required here, because the app has to tell Google and AI apps its own address. `CF_ACCESS_TEAM` and `CF_ACCESS_AUD` are not used.

Two more settings are optional:

| Setting | What it does | If left out |
|---|---|---|
| `SESSION_DAYS` | How long a browser stays signed in | 30 |
| `CONNECTOR_REDIRECTS` | The addresses AI apps may be sent back to, separated by commas | Claude's and ChatGPT's |

## 3. Check and start

This way of signing in has its own compose file:

```
docker compose -f docker-compose.builtin.yml run --rm app python -m innerface.check
docker compose -f docker-compose.builtin.yml up -d
```

It is a separate file on purpose. Nothing in `.env` can switch an install that uses `docker-compose.yml` away from Cloudflare Access.

Both files use the same data, so an install can move from one to the other. Stop the one that is running first, with `docker compose down`. Sessions and AI app connections made here stop working behind Cloudflare Access, and the other way round.

## How people get in

Someone opens the address and sees a sign-in page with **Continue with Google**. After Google, they land in the app. The browser stays signed in for `SESSION_DAYS`.

Each person has an **Account** screen, reached from the top of the app. It shows:

- every browser they are signed in on, with a button to sign each one out
- every AI app that acts for them, with a button to disconnect each one
- the address to give an AI app

Signing a browser out also stops the alerts it was getting. They start again the next time the app is opened there, signed in. The same goes for an install that moves over from Cloudflare Access: alerts start again once each person opens the app. Signing in again on a browser closes the sign-in it had before.

Taking someone out of their last project under **Settings → People** signs them out everywhere, at once: every browser, every AI app and every alert. If they are added back later, they sign in again. Someone taken out by editing `innerface.toml` instead is signed out when the app next starts, or the next time they come to it, whichever is first.

## AI apps

Each person adds a custom connector in their own AI app with the address `https://<your address>/mcp`. They leave the client ID and secret empty: the app registers itself.

The AI app then sends the person to Inrface. They sign in if they aren't already, see which app is asking, and press **Allow**. Only then does the app get a token.

- The token works on `/mcp` only. It doesn't open the web app, so an AI app can't change people or settings, make share links or press Start build.
- It lasts an hour. The AI app renews it by itself for up to 30 days of not being used.
- Each renewal swaps the renewal token for a new one. If an old one is used a second time, the connection ends and the person connects again. An AI app that retries a renewal after a dropped connection can trip this.
- The code an AI app gets after **Allow** works once. If it turns up a second time, the connection it made ends.
- An app's registration is kept while anyone is connected through it, and for 90 days after it was last used. One that nobody went on to use is cleared after a day.

An AI app can only be sent back to an address on the `CONNECTOR_REDIRECTS` list. To let another app connect, set the list to the addresses you want, the built-in ones included:

```
CONNECTOR_REDIRECTS=https://claude.ai/api/mcp/auth_callback,https://claude.com/api/mcp/auth_callback,https://another.example/oauth/callback
```

An entry may end in `/*` to allow plain paths under it: letters, digits and `-`, `.`, `_`, `~`, with `/` between the parts. An address with a `?` or anything else in it is refused.

This has been tested against the app's own tests only. It hasn't been tried with Claude or ChatGPT yet.

## What it does and doesn't protect

- What signs a browser in or lets an AI app in is kept only as a hash: session cookies, AI app tokens, renewal tokens and the one-use codes. A copy of the database signs nobody in.
- A sign-in or a connection that is under way is kept as it is. It stops working after ten minutes and is cleared the next time someone signs in, an app registers or the app starts. The secret of an AI app that registers with one is kept as it is too. Claude and ChatGPT register without.
- The session cookie can't be read by scripts, is sent over https only, and isn't accepted on `/mcp`.
- A request that changes something must come from the app's own pages.
- A sign-in has to finish in the browser that started it, within ten minutes, and counts once.
- A request to one of the AI app addresses can be 64 KB at most, however it is sent.

It does not do these things:

- **A second sign-in step.** That is Google's to ask for. Turn on 2-Step Verification on the Google accounts.
- **Slowing down repeated attempts.** There are caps on how many sign-ins and registrations can be waiting, and nothing more. Put the app behind a tunnel or proxy that limits requests.
- **Guarding the intake API.** With nothing in front of the app, each source's key is the only proof. Behind Cloudflare Access there are two.
- **A list of past sign-ins.** The server log has them: `docker compose -f docker-compose.builtin.yml logs app | grep "signed in"`.
- **Telling people apart by more than their address.** A person is whoever Google says holds that email address when they sign in. If an address passes to someone else, take it out under **Settings → People**.
- **Checking who an AI app is.** The name on the **Allow** page is what the app calls itself. What can be relied on is where the person is sent back to, which the page shows and which has to be on the list. Only press **Allow** for a connection you just started yourself.
- **Keeping registrations open under a flood.** Up to 500 apps can be registered. If someone fills that, no new app can register until the unused ones are cleared a day later. Apps already connected keep working.

A person can be signed in on 20 browsers and connect 20 AI apps. The oldest is dropped after that.

## When something goes wrong

Run the check from step 3 first. The log says why a sign-in didn't finish:

```
docker compose -f docker-compose.builtin.yml logs app | grep -E "login refused|sign-in did not finish"
```

| What you see | What it means |
|---|---|
| The app won't start and the log says `PUBLIC_URL is required` | Set `PUBLIC_URL` to the `https://` address people open. |
| The sign-in page says sign-in isn't set up yet | `GOOGLE_CLIENT_ID` or `GOOGLE_CLIENT_SECRET` is empty. |
| Google shows `redirect_uri_mismatch` | The redirect URI in the Google client isn't exactly `PUBLIC_URL` followed by `/signin/google/back`. |
| "That account isn't in any project here" | The person signed in, but with an address no project lists. The log shows the address. A receiver adds it under **Settings → People**. |
| "Sign-in didn't finish" | The log says why. Often the link was opened in a different browser from the one that started, or cookies are blocked. |
| An AI app fails with `invalid_redirect_uri` | That app's return address isn't on the `CONNECTOR_REDIRECTS` list. |
| An AI app fails with `invalid_target` | The address in the connector isn't `PUBLIC_URL` followed by `/mcp`. |
| An AI app asks to connect again | The person was taken out of their last project, disconnected it on the Account screen, left it unused for 30 days, or a renewal token or a one-use code was used twice. The log has a line for the last two: look for `was ended`. |
