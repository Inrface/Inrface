# Build flow

A card follows its GitHub issue. Without any setup it shows the issue, the pull request when one appears, and Done / Live once the issue closes with the pull request merged. That fits a team that merges straight to one branch and ships from it.

Most teams have more steps between a merge and live. The **Build flow** setting lets a receiver say what they are, so a card shows where its code has got to.

[TOC]

## Set it up

Open **Settings**, then **Connectors**, then **GitHub and build flow**. The repo and token have to be saved first.

1. Press **Add a stage** for each step, in the order code travels. Name it as your team does: Dev, Staging, Live.
2. Pick the branch that marks the stage. The list is the repo's branches as GitHub shows them to the token. Your branch can be called anything: `dev_2`, `master`, `final`.
3. Put the stage that means live last. When a card gets there it becomes Done / Live, and the person who had the idea is told.
4. Press **Save**.

If the list is empty and a message says Inrface can't see the branches, the token isn't set up right. It needs read access to the repo's **Contents**. Fix the token under GitHub above, then come back. A flow can't be saved until the branches can be seen.

## How a card moves

Every few minutes Inrface looks at each card that has a pull request. Once the pull request has merged, it checks which stages' branches hold the commit the merge made. The furthest one is the card's stage. It shows on the card as "Now in Staging", and in the activity list. The idea's author and the receivers get an alert each time it moves forward.

A card never moves back. If you change the stages, a card catches up the next time it is checked.

## Example setups

Pick the one nearest to how your team works, and adjust. A stage is only a name and a branch, so any of these can be set up in a minute. The branch names are examples: use whatever yours are called, such as `master`, `dev_2` or `final`.

### Straight to main

Pull requests merge into `main`, and a merge to `main` ships. Many small teams work this way.

| Stage | Branch |
|---|---|
| Live | `main` |

One stage is enough. A card becomes Done / Live when its pull request is merged. Testing happens before the merge, in the pull request or on a preview, so there is nothing after it to follow. This is also how a team with no flow set up behaves, except the card waits for the branch and not for the issue to close.

### A dev branch in front of main

Pull requests merge into `dev`. Later, `dev` is merged into `main` to ship.

| Stage | Branch |
|---|---|
| Testing | `dev` |
| Live | `main` |

A card shows "Now in Testing" once its pull request is on `dev`, and becomes Done / Live when `dev` is merged into `main`. Promote by merging `dev` into `main` or fast-forwarding it. Don't squash `dev` into `main`: the original commit then never reaches `main` and the card stays at Testing.

### Dev, staging and main

Three environments, each with its own branch.

| Stage | Branch |
|---|---|
| Dev | `dev` |
| Staging | `staging` |
| Live | `main` |

Cards move forward as each branch gets the code. A flow can have up to six stages.

### Testing before the pull request

Some teams test on a throwaway branch first, such as a `sandbox` that several branches are merged into on a dev server, and only open the pull request once it works. The testing branch isn't a step the code travels through afterwards, so don't add it as a stage.

| Stage | Branch |
|---|---|
| Live | `main` |

The testing that happens before the pull request is already covered. A card is **In dev for testing** from the moment Start build is pressed. If you want that shown as its own column, add an **In Testing** label under [Board labels](/docs/board-labels).

### Release branches

If you cut a release branch for each version, such as `release/1.4`, the branch name changes every time, so it can't be a stage. Use the branch that always exists: stage it as `main`, or as one long-lived `release` branch if you keep one.

### Sprints

A sprint is a way of grouping ideas, not a branch the code travels through. Use [Board labels](/docs/board-labels) for it: add **Next sprint** and **This sprint** as labels. The build flow can stay as simple as one stage.

## Not covered yet

- **Deploys by tag or release.** If you ship by tagging a release rather than merging to a branch, a card won't move on the tag. Use the branch the tag is cut from.
- **Deployment environments.** GitHub's deployment status isn't read. Only branches are.
- **Squashed promotions,** as described below.

If your setup isn't here and the stages don't fit, open an issue at the project's GitHub page with how your branches work, and the example will be added.

## What to know

- **A squash that skips the merge isn't seen.** The check asks whether the branch holds the commit the pull request merged as. If your team promotes by merging the dev branch into main, or by fast-forwarding, that works. If a person re-creates the change on main as a new squashed commit, the original commit never reaches main and the card stays at the earlier stage.
- **A deleted or renamed branch** pauses the check for that project, and the log says why. Pick the new name under **Settings → Connectors → GitHub and build flow**.
- **Only a person changes the flow.** It is not available to AI apps.
- **With no stages,** nothing changes from before: Done / Live when the issue closes with its pull request merged.
- It only reads from GitHub. It never merges, promotes or deploys anything.
