"""What the free edition includes.

This edition has one set of limits and they are fixed here: there is no PLAN setting to change them. Some things
other editions have are not part of it at all. The code for them is not in this repository.
"""
from .config import Settings

FEATURES = {
    "share_links": "roadmap links",
    "changelog": "the list of what shipped",
}

# The most a project can have. None is no limit of this edition's own (the app's own caps still apply).
LIMITS = {"projects": None, "share_links": 1, "trackers": 1, "chat_channels": 1}

THINGS = {"projects": ("project", "projects", ""), "share_links": ("roadmap link", "roadmap links", " a project"),
          "trackers": ("tracker", "trackers", " a project"), "chat_channels": ("chat channel", "chat channels", " a project")}


def has(s: Settings, feature: str) -> bool:
    return feature in FEATURES


def need(s: Settings, feature: str) -> None:
    if not has(s, feature):
        from .service import ServiceError
        raise ServiceError(402, "That isn't part of the free edition.")


def limit(s: Settings, name: str) -> int | None:
    return LIMITS[name]


def name(s: Settings) -> str:
    return "free"


def full(s: Settings, limit_name: str) -> str:
    """The sentence for someone who has reached a limit. Safe to show."""
    one, many, per = THINGS[limit_name]
    room = LIMITS[limit_name]
    return f"The free edition has room for {room} {one if room == 1 else many}{per}."


def summary(s: Settings) -> dict:
    """For the app, so it can leave out what this edition doesn't have."""
    return {"name": "free", "features": {"build_phrase": False, "share_links": True, "changelog": True, "signin_addons": False, "approvals": False},
            "limits": dict(LIMITS)}
