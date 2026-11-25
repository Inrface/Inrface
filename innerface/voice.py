"""Builds asked for from an AI app.

An AI app can mishear its user, and it can be talked into things by text it reads on a card or in a repo.
So when one asks for a build, nothing is sent. The request waits on the card, the person gets an alert, and
the build starts when a receiver presses Start build in the app, which no AI app can do.

Every request waits for that press.
"""
from . import db, ready, service
from .config import Settings

try:
    from . import phrase
except ImportError:   # not part of this edition
    phrase = None

ASK_SECONDS = 24 * 3600   # how long a request waits before it is dropped
ALERT_EVERY = 120         # seconds before asking again sends the alert again


def waiting(card: dict) -> bool:
    """Whether a request from an AI app is waiting on this card."""
    at = card.get("build_asked_at")
    return bool(at and not card.get("build_started_at") and at > db.ago(ASK_SECONDS))


def _has_alerts(s: Settings, email: str) -> bool:
    if not s.push_contact:
        return False
    with db.connect(s) as c:
        return bool(c.execute("SELECT 1 FROM push_subscriptions WHERE email=? LIMIT 1", (email,)).fetchone())


def ask(s: Settings, user: dict, card_id: str, said: str = "") -> dict:
    """An AI app asking for a build. Starts it only if the person said their build phrase; otherwise the
    request waits for a press in the app."""
    card, project, targets = service.buildable(s, user, card_id)
    if card["build_started_at"]:
        out = {"started": True, "already_started": True, "issue_url": card["issue_url"]}
        if card["unsent"]:
            out["not_yet_sent_to"] = card["unsent"]
            out["tell_the_user"] = "This build already started. It hasn't reached " + ", ".join(card["unsent"]) + ": they can send it there from the idea in the Inrface app."
        return out
    # What the write-up still lacks. It doesn't hold the build back; the person hears it and decides.
    found = card.get("ready") or {"ready": True, "gaps": []}
    still_open = [g["what"] for g in found["gaps"] if g["weight"] == ready.MISSING]
    heard = phrase.heard(s, user["email"], said) if phrase else "none"
    if heard == "right":
        out = service.start_build(s, user, card_id)
        if out.get("waiting_for_approval"):
            return {"started": False, **out}
        # Always, so a phrase used without the person meaning it doesn't go unnoticed.
        service.notify(s, [user["email"]], "Build started by voice", card["title"], card["id"])
        if still_open:
            out["went_with_still_open"] = still_open
        return {"started": True, **out}
    again = waiting(card) and card["build_asked_by"] == user["email"]
    alert = not again or card["build_asked_at"] < db.ago(ALERT_EVERY)
    if alert:
        with db.connect(s) as c:
            c.execute("UPDATE cards SET build_asked_at=?, build_asked_by=? WHERE id=? AND build_started_at IS NULL", (db.now(), user["email"], card["id"]))
            if not again:
                service.log_activity(c, card, project.id, user["email"], "build_asked", "")
        service.notify(s, [user["email"]], "Start this build?", card["title"], card["id"], "build")
    say = "Nothing has been sent yet. The build starts when they press Start build on this idea in the Inrface app. "
    say += "An alert that opens the idea is on its way to their phone." if _has_alerts(s, user["email"]) else "They don't have alerts switched on, so they need to open Inrface themselves."
    if heard == "wrong":
        say = "Those words aren't the build phrase saved in Inrface. " + say
    elif heard == "locked":
        say = "The build phrase is switched off for an hour after too many wrong tries. " + say
    out = {"started": False, "waiting_for_approval": True, "would_go_to": [t["name"] for t in targets], "tell_the_user": say}
    if still_open:
        out["still_open"] = still_open
        out["tell_the_user"] += " Say what is still open on it too: " + " ".join(still_open)
    return out


def decline(s: Settings, user: dict, card_id: str) -> None:
    """A receiver saying no to a request from an AI app. The idea stays as it was."""
    with db.connect(s) as c:
        card = service.load_card(c, s, user, card_id, receiver=True)
        if not waiting(card):
            return
        c.execute("UPDATE cards SET build_asked_at=NULL, build_asked_by='' WHERE id=?", (card["id"],))
        service.log_activity(c, card, card["project_id"], user["email"], "build_declined", "")
