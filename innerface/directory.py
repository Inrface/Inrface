"""People and projects kept in the app, laid over innerface.toml.

The settings file still names the first people and projects, and whatever it says keeps working. From the app, a
receiver can add someone to a project, change what they are in it, take them out, rename the project, and start a
new one. What is saved here wins over the file for that person in that project.

Only the web app changes any of this. AI apps and the other ways in have no tool for it, so nothing a card or a
repo says can talk its way into a project.
"""
import dataclasses
import logging
import re
import threading

from . import db, plans
from .config import Person, Project, Settings

log = logging.getLogger("innerface")

ROLES = ("giver", "receiver")
OUT = "none"            # saved for someone the settings file lists but who has been taken out here
MAX_PEOPLE = 200        # in one project
MAX_PROJECTS = 50
LOG_SHOWN = 20

_EMAIL = re.compile(r"^[^@\s<>\"',;:\\()\[\]]{1,64}@[^@\s<>\"',;:\\()\[\]]{1,180}\.[^@\s<>\"',;:\\()\[\]]{2,60}$")
_lock = threading.Lock()


def _error(status: int, message: str):
    from .service import ServiceError
    return ServiceError(status, message)


def _name(text, most: int, what: str) -> str:
    name = " ".join(str(text or "").split())
    if not name or len(name) > most or any(ord(ch) < 32 for ch in name):
        raise _error(422, f"{what} needs a name of up to {most} characters")
    return name


# ---------- laying what is saved over the file ----------

def _laid_over(s: Settings, c) -> tuple[dict, dict]:
    people = dict(s.file_people)
    for r in c.execute("SELECT email, name FROM dir_people"):
        was = people.get(r["email"])
        people[r["email"]] = Person(key=was.key if was else "", email=r["email"], name=r["name"])
    names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM dir_projects ORDER BY created_at, id")}
    saved: dict[str, dict[str, str]] = {}
    for r in c.execute("SELECT project_id, email, role FROM dir_members ORDER BY set_at, email"):
        saved.setdefault(r["project_id"], {})[r["email"]] = r["role"]
    projects = {}
    for pid in [*s.file_projects, *(i for i in names if i not in s.file_projects)]:
        base = s.file_projects.get(pid) or Project(id=pid, name=names[pid], givers=(), receivers=())
        roles = {e: "receiver" for e in base.receivers}
        roles.update({e: "giver" for e in base.givers if e not in roles})
        roles.update(saved.get(pid, {}))
        receivers = tuple(e for e, r in roles.items() if r == "receiver" and e in people)
        givers = tuple(e for e, r in roles.items() if r == "giver" and e in people)
        if not receivers:
            if pid not in s.file_projects:
                continue
            # The file changed under what was saved. A project nobody receives is no use, so the file's own list stands.
            log.warning("project %s would have no receiver with the changes saved in the app, so the settings file's list is used", pid)
            receivers, givers = base.receivers, base.givers
        projects[pid] = dataclasses.replace(base, name=names.get(pid) or base.name, receivers=receivers, givers=givers)
    return people, projects


def refresh(s: Settings) -> None:
    """Work out who is who and swap it in. Called at start, and after every change made here."""
    if s.file_people is None:
        return
    with _lock, db.connect(s) as c:
        people, projects = _laid_over(s, c)
        for email in people:
            c.execute("INSERT OR IGNORE INTO users(email) VALUES (?)", (email,))
    s.use(people, projects)


# ---------- reading ----------

def _receiver(s: Settings, user: dict, project_id: str) -> Project:
    from .service import _receiver_project
    return _receiver_project(s, user, project_id)


def people(s: Settings, user: dict, project_id: str) -> dict:
    """Who is in a project, with their addresses. For that project's receivers only."""
    p = _receiver(s, user, project_id)
    filed = s.file_projects.get(project_id)
    with db.connect(s) as c:
        rows = c.execute("SELECT actor_email, what, created_at FROM dir_log WHERE project_id=? ORDER BY id DESC LIMIT ?", (project_id, LOG_SHOWN)).fetchall()
    return {
        "people": [{"email": e, "name": s.name(e), "role": p.role(e), "you": e == user["email"],
                    # Named in the settings file with this same role. Taking them out here works, and is remembered here.
                    "in_file": bool(filed and filed.role(e) == p.role(e))} for e in p.members],
        "changes": [{"at": r["created_at"], "by": s.name(r["actor_email"]), "what": r["what"]} for r in rows],
        "most": MAX_PEOPLE,
    }


# ---------- changing ----------

def _log(c, project_id: str, actor: str, what: str) -> None:
    c.execute("INSERT INTO dir_log(project_id, actor_email, what, created_at) VALUES (?,?,?,?)", (project_id, actor, what, db.now()))


def set_member(s: Settings, user: dict, project_id: str, email: str, role: str, name: str = "") -> dict:
    """Add someone to a project, change what they are in it, or (role "none") take them out."""
    from . import service
    p = _receiver(s, user, project_id)
    email = str(email or "").strip().lower()
    if len(email) > 254 or not _EMAIL.match(email):
        raise _error(422, "That doesn't look like an email address")
    if role not in (*ROLES, OUT):
        raise _error(422, "A person is a giver or a receiver")
    was, known = p.role(email), s.people.get(email)
    if role == OUT and not was:
        raise _error(404, "They aren't in this project")
    if was == "receiver" and role != "receiver" and len(p.receivers) == 1:
        raise _error(422, "A project needs at least one receiver. Make someone else a receiver first.")
    if not was and len(p.members) >= MAX_PEOPLE:
        raise _error(422, f"A project can have {MAX_PEOPLE} people")
    new_name = _name(name, 80, "A person") if name or not known else ""
    if was == role and (not new_name or new_name == known.name):
        return people(s, user, project_id)
    shown = new_name or known.name
    with db.connect(s) as c:
        if new_name and (not known or new_name != known.name):
            c.execute("""INSERT INTO dir_people(email, name, added_by, added_at) VALUES (?,?,?,?)
                         ON CONFLICT(email) DO UPDATE SET name=excluded.name""", (email, new_name, user["email"], db.now()))
            if known:
                _log(c, project_id, user["email"], f"renamed {known.name} to {new_name}")
        if was != role:
            c.execute("""INSERT INTO dir_members(project_id, email, role, set_by, set_at) VALUES (?,?,?,?,?)
                         ON CONFLICT(project_id, email) DO UPDATE SET role=excluded.role, set_by=excluded.set_by, set_at=excluded.set_at""",
                      (project_id, email, role, user["email"], db.now()))
            what = (f"took {shown} out" if role == OUT else f"added {shown} as a {role}" if not was else f"made {shown} a {role}")
            _log(c, project_id, user["email"], what)
    refresh(s)
    if role == OUT and not s.projects_for(email):
        # Out of their last project: their browsers and AI apps are signed out, so adding them back later starts clean.
        from . import signin
        signin.forget(s, email)
    if was != role:
        # The other receivers hear about it: who can see a project's ideas is theirs to know.
        tell = [e for e in p.receivers if e not in (user["email"], email)]
        said = (f"took {shown} out of {p.name}" if role == OUT else f"added {shown} to {p.name} as a {role}" if not was
                else f"made {shown} a {role} in {p.name}")
        service.notify(s, tell, "People changed", f"{user['name']} {said}.")
        if was and role != OUT and email != user["email"]:
            service.notify(s, [email], "Your role changed", f"You are now a {role} in {p.name}.")
    if email == user["email"] and role != "receiver":
        return {"people": [], "changes": [], "most": MAX_PEOPLE, "left": True}
    return people(s, user, project_id)


def _slug(name: str, taken) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32].strip("-") or "project"
    slug, n = base, 1
    while slug in taken:
        n += 1
        slug = f"{base}-{n}"
    return slug


def create_project(s: Settings, user: dict, name: str) -> dict:
    """Anyone who already receives a project can start another. They are its first receiver."""
    if "receiver" not in user["projects"].values():
        raise _error(403, "Only a receiver can start a project")
    name = _name(name, 60, "A project")
    with _lock, db.connect(s) as c:
        taken = set(s.projects) | {r["id"] for r in c.execute("SELECT id FROM dir_projects")}
        if len(s.projects) >= MAX_PROJECTS:
            raise _error(422, f"This install can have {MAX_PROJECTS} projects")
        room = plans.limit(s, "projects")
        if room is not None and len(s.projects) >= room:
            raise _error(402, plans.full(s, "projects"))
        if name.lower() in {p.name.lower() for p in s.projects.values()}:
            raise _error(422, "There is already a project with that name")
        pid = _slug(name, taken)
        c.execute("INSERT INTO dir_projects(id, name, created_by, created_at) VALUES (?,?,?,?)", (pid, name, user["email"], db.now()))
        c.execute("INSERT INTO dir_members(project_id, email, role, set_by, set_at) VALUES (?,?,?,?,?)", (pid, user["email"], "receiver", user["email"], db.now()))
        _log(c, pid, user["email"], "started the project")
    refresh(s)
    return {"id": pid, "name": name}


def rename_project(s: Settings, user: dict, project_id: str, name: str) -> dict:
    p = _receiver(s, user, project_id)
    name = _name(name, 60, "A project")
    if name == p.name:
        return {"id": project_id, "name": name}
    if name.lower() in {x.name.lower() for x in s.projects.values() if x.id != project_id}:
        raise _error(422, "There is already a project with that name")
    with db.connect(s) as c:
        c.execute("""INSERT INTO dir_projects(id, name, created_by, created_at) VALUES (?,?,?,?)
                     ON CONFLICT(id) DO UPDATE SET name=excluded.name""", (project_id, name, user["email"], db.now()))
        _log(c, project_id, user["email"], f"renamed the project from {p.name} to {name}")
    refresh(s)
    return {"id": project_id, "name": name}
