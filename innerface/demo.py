"""Fill a try-it install with sample ideas, so there is something to look at.

    docker compose -f docker-compose.try.yml exec app python -m innerface.demo

It only runs where sign-in is switched off (AUTH_MODE=dev) and only into a database with no ideas in it,
so it can never add made-up ideas to a real install. The ideas are for a made-up school app. They are
written straight into the database with dates spread over the last few weeks, which is why this doesn't
go through the service layer: nothing is sent to anyone, and no build is started anywhere.
"""
import json
import sys

from . import db
from .config import ConfigError, Settings, get_settings

HOUR, DAY = 3600, 86400

# Each idea: (title, source, sent this many days ago, by the giver or the receiver, fields, what happened next).
# What happened next is a list of (hours after it was sent, who, what, detail, where it was done).
IDEAS = [
    ("Print a register for a cover teacher", "mcp", 0.02, "giver", dict(
        original_text="When someone's off sick the cover teacher has no idea who's meant to be in the room. I'd like a one-page register they can print.",
        summary="A one-page register for a class that a cover teacher can print before the lesson.",
        who="Cover teachers and the office", problem="A cover teacher doesn't know who should be in the room."), []),
    ("Weekly numbers by email", "mcp", 0.2, "giver", dict(
        original_text="I keep logging in every Monday just to look at the same three numbers. Can it just email them to me? Sign-ups, active families and anything that failed. Monday morning before 8 would be ideal.",
        summary="Email the three weekly numbers every Monday morning so nobody has to log in to check them.",
        who="The two owners", problem="Checking the numbers means logging in every week to look at the same three figures.",
        scope="In: one email a week with sign-ups, active families and failures. Out: charts, and any choice of numbers.",
        open_questions=["Should it go to both owners or just one?", "What counts as a failure?"]), []),
    ("Dark mode for the dashboard", "typed", 1, "giver", dict(original_text="Dark mode for the dashboard\nIt's really bright at night."), [
        (3, "receiver", "ask", "Should it follow the phone's setting, or have its own switch?", "web")]),
    # The same idea again in other words, so the board has a pair to suggest merging.
    ("A night theme", "typed", 0.4, "receiver", dict(original_text="A night theme\nThe dashboard is far too bright to use in the evening."), []),
    ("Copy last year's lesson plans into this year", "mcp", 2, "giver", dict(
        original_text="Every September the teachers rebuild the same lesson plans. Let them copy last year's across, attachments and all.",
        summary="Let a teacher copy last year's lesson plans into the new year, with attachments.",
        who="Teachers", problem="Plans are rebuilt by hand every September."), [
        (5, "receiver", "ask", "Should their private notes come across too, or just the plan and attachments?", "mcp"),
        (9, "giver", "answer", "Notes too. They write those for themselves and lose them otherwise.", "mcp")]),
    ("Export the class list to a spreadsheet", "mcp", 3, "giver", dict(
        original_text="Teachers keep asking me for the class list in a spreadsheet so they can sort it themselves.",
        summary="A button on the class page that downloads the list as a spreadsheet file.", who="Teachers",
        problem="Teachers copy names out by hand to make their own lists.",
        priority="High", tags=["teachers", "quick win"]), [
        (4, "receiver", "scope", dict(scope="In: names, year group and contact. Out: grades.",
                                       acceptance=["Downloads a file that opens in Excel and Sheets", "Works for a class of 200"]), "mcp"),
        (4.1, "receiver", "file", "Immediate roadmap", "mcp")]),
    ("A login for parents so they can see homework", "typed", 6, "giver", dict(
        original_text="A login for parents so they can see homework",
        code_check=dict(says="partly", summary="Teachers already sign in with an email and a one-time code. Families are stored, but a family has no way to sign in, and homework isn't linked to a family yet.",
                        places=[dict(path="app/auth/login.py", why="How teachers sign in today"), dict(path="app/models/family.py", why="Families exist but have no login")])), [
        (20, "receiver", "file", "6-month roadmap", "web"),
        (20.1, "receiver", "note", "Good idea, after the teacher tools settle.", "web")]),
    ("Remind families the day before a trip", "mcp", 8, "giver", dict(
        original_text="Half the class forgot their packed lunch for the museum trip. A reminder the day before would have saved it.",
        summary="Send a reminder the day before any trip on the calendar.", who="Families", problem="Families forget what to bring."), [
        (6, "receiver", "scope", dict(scope="In: one message at 4pm the day before. Out: replies.",
                                       acceptance=["Goes out at 4pm the day before", "Only to families going on the trip"]), "mcp"),
        (7, "receiver", "build", "", "mcp")]),
    ("Voice notes inside a lesson plan", "typed", 9, "receiver", dict(original_text="Voice notes inside a lesson plan"), [
        (0.1, "receiver", "file", "Far down the line", "web")]),
    ("Change every button to purple", "typed", 12, "giver", dict(original_text="Change every button to purple"), [
        (30, "receiver", "file", "Rejected", "web"),
        (30.1, "receiver", "note", "We'd lose the colour meanings we already use.", "web")]),
    ("Show the term dates on the home page", "mcp", 20, "giver", dict(
        original_text="Parents ring the office to ask when term ends. Put the dates on the home page.",
        summary="Show this term's start and end dates on the home page.", who="Families", problem="The office answers the same question by phone."), [
        (2, "receiver", "scope", dict(scope="In: this term's dates. Out: a full calendar.", acceptance=["Shows the current term's start and end dates"]), "mcp"),
        (3, "receiver", "build", "", "mcp"),
        (30, "github", "done", "", "github")]),
    ("Search by family name", "typed", 26, "giver", dict(original_text="Search by family name\nRight now you can only search by the child's name."), [
        (26, "receiver", "scope", dict(scope="In: search by a parent's or carer's surname. Out: fuzzy matching.", acceptance=["Typing a surname finds every child in that family"]), "web"),
        (27, "receiver", "build", "", "web"),
        (80, "github", "done", "", "github")]),
    ("Let the office mark a child as off sick", "mcp", 41, "giver", dict(
        original_text="The office takes the sick calls but only teachers can mark it.",
        summary="Let office staff mark a child as off sick for the day.", who="Office staff", problem="Only teachers can record an absence."), [
        (8, "receiver", "scope", dict(scope="In: today only. Out: planned absences.", acceptance=["The office can mark today's absence", "The teacher sees it on the register"]), "mcp"),
        (10, "receiver", "build", "", "mcp"),
        (60, "github", "done", "", "github")]),
]


class DemoError(Exception):
    pass


def fill(s: Settings) -> int:
    """Write the sample ideas and return how many. Raises DemoError where this must not run."""
    if s.auth_mode != "dev":
        raise DemoError("This only runs on a try-it install, where sign-in is switched off (AUTH_MODE=dev). Nothing was added.")
    project = next(iter(s.projects.values()), None)
    if not project or not project.receivers:
        raise DemoError("The settings file needs a project with at least one receiver.")
    who = {"receiver": project.receivers[0], "giver": (project.givers or project.receivers)[0], "github": "github"}
    db.init(s)
    with db.connect(s) as c:
        if c.execute("SELECT COUNT(*) FROM cards").fetchone()[0]:
            raise DemoError("There are already ideas here, so nothing was added. Sample ideas only go into an empty install.")

        def log(cid, at, actor, kind, detail, via):
            c.execute("INSERT INTO activity(id, card_id, project_id, actor_email, kind, detail, via, created_at) VALUES (?,?,?,?,?,?,?,?)",
                      (db.new_id(), cid, project.id, actor, kind, detail, via, at))

        def say(cid, at, author, text, ctype):
            c.execute("INSERT INTO comments(id, card_id, author_email, text, type, created_at) VALUES (?,?,?,?,?,?)", (db.new_id(), cid, author, text, ctype, at))

        for title, source, days, by, fields, steps in IDEAS:
            cid, sent = db.new_id(), days * DAY
            f = {"status": "New", "note": "", "scope": fields.get("scope", ""), "acceptance": [], "build_started_at": None, "updated_at": db.ago(sent)}
            # What an AI app would have saved after looking through the project's code. The sample has no repo behind it.
            check = fields.get("code_check")
            check = json.dumps({**check, "by": who["receiver"], "at": db.ago(sent - HOUR), "repo": "", "commit": "", "as_of": "", "verified": True}) if check else ""
            c.execute(
                """INSERT INTO cards(id, title, summary, who, problem, open_questions, original_text, source, author_email, project_id,
                                     priority, tags, code_check, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, title, fields.get("summary", ""), fields.get("who", ""), fields.get("problem", ""), json.dumps(fields.get("open_questions", [])),
                 fields.get("original_text", title), source, who[by], project.id, fields.get("priority", ""), json.dumps(fields.get("tags", [])),
                 check, db.ago(sent), db.ago(sent)))
            log(cid, db.ago(sent), who[by], "created", title, "mcp" if source == "mcp" else "web")
            for after, actor, what, detail, where in steps:
                at = db.ago(max(sent - after * HOUR, 0))
                f["updated_at"] = at
                if what == "ask":
                    f.update(status="Needs info", note=detail)
                    say(cid, at, who[actor], detail, "clarification")
                    log(cid, at, who[actor], "status", "Needs info", where)
                elif what == "answer":
                    f["status"] = "Answered"
                    say(cid, at, who[actor], detail, "reply")
                    log(cid, at, who[actor], "answered", detail, where)
                elif what == "note":
                    f["note"] = detail
                    say(cid, at, who[actor], detail, "note")
                elif what == "file":
                    f["status"] = detail
                    f["shipped_at"] = at if detail == "Done / Live" else None
                    log(cid, at, who[actor], "status", detail, where)
                elif what == "scope":
                    f.update(detail)
                    log(cid, at, who[actor], "scoped", "Updated the scope", where)
                elif what == "build":
                    f.update(status="In dev for testing", build_started_at=at)
                    log(cid, at, who[actor], "build", "Sample build", where)
                elif what == "done":
                    f.update(status="Done / Live", shipped_at=at)
                    log(cid, at, "github", "status", "Done / Live", "github")
            c.execute("UPDATE cards SET status=?, note=?, scope=?, acceptance=?, build_started_at=?, updated_at=?, shipped_at=? WHERE id=?",
                      (f["status"], f["note"], f["scope"], json.dumps(f["acceptance"]), f["build_started_at"], f["updated_at"], f.get("shipped_at"), cid))
    return len(IDEAS)


def main(argv: list[str] | None = None) -> int:
    if sys.argv[1:] if argv is None else argv:
        print("Usage: python -m innerface.demo", file=sys.stderr)
        return 2
    try:
        n = fill(get_settings())
    except (DemoError, ConfigError) as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Added {n} sample ideas. Reload the page to see them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
