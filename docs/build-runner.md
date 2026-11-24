# Run the build runner

The build runner is a small script that picks up each build issue Inrface opens and hands it to your coding agent, so a build starts without anyone opening a terminal. It runs on the machine that holds your code. It is free.

[TOC]

## What it does

Every few minutes (and at once, if you add the webhook below) it:

1. Lists open issues in your repo with the build label.
2. Keeps an issue only if **all three** are true: it has the label, its body opens the way Inrface writes it (`# Title`, then `Idea from …, scoped in Inrface.`, with a `## How to build this` section), and it was opened by a login you allow.
3. Skips issues it has handled before, and issues that already have a pull request.
4. Comments "Build started" on the issue, then runs your agent command in a fresh copy of your code on branch `inrface/issue-N`.
5. Writes a log, and one output file per build.

It never merges, never deploys and never pushes to your main branch itself. A person still looks at every pull request. Inrface never starts a build either until a person presses **Start build** (or the last sign-off signs, after that press).

## Where each setting lives

| Set in Inrface (Settings → Connectors → Build runner) | Set only in the file on the machine |
|---|---|
| On or off | The agent command and the tools it may use |
| How often to check | The repo folder |
| Builds at the same time (1 to 5) | The GitHub token and who may open issues |
| (The repo and build label come from the GitHub settings above it) | Where logs and state are kept |

Nothing in the right column can be changed from Inrface, on purpose. If it could, anyone with an Inrface login could choose what runs on your machine.

## Set it up

1. **Make a GitHub token** for the runner: fine-grained, for this repo only, with **Issues** read and write and **Pull requests** read. Save it in a file only you can read, for example `~/.config/inrface-runner.github` with `chmod 600`. This is separate from the credentials your agent uses to push and open pull requests.
2. **Copy `runner/inrface_runner.py` and `runner/runner.example.toml`** to the machine. It needs Python 3.11 or later and nothing else installed. Save the example as `~/.config/inrface-runner.toml`, fill it in and run `chmod 600` on it.
3. **Set `allowed_authors`** to the GitHub login Inrface's token belongs to. Ask your team, or look at who opened the first build issue.
4. **Test it:** `python3 inrface_runner.py --check` shows the account the token belongs to and what the file says. `--dry-run` says what would start and starts nothing.
5. **In Inrface**, open **Settings → Connectors → Build runner**, make a key and paste it as `runner_key`, with `inrface_url`. Switch it on and save. The panel shows when the runner last checked in. (If Cloudflare Access is in front of Inrface, the runner needs a service token, `inrface_headers` in the example, and a policy that lets it in.)
6. **Run it all the time.** A systemd user service:

```ini
# ~/.config/systemd/user/inrface-runner.service
[Unit]
Description=Inrface build runner

[Service]
ExecStart=/usr/bin/python3 %h/inrface/inrface_runner.py --config %h/.config/inrface-runner.toml
Restart=on-failure

[Install]
WantedBy=default.target
```

Then `systemctl --user enable --now inrface-runner`. To keep it running when you're logged out: `loginctl enable-linger $USER`.

To stop it at once without stopping the service, create the file `~/.local/state/inrface-runner/PAUSE`. Delete it to carry on.

## Make it try the work, not only test it

Passing unit tests doesn't show that a screen works. By default the agent can only do what its tool list allows, and in `claude -p` mode anything else is refused without a word. So a build can pass its tests and never have been looked at. Three things fix that:

1. **Give the project a check for what a person sees.** Write a script that starts the app on its own (demo data, a temporary folder, no network), drives a page or flow in a headless browser at phone and desktop widths, saves screenshots, and reports console errors and failed requests. Keep it **outside the repository and owned by someone other than the agent**, so the agent can run it but not change it. Have it accept `--self-test`, which only checks that its parts are there (the browser, the folders) and exits 0.
2. **Tell the runner about it** with `verify_commands`, and allow it in the agent's tool list as an exact entry, not a general shell:

   ```toml
   verify_commands = ["/opt/verify/ui.sh"]
   command = ["claude", "-p", "{prompt}", "--output-format", "stream-json", "--verbose",
              "--allowedTools", "Read,Edit,Write,Glob,Grep,Bash(git add:*),Bash(git commit:*),Bash(git push origin inrface/*),Bash(gh pr create:*),Bash(/opt/verify/ui.sh:*)"]
   ```

   Each entry is a program path, or a list of arguments. It is never run through a shell.
3. **Run `--check`.** It runs every verify command with `--self-test`, so a missing browser shows up before a build instead of after, and it warns if a verify command isn't in the agent's allowed tools.

What the runner does with it:

- **The prompt** names those commands and says: done means verified. Run the tests, then the project's check for anything a person sees or uses. In the pull request, put what was checked, with the output or screenshots, under **Verified**, and anything not checked or refused under **Not verified**. Don't call the work complete while anything is under **Not verified**. With no `verify_commands`, the prompt tells the agent to say plainly that nothing could try the work.
- **Refused tools are shown.** With `--output-format json` or `stream-json`, Claude Code lists the tool calls it refused. The runner reads that list and comments on the issue with the tool **names** (never what they were asked to do). The same flag also keeps the whole stream in `issue-N.log`, so you can read what happened afterwards.

Two cautions about the script:

- **It runs the checkout's own code** (it has to start the app), as the user the agent runs as. That is no more than the test command already does, but it means that user should have no more power than it needs, and the script itself should be owned by someone else so the agent can't edit it.
- **Isolating the network** (a temporary user and network namespace, for example `unshare -rn`) needs unprivileged user namespaces, which some hosts switch off. If your script relies on it, have `--self-test` check for it, so it fails at `--check` rather than during a build. If your agent's `Read` tool is limited to its working copy, add the folder the screenshots go to.

The runner can make a skipped check visible. It can't make the agent do it, so read the **Not verified** list when you review the pull request.

## Start a build the moment Inrface asks

Add a **Webhook** connector in Inrface pointing at the runner's address and put `listen` and `webhook_secret` in the file. The runner checks the signature, and then does a normal check right away. It never reads the webhook's contents as instructions. Polling stays as the fallback. Inrface only posts to `https://` addresses and not to private ones, so the address has to be reachable that way, for example through a tunnel, unless you've set `WEBHOOK_ALLOW_PRIVATE`.

## Keeping it safe

- **The issue text is written by people and AI assistants.** The runner passes it to the agent marked as requirements to consider, not commands, and your agent's tool list is the real limit. Give it only what it needs: read and edit files, run your tests, commit, push its own branch, open a pull request. Leave out merging and pushing to your main branch.
- **If your plan has no branch protection.** GitHub only offers branch protection and rulesets on private repos to paid plans. Without them, nothing on GitHub stops an agent pushing to your main branch, so add these layers yourself:
  - leave merging and pushing to the main branch out of the agent's tool list;
  - add a local `pre-push` hook that refuses the main branch (`git config core.hooksPath` to a folder with a hook that exits 1 when the pushed ref is `refs/heads/main`);
  - give the agent a credential that can't push to the main branch, if you can, such as a deploy key for a fork.
  With branch protection on, require a pull request review before merging as well.
- **Cost.** `max_runs_per_day` stops a flood of issues from running up an agent bill. `timeout_minutes` stops one build running forever.
- **Allowed authors is the real gate.** An issue from anyone else is ignored, even with the label and the opening line. Keep the list short.
- **A key that leaks** can only read the on/off, interval, builds at once, repo name and label. Make a new key in Inrface and the old one stops working at once.

## When something goes wrong

- **The pull request says a screen was never tried.** The agent was probably refused the tool that starts the app or the browser. Look for the runner's "refused" comment on the issue, add the check script to `verify_commands` and to the allowed tools, and run `--check`.
- **Nothing starts.** Run `--dry-run`. It says why: paused, off in Inrface, no matching issue, or the author isn't allowed. Look at `~/.local/state/inrface-runner/runner.log`.
- **A build failed.** Its output is in `issue-N.log` in the same folder, and the issue gets a comment. To try again, remove `"N"` from `state.json` while the runner is stopped.
- **Inrface can't be reached.** The runner keeps the last settings it was given. If it has never reached Inrface, it does nothing.
- **"This key is for another repo".** The key's project in Inrface and `repo` in the file differ. The runner refuses rather than guess.
