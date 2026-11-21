"""Is this idea ready to be built?

A plain look at the card, with no AI call, so the answer is the same every time and costs nothing. It names
what whoever builds this would have to guess at. It never stops a build: a receiver decides, and can go
ahead with things still open.
"""

# "missing" is something the builder would have to guess. "check" is something worth a look that may be fine.
MISSING, CHECK = "missing", "check"


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def look(card: dict, targets: list[str], said_since: int = 0) -> dict:
    """What is still open on this card before a build. `card` is the card as read_card returns it, `targets`
    the names of the places a build would go, `said_since` how many replies came after the scope last changed."""
    gaps: list[dict] = []

    def add(key: str, weight: str, what: str, then: str, items: list[str] | None = None) -> None:
        gaps.append({"key": key, "weight": weight, "what": what, "next": then, "items": items or []})

    if not targets:
        add("nowhere", MISSING, f"{card['project_name']} has nowhere to send builds.", "Link a GitHub repo or add a connector under Settings → Connectors.")
    if not card["summary"]:
        add("summary", MISSING, "There's no summary.", "Say in two or three sentences what's wanted.")
    if not card["scope"]:
        add("scope", MISSING, "Nothing says what's in and what's left out.", "Write down the smallest version worth building, and what waits.")
    if not card["acceptance"]:
        add("done_when", MISSING, "Nothing says how to tell when it's done.", "Add checks someone could tick off, one per line.")
    if card["open_questions"]:
        count = len(card["open_questions"])
        add("undecided", MISSING, _n(count, "thing is", "things are") + " still undecided.",
            "Settle them, or leave them: whoever builds it is told not to guess.", list(card["open_questions"]))
    if card["status"] == "Needs info":
        add("waiting", MISSING, f"{card['author_name']} hasn't answered the last question yet.", "Wait for the answer, or scope it without one.")
    check = card.get("code_check")
    if check and check.get("says") == "built":
        add("built", MISSING, "The code check says this is already built.", "Find out what's missing from what's there before sending it.")
    elif check and check.get("says") == "clashes":
        add("clashes", MISSING, "The code check says this clashes with how something works today.", "Decide how to handle the clash and put it in the scope.")
    elif not check and card.get("code_open"):
        add("code", CHECK, "Nobody has checked this against the code.", "An AI app can look through the code and keep what it finds on the idea.")
    if said_since:
        add("replies", CHECK, _n(said_since, "comment was", "comments were") + " added after the scope was last changed.", "Read them and carry over anything that changes the scope.")
    if card.get("similar"):
        add("look_alike", CHECK, "It looks like the same idea as " + _n(len(card["similar"]), "other card", "other cards") + ".",
            "Merge them, or mark them as not the same.", [f"{m['title']} ({m['status']})" for m in card["similar"]])
    return {"ready": not any(g["weight"] == MISSING for g in gaps), "headline": _headline(gaps), "gaps": gaps}


def _headline(gaps: list[dict]) -> str:
    missing = sum(1 for g in gaps if g["weight"] == MISSING)
    checks = len(gaps) - missing
    if missing:
        return _n(missing, "thing", "things") + " to settle before building"
    if checks:
        return "Ready to build, with " + _n(checks, "thing", "things") + " worth a look"
    return "Ready to build"
