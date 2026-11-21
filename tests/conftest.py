import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import pytest


@pytest.fixture(autouse=True)
def roomy(request, monkeypatch):
    """Most tests are about something else and use more links, trackers or channels than one project has room for.
    Only test_free.py runs with the edition's real limits."""
    if request.module.__name__ == "test_free":
        return
    from innerface import plans
    for name in ("share_links", "trackers", "chat_channels"):
        monkeypatch.setitem(plans.LIMITS, name, None)
