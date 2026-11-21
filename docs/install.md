# Install Inrface on your own server

This takes about half an hour the first time. Most of it is spent in Cloudflare's dashboard.

You need:

- A machine that stays on, with Docker and git. A small VM or mini PC is enough.
- A domain on Cloudflare. The free plan works, and so does the free Zero Trust plan for up to 50 people.
- A Google account for each person, or another login method that Cloudflare Access supports.

To run without Cloudflare Access, see [Sign in without Cloudflare Access](/docs/sign-in). That way is newer, and nobody outside the project has reviewed it.

Inrface opens no ports on the machine. People reach it through a Cloudflare Tunnel, and Cloudflare Access signs them in before a request gets that far. The app then checks Cloudflare's signed token on every request, so a mistake in the Cloudflare setup locks people out and does not let strangers in.

## Try it first

To look around before setting anything up, run it on your own computer with made-up people and no sign-in:

```
git clone https://github.com/Inrface/Inrface.git inrface
cd inrface
docker compose -f docker-compose.try.yml up --build
```

Open `http://localhost:8080`. You are Alex, who receives ideas. It starts empty. To fill it with sample ideas, run this in a second terminal and reload the page:

```
docker compose -f docker-compose.try.yml exec app python -m innerface.demo
```

The sample ideas only go into an empty try-it install. The command refuses to run where sign-in is on. To see what a giver sees, stop it and start it again as Sam:

```
DEV_USER=sam@example.com docker compose -f docker-compose.try.yml up
```

This mode trusts anyone who can reach it, so it only listens on your own machine. Start build needs a real GitHub repo or a connector, so that one button reports an error here. `docker compose -f docker-compose.try.yml down -v` removes it and its data.

## 1. Get the files

On the server:

```
git clone https://github.com/Inrface/Inrface.git inrface
cd inrface
cp innerface.example.toml innerface.toml
cp .env.example .env
chmod 600 .env
```

`innerface.toml` holds people and projects. `.env` holds secrets. Neither is ever committed.

## 2. Make the tunnel

In the Cloudflare dashboard, open **Zero Trust > Networks > Tunnels** and create a tunnel of type **Cloudflared**.

1. Copy the tunnel's token (the long string after `--token` in the command Cloudflare shows) into `.env` as `TUNNEL_TOKEN`. You don't run Cloudflare's command: Inrface starts the tunnel for you.
2. Add a public hostname to the tunnel (the dashboard may call it a published application). Pick the address people will use, such as `ideas.example.com`, and point it at `http://app:8080`.
3. Put the address in `.env` as `PUBLIC_URL`, with `https://` in front and no slash at the end.

## 3. Put sign-in in front of it

Open **Zero Trust > Access > Applications** and add a **self-hosted** application.

1. Give it the exact address from step 2.
2. Add a login method, such as Google.
3. Add a policy that allows only the email addresses of the people who will use it.
4. Set the session length to the longest you're comfortable with. The phone app has to sign in again each time it runs out.
5. Save, then copy the **Application Audience (AUD) Tag** into `.env` as `CF_ACCESS_AUD`.

Put your team name in `.env` as `CF_ACCESS_TEAM`. It is the first part of your team domain: for `myteam.cloudflareaccess.com` it is `myteam`. **Zero Trust > Settings** shows it.

If you already have an Access application that covers the whole domain with a wildcard, the new application has to name this address exactly, so that it is the one Cloudflare uses here.

## 4. Let AI apps sign in

On the same application, open **Advanced settings** and turn on **Managed OAuth**, with dynamic client registration allowed. This is what lets Claude, ChatGPT and other AI apps sign in as a person.

Cloudflare only sends people back to addresses on the **Allowed redirect URIs** list. Add the ones for the apps your people use:

| App | Addresses to allow |
|---|---|
| Claude | `https://claude.ai/api/mcp/auth_callback` and `https://claude.com/api/mcp/auth_callback` |
| ChatGPT | `https://chatgpt.com/connector_platform_oauth_redirect` and `https://chatgpt.com/connector/oauth/*` |

The Claude addresses are tested. The ChatGPT ones come from OpenAI's documentation and haven't been tried with Inrface yet.

Cloudflare suggests a short access token lifetime (5 to 15 minutes) and a longer grant session (1 to 2 weeks).

## 5. Name the people and projects

Edit `innerface.toml`. Each person needs the email address they sign in to Cloudflare with, written the same way. Each project lists who gives ideas and who receives them.

A project that builds through GitHub also names a repo. For each one, create a fine-grained GitHub token limited to that repo, with **Issues** read and write, **Pull requests** read and **Contents** read. Contents is what lets people ask their AI app about the code; leave it off if you don't want that. Put it in `.env` under the name the project gives in `github_token_env`. You can leave the repo out and add it later from the Settings screen.

The file only has to name the first receiver and one project. Once the app is running, a receiver adds the other people under **Settings → People**, and more projects under **Settings → New project**. Each person added there also needs their address in the Access policy from step 3.

## 6. Fill in the rest of `.env`

- `INNERFACE_SECRET_KEY` encrypts tokens saved from the Settings screen. Make one with `openssl rand -base64 48` and keep a copy somewhere safe.
- `PUSH_CONTACT` is an email address given to phone push services. Phone alerts stay off until it is set.
- The in-app assistant (`AI_PROVIDER`) is optional. Leave it at `none` to start.

## 7. Check the setup

```
docker compose pull
docker compose run --rm app python -m innerface.check
```

The first command fetches the ready-made image. To build from this folder instead, run `docker compose build`.

The check reads your settings the way the server will, asks Cloudflare and GitHub whether they agree, and lists what it found:

```
Inrface setup check

  OK    Settings file: 2 people, 1 project.
  OK    The data folder can be written to.
  OK    Cloudflare knows the team "myteam".
  OK    https://ideas.example.com asks people to sign in through the right Access application.
  OK    AI apps can sign in at https://ideas.example.com/mcp.
  OK    Main product: the token can read your-org/your-repo, its issues and its pull requests.

Nothing to fix.
```

A line marked `FIX` says what is wrong and what to do about it. A line marked `NOTE` is something you may have left out on purpose. The check changes nothing and prints no secrets, so its output is safe to paste when asking for help.

## 8. Start it

```
docker compose up -d
```

It starts again on its own after a reboot. Make sure the machine doesn't sleep.

Open the address. Cloudflare asks you to sign in, and then the app opens.

## 9. Phones

Open the address on the phone, then **Share > Add to Home Screen**. Open it from the home screen and tap **Turn on alerts** on the Ideas tab. Alerts need iOS 16.4 or later.

## 10. AI apps

Each person adds a custom connector in their own AI app with the address `https://<your address>/mcp`. Leave the client ID and secret empty: the app registers itself. It then sends the person to Cloudflare to sign in, once.

After that, a giver can talk an idea through with their AI app and have it sent in, and a receiver can review and scope by voice. A build asked for from an AI app waits for a press on **Start build** in the app. Either of them can ask "what's new?" to hear what came in, what is being built and what went live.

## When something goes wrong

Run the check from step 7 first. Then look at the log, which says why each login was refused:

```
docker compose logs app | grep "login refused"
```

| What you see | What it means |
|---|---|
| The browser shows a Cloudflare error page | The tunnel isn't connected. `docker compose logs tunnel` says why. A wrong `TUNNEL_TOKEN` is the usual cause. |
| The page loads but stays empty, or says you aren't signed in | The request reached the app without Cloudflare's token. No Access application covers this address. |
| "This account doesn't have access" | You signed in to Cloudflare, but that email address isn't in any project. A receiver adds it under **Settings → People**, written the way Cloudflare shows it. If nobody can get in, fix `innerface.toml` and run `docker compose restart app`. |
| The log says "the token is for a different Access application" | Another Access application, often a wildcard one, covers this address. Make the Inrface application name the exact address, or copy the AUD tag of the one in use. |
| An AI app fails with `invalid_target` | The address in the connector isn't covered by an Access application with Managed OAuth turned on. |
| An AI app fails with "redirect_uri is not allowed" | Add that app's address to the Allowed redirect URIs list in step 4. |
| An AI app can't connect and the log shows no `/mcp` line | The request never reached Inrface. The cause is in Cloudflare or in the AI app. |
| The app won't start and the log says `innerface.toml` is a folder | The file was missing the first time Docker started. Run `docker compose down`, remove the folder, copy the example file again and fill it in. |
| The app won't start and the log says the config can't be read | Run `chmod 644 innerface.toml`. The app runs as its own user inside the container. |
| `docker compose pull` says access is denied | The ready-made image can't be fetched from here. Build it instead: `docker compose up -d --build`. |

## Keeping it running

- **Update:** `git pull && docker compose pull && docker compose up -d`. If you build from source, use `git pull && docker compose up -d --build`.
- **Back up:** `scripts/backup.sh`, then copy `backups/` somewhere off the machine. Backups hold private text and the alert key.
- **Logs:** `docker compose logs -f app`

The data lives in the Docker volume `innerface-data`, not in this folder. To move to another machine: back up, set up the new machine, start it once, copy the files in with `docker compose cp`, and restart.
