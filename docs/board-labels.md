# Board labels

The board's columns are labels, and a team picks its own. A team that works in sprints can add **Next sprint** and **This sprint**. A team with a review step can have **Under Review**. A team that tests before release can have **In Testing**, or its own name for it, such as **QA**. They are only names, in the order the team wants them.

[TOC]

## Change them

Open **Settings**, then **Board** for the project. Add, rename, reorder or remove labels, and press **Save**. **Put back the usual ones** returns to the defaults: New, Under Review, Needs info, Far down the line, 6-month roadmap, Immediate roadmap, Being built, In Testing, Done / Live and Rejected.

Dragging an idea to a column gives it that label. The idea's own card shows the label too.

## What a label counts as

Each label counts as one of a few core meanings. That is what the app goes by, so changing a name never changes what the app does:

| Counts as | What it means to the app |
|---|---|
| New | Just came in, or is being looked at. Ideas can't be moved back to it by dragging. |
| Needs info | Waiting on an answer from the person with the idea. |
| Roadmap | Planned. Make as many labels as you like for it: Now, Next sprint, 6-month roadmap. The roadmap link treats them as one group. |
| Someday | Possible, but a long way off the current roadmap. Not rejected, and not planned. |
| Active development | Being built. Starting a build puts an idea here. |
| Testing | Built, and being tested. An idea gets here by a label change once its build has started. |
| In production | Finished. The person with the idea is told, and it shows under Shipped. |
| Rejected | Declined. |

Two labels can count as the same thing. **Next sprint** and **This sprint** can both count as Roadmap.

A board has to have at least one label that counts as New, Needs info, Active development, In production and Rejected, so every idea always has somewhere to sit. Roadmap, Someday and Testing are optional. If you remove a label that has ideas in it, they move to the first label that counts as the same thing.

Ideas can't be put under Active development or Testing by hand until a build has started. Press **Start build** first.

## What to know

- When the person who had an idea answers a question, it comes back as **Answered**. That counts as New, so it shows up in the same place, marked so you can tell it has an answer.
- Every idea keeps a **History** of who did what and when: moves, scope changes, builds, and where its code has got to. Everyone who can see the idea can see it.
- When an idea changes status any other way, such as Start build or its pull request merging, it takes the first label that counts as its new status.
- Only receivers change labels, and only in the web app. An AI app can't.
- The labels belong to one project. Projects don't share them.
