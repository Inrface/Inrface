"""Spotting ideas that say the same thing.

This is word matching, not AI: it runs on the server in a few milliseconds, sends nothing anywhere, and gives the
same answer every time. It only ever suggests. A person, or the AI app they are talking to, decides whether two
ideas really are the same.

Two ideas are compared twice: on their headlines (title and summary), and on everything they say. Words that
turn up in most ideas count for little, and a short list of everyday pairs ("export" and "download",
"sign in" and "login") are treated as the same word.
"""
import math
import re
from collections import Counter

CLOSE = 0.42       # worth saying "this looks like the same idea"
RELATED = 0.24     # worth a mention, no more
MAX_TEXT = 6000    # a long voice dump is compared on its first part
MAX_DOCS = 400     # the newest ideas in a project; older ones are left out of the comparison

_WORD = re.compile(r"[^\W_]+", re.UNICODE)

_COMMON = frozenset("""
a about after again all also am an and any are as at be because been before being but by can could did do does
doing done down each even every few for from get gets getting go goes going got had has have having he her here
hers him his how i if in into is it its just let like lot make makes making many may maybe me might more most
much my need needs no not now of on once one only or other our out over own please really same she should so
some such than that the their them then there these they thing things think this those through to too under up
us use used using very want wants was we were what when where which while who why will with would you your
able idea ideas feature features something someone way ways add adding added new able nice good great better
bit little thought thinking wondering wonder would've could've user users people person app site page pages
""".split())

# Said one way or another, these mean the same thing to someone bringing an idea.
_PHRASES = [(re.compile(p, re.I), w) for p, w in (
    (r"\b(?:sign|log)[\s-]?(?:in|on)\b", "login"),
    (r"\bsign[\s-]?up\b", "signup"),
    (r"\b(?:two|2|multi)[\s-]?(?:factor|step)\b|\b2fa\b", "mfa"),
    (r"\be-mail", "email"),
    (r"\bopt[\s-]?(?:in|out)\b", "optin"),
)]
_SAME = {w: group[0] for group in (
    ("login", "signin", "logon", "authentication", "auth"),
    ("signup", "register", "registration", "enrol", "enroll"),
    ("export", "download", "save"),
    ("spreadsheet", "excel", "csv", "xlsx", "sheet", "sheets"),
    ("notification", "notify", "alert", "push", "reminder", "remind"),
    ("email", "mail", "inbox"),
    ("dark", "night", "black"),
    ("theme", "mode", "skin"),
    ("delete", "remove", "erase"),
    ("search", "find", "lookup"),
    ("picture", "photo", "image", "pic", "avatar"),
    ("password", "passcode", "pin"),
    ("color", "colour"),
    ("phone", "mobile", "cell"),
    ("print", "printout", "printable"),
    ("report", "summary", "digest", "roundup"),
    ("calendar", "schedule", "timetable"),
    ("message", "text", "sms", "chat"),
    ("payment", "pay", "billing", "invoice", "checkout"),
    ("copy", "duplicate", "clone"),
    ("edit", "change", "update", "modify"),
    ("show", "display", "view", "see"),
) for w in group}


def _stem(w: str) -> str:
    """Rough and ready: enough that "exports", "exported" and "exporting" land on one word."""
    if not w.isascii() or len(w) < 4:
        return w
    if w.endswith("ies"):
        w = w[:-3] + "y"
    elif w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    for end in ("ing", "ed"):
        if w.endswith(end) and len(w) - len(end) >= 3:
            w = w[: -len(end)]
            break
    if len(w) > 3 and w.endswith("e"):
        w = w[:-1]
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "lsz":
        w = w[:-1]
    return w


def words(text: str) -> list[str]:
    text = str(text or "")[:MAX_TEXT].lower()
    for pattern, word in _PHRASES:
        text = pattern.sub(f" {word} ", text)
    out = []
    for w in _WORD.findall(text):
        if w in _COMMON or (len(w) < 2 and not w.isdigit()):
            continue
        w = _SAME.get(w, w)
        out.append(_SAME.get(_stem(w), _stem(w)))
    return out


def _counts(text: str) -> dict[str, float]:
    return {w: 1 + math.log(n) for w, n in Counter(words(text)).items()}


class Doc:
    """One idea, as the two bags of words it is compared on."""
    __slots__ = ("id", "head", "body")

    def __init__(self, id: str, title: str = "", summary: str = "", rest: str = ""):
        self.id = id
        self.head = _counts(f"{title}\n{summary}")
        self.body = _counts(f"{title}\n{summary}\n{rest}")


_held: dict[str, tuple[str, Doc]] = {}


def doc_for(card: dict) -> Doc:
    """The card as a Doc. Kept until the card next changes, since a board is compared against itself often."""
    stamp = card.get("updated_at") or ""
    held = _held.get(card["id"])
    if held and stamp and held[0] == stamp:
        return held[1]
    doc = Doc(card["id"], card.get("title", ""), card.get("summary", ""),
              "\n".join(str(card.get(k) or "") for k in ("problem", "scope", "original_text")))
    if stamp:
        if len(_held) >= 4000:
            _held.clear()
        _held[card["id"]] = (stamp, doc)
    return doc


def _weights(docs: list[Doc]) -> dict[str, float]:
    """How much each word tells ideas apart. With only a handful of ideas, every word counts the same."""
    n = len(docs)
    if n < 6:
        return {}
    seen = Counter(w for d in docs for w in d.body)
    return {w: max(0.15, math.log((n + 1) / (df + 0.5)) / math.log(n + 1)) for w, df in seen.items()}


def _cosine(a: dict, b: dict, weight: dict) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = sum(v * b[w] * weight.get(w, 1.0) ** 2 for w, v in a.items() if w in b)
    if not dot:
        return 0.0
    na = math.sqrt(sum((v * weight.get(w, 1.0)) ** 2 for w, v in a.items()))
    nb = math.sqrt(sum((v * weight.get(w, 1.0)) ** 2 for w, v in b.items()))
    return dot / (na * nb)


def score(a: Doc, b: Doc, weight: dict) -> float:
    """0 to 1. Headlines that match are strong evidence by themselves; matching bodies back them up. An idea
    with a long ramble under it is still found by its headline, and a bare one-liner by its few words."""
    head = max(_cosine(a.head, b.head, weight), _cosine(a.head, b.body, weight) * 0.8, _cosine(a.body, b.head, weight) * 0.8)
    body = _cosine(a.body, b.body, weight)
    shared = len(a.body.keys() & b.body.keys())
    s = 0.6 * head + 0.4 * body if head and body else max(head, body) * 0.6
    # One shared word is a coincidence more often than not.
    return s * (0.5 if shared < 2 else 1.0)


def how_close(s: float) -> str:
    return "close" if s >= CLOSE else "related" if s >= RELATED else ""


def rank(query: Doc, docs: list[Doc], limit: int = 5) -> list[tuple[str, float]]:
    """The ideas most like this one, best first, leaving out anything that is barely related."""
    weight = _weights(docs + [query])
    scored = [(d.id, score(query, d, weight)) for d in docs if d.id != query.id]
    return sorted([x for x in scored if x[1] >= RELATED], key=lambda x: -x[1])[:limit]


def pairs(docs: list[Doc], limit: int = 20) -> list[tuple[str, str, float]]:
    """Every pair of ideas that look like the same thing, closest first."""
    weight = _weights(docs)
    out = []
    for i, a in enumerate(docs):
        for b in docs[i + 1:]:
            if a.body.keys().isdisjoint(b.body.keys()):
                continue
            s = score(a, b, weight)
            if s >= CLOSE:
                out.append((a.id, b.id, s))
    return sorted(out, key=lambda x: -x[2])[:limit]
