# Run the build runner

The build runner picks up each build issue Inrface opens and hands it to your coding agent, so a build starts without anyone opening a terminal. It runs on the machine that holds your code, as your normal user. It is free.

[TOC]

## Set it up

You need Python 3.11 or later, git, and your coding agent (Claude Code by default, logged in).

**The quickest way:** in Inrface, open **Settings → Connectors → Build runner** and press **Copy command**. Paste it in a terminal on the machine that holds your code. It downloads the runner, asks for the token once, uses your checkout (or clones the repo), and starts the runner in the background. The steps below are the same thing by hand.

1. **One GitHub token.** In GitHub, make a fine-grained token for the repo, with **Contents**, **Issues** and **Pull requests** all set to **Read and write**. Paste it into Inrface (**Settings → Connectors → GitHub**). It's the same token for the app and the runner.
2. **In your checkout of the repo, run setup:**

   ```sh
   python3 runner/inrface_runner.py setup
   ```

   Paste the same token when it asks (nothing shows as you paste). Setup reads the repo, the main branch and the test command from the checkout, checks the token with GitHub, and saves everything in `~/.config/inrface-runner/`, in a file named after the repo (for `acme/shop`, `shop.toml` and `shop.token`). If the token is already in a file, use `setup --token-file <path>` instead.
3. **Keep it running:**

   ```sh
   python3 runner/inrface_runner.py install
   ```

   That starts it as a background service for your user, and again at boot. The service is named after the repo too: `inrface-runner-shop`.

That's all. Press **Start build** on an idea in Inrface and the runner picks it up within five minutes.

To try one check by hand instead of installing it, run `inrface_runner.py once`. To see what it would do and start nothing, run `inrface_runner.py dry-run`. To test the settings, run `inrface_runner.py check`.

### More than one project on the same machine

Run setup and install once per project, each with its repo: `setup --repo acme/shop`, then `install --repo acme/shop`, and the same for the next one. Each project gets its own runner, with its own settings, token, log and service, and they don't share anything. When more than one is set up, add `--repo` to `once`, `check` and `dry-run` too, so the runner knows which one you mean.

## What it does

Every few minutes, it:

1. Lists open issues in your repo with the build label (`ready-to-build`).
2. Keeps an issue only if its body opens the way Inrface writes it and it was opened by the token's owner, which is who Inrface opens it as.
3. Skips issues it has handled before, and issues that already have a pull request.
4. Comments "Build started", makes a fresh copy of the code on branch `inrface/issue-N`, and runs the agent there.
5. When the agent finishes, **the runner** pushes the branch and opens the pull request, with the description the agent wrote.

The agent only reads, edits, commits and runs your tests. It holds no credential, so it can't push anywhere, including your main branch. The runner pushes only to `inrface/issue-N` branches, and it never merges or deploys. A person still reviews every pull request.

## When a pull request conflicts

If another pull request merges first and a build's pull request can no longer merge, the runner rebases it onto the main branch itself. If the rebase is clean, it pushes, and nobody has to do anything. If there are real conflicts, it sends the agent in to resolve them, then pushes. It never opens a second pull request.

The idea in Inrface shows **Fixing conflicts** while that runs. The receivers get an alert when it's ready to test again, or when the agent couldn't resolve it and a person is needed. It tries each pull request up to twice (`max_conflict_fixes`; 0 turns this off).

## Settings

Setup writes `~/.config/inrface-runner/<repo name>.toml`. You don't have to change anything in it. These are the settings people do change:

| Setting | Default | What it does |
|---|---|---|
| `test_commands` | found from the repo | the commands the agent may run to test, such as `["npm test"]` |
| `allowed_authors` | the token's owner | who may open the issues that get built |
| `max_builds` | 1 | builds at the same time |
| `interval_minutes` | 5 | how often it checks |
| `max_runs_per_day` | 20 | a stop on cost |
| `timeout_minutes` | 60 | a build still running after this is stopped |
| `command` | Claude Code, with tools to read, edit, commit and test | a different agent command, as a list of arguments (never run through a shell) |

On or off, interval and builds at once can also be set from Inrface: see **Control it from Inrface** below.

## Make it try the work, not only test it

Passing tests doesn't show that a screen works. If your project has a script that starts the app and looks at it (a headless browser at phone and desktop sizes, with screenshots), add it as `verify_commands = ["/path/to/check.sh"]`. The agent is then allowed to run it and told to. Have it accept `--self-test`, so `check` can confirm it works before a build needs it.

In the pull request, the agent lists what it checked under **Verified** and anything it couldn't under **Not verified**. Read the second list when you review. If the agent was refused a tool, the runner says which on the issue.

## Control it from Inrface

Optional. In **Settings → Connectors → Build runner**, make a key and add `inrface_url` and `runner_key` to the config. Inrface then decides on or off, the interval and builds at once, and shows when the runner last checked in. If Cloudflare Access is in front of Inrface, the runner also needs an Access service token (`inrface_headers`) or a bypass for the path `/api/runner/v1/config`. Without any of this, the runner works from its own file.

To stop builds at once without stopping the service, create the file `PAUSE` in the runner's state folder, `~/.local/state/inrface-runner/<repo name>/`. Delete it to carry on.

## Keeping it safe

- **The issue text is written by people and AI assistants.** The runner hands it to the agent marked as requirements to consider, not commands. The agent's tools are the real limit, and by default they don't include pushing or merging.
- **Only the token's owner's issues are built.** An issue from anyone else is ignored, even with the label.
- **Cost.** `max_runs_per_day` stops a flood of issues from running up an agent bill. `timeout_minutes` stops one build running forever.
- **Without branch protection** (private repos on GitHub's free plan), nothing on GitHub stops a push to the main branch. The runner itself never pushes there, and the agent can't push at all.

### Stricter, if you want it

The agent runs as your user, so in principle it could edit the runner's config or script. For most people that's fine. If you run agents on issues you don't fully trust, run the runner as a separate user with its script and config owned by root, and hand the config over with systemd's `LoadCredential=`. This is optional, and nothing above needs it.

## When something goes wrong

- **Nothing starts.** Run `inrface_runner.py dry-run`. It says why: paused, off in Inrface, no matching issue, or the author isn't allowed. The log is `runner.log` in `~/.local/state/inrface-runner/<repo name>/`, or `journalctl --user -u inrface-runner-<repo name>`.
- **A build failed.** Its output is in `issue-N.log` in the same folder, and the issue got a comment. To try again, remove `"N"` from `state.json` in that folder.
- **The push or the pull request was refused.** The token needs **Contents** and **Pull requests** set to Read and write. You can change a fine-grained token's permissions in GitHub without making a new one.
- **"More than one runner is set up here."** Add `--repo owner/name` to say which one.
- **"Can't read the GitHub token file."** The file named in `github_token_file` doesn't exist. Run `setup` again.
- **"Tests: none found."** The runner couldn't tell how this project runs its tests. Add them to the config as `test_commands`, for example `test_commands = ["npm test"]`.
- **The pull request says a screen was never tried.** Add your check script as `verify_commands` (above).
- **"This key is for another repo".** The key's project in Inrface and `repo` in the file differ. The runner refuses rather than guess.
