"""Looking through a project's code, so an AI app can say "that's already there" or "that would clash" while an idea is talked through.

Inrface keeps a copy of each linked repo's default branch in memory. It is fetched from GitHub with the project's
token, checked for changes every couple of minutes, and thrown away on restart. Nothing is written to disk, nothing
in the repo is run, and files that look like they hold secrets are never kept.
"""
import fnmatch
import io
import logging
import math
import posixpath
import re
import sys
import tarfile
import threading
import time
from dataclasses import dataclass, field

from . import github
from .config import Project, Settings

log = logging.getLogger("innerface.code")

MAX_ARCHIVE = 2_000_000_000  # the compressed download, read as it comes and never held whole
MAX_UNPACKED = 6_000_000_000  # how much of the archive is looked through
MAX_TEXT = 40_000_000       # how much memory the kept text may take, per repo
MAX_FILE = 400_000          # bigger files are listed, not kept
MAX_FILES = 20_000
CHECK_EVERY = 120           # seconds between asking GitHub whether the branch moved
RETRY_AFTER = 30            # seconds before a download that failed is tried again
KEEP = 4                    # repos held in memory at once

SEARCH_FILES = 12
SEARCH_LINES = 4
LINE_CUT = 240
READ_LINES = 250
READ_CHARS = 20_000
LIST_MAX = 300

# Files that tend to hold passwords and keys. They are named in listings and never kept or shown.
_SECRET_NAMES = ("*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "*.kdbx", "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
                 ".npmrc", ".pypirc", ".netrc", ".envrc", ".git-credentials", ".htpasswd", "credentials", "credentials.*", "secrets.*", "secret.*",
                 "*.tfstate", "*.tfstate.*", "*.tfvars", "service-account*.json", "*.ovpn")
_ENV_OK = ("example", "sample", "template", "dist", "defaults")
# Written by tools, not people: big, and noise in a search.
_GENERATED = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Cargo.lock", "Pipfile.lock", "composer.lock",
              "Gemfile.lock", "go.sum", "*.min.js", "*.min.css", "*.map")
_SKIP_DIRS = {".git", "node_modules"}


class CodeError(Exception):
    """The message is safe to show."""


@dataclass
class Snapshot:
    repo: str
    sha: str
    date: str
    files: dict[str, str]
    left_out: dict[str, str]  # path -> why it can't be read
    partial: bool = False     # the repo was too big to hold all of it
    checked_at: float = field(default=0.0)
    used_at: float = field(default=0.0)

    @property
    def about(self) -> dict:
        out = {"repo": self.repo, "as_of": self.date or "the latest commit", "commit": self.sha[:7]}
        if self.partial:
            out["incomplete"] = "This repo is too big to hold in full, so some files are missing from searches."
        return out


def secret_name(path: str) -> bool:
    name = posixpath.basename(path).lower()
    if name == ".env" or name.startswith(".env.") or name.endswith(".env"):
        return not any(part in _ENV_OK for part in name.split("."))
    return any(fnmatch.fnmatchcase(name, pat) for pat in _SECRET_NAMES)


def clean_path(path: str) -> str:
    """A repo path as the snapshot keys it: forward slashes, no leading slash, nothing that climbs out."""
    path = (path or "").strip().replace("\\", "/").strip("/")
    if path in ("", "."):
        return ""
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts) or len(path) > 500:
        raise CodeError("That isn't a path inside the repo.")
    return "/".join(parts)


def unpack(source) -> tuple[dict[str, str], dict[str, str], bool]:
    """Read a GitHub tarball, as bytes or a stream, into {path: text}. Nothing is extracted to disk."""
    files: dict[str, str] = {}
    left_out: dict[str, str] = {}
    kept = seen = 0
    partial = False
    try:
        with tarfile.open(fileobj=io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source, mode="r|gz") as tar:
            for m in tar:
                if not m.isreg():
                    continue  # folders, links and devices carry no code
                seen += m.size
                if seen > MAX_UNPACKED or len(files) + len(left_out) >= MAX_FILES:
                    partial = True
                    break
                # GitHub puts everything inside one folder named after the repo and commit.
                _, _, path = m.name.replace("\\", "/").partition("/")
                parts = path.split("/")
                if not path or ".." in parts or _SKIP_DIRS.intersection(parts) or len(path) > 500:
                    continue
                name = parts[-1]
                if secret_name(path):
                    left_out[path] = "it may hold secrets"
                elif any(fnmatch.fnmatchcase(name, pat) for pat in _GENERATED):
                    left_out[path] = "it is written by a tool"
                elif m.size > MAX_FILE:
                    left_out[path] = "it is too big"
                elif kept + m.size > MAX_TEXT:
                    left_out[path] = "the repo is too big to hold all of it"
                    partial = True
                else:
                    f = tar.extractfile(m)
                    raw = f.read() if f else b""
                    if b"\0" in raw[:8000]:
                        left_out[path] = "it isn't text"
                        continue
                    files[path] = raw.decode("utf-8", errors="replace")
                    kept += sys.getsizeof(files[path])
    except (tarfile.TarError, EOFError, OSError) as e:
        log.warning("couldn't read the code archive: %s", type(e).__name__)
        raise CodeError("GitHub sent something that couldn't be read. Try again in a minute.")
    return files, left_out, partial


_snapshots: dict[tuple[str, str], Snapshot] = {}
_locks: dict[tuple[str, str], threading.Lock] = {}
_failed: dict[tuple[str, str], tuple[float, str]] = {}  # downloads that failed: when to try again, and what to say until then
_guard = threading.Lock()


def forget(project_id: str | None = None) -> None:
    """Drop the copies held in memory: one project's, or all of them."""
    with _guard:
        for held in (_snapshots, _failed):
            for key in [k for k in held if project_id in (None, k[0])]:
                del held[key]


def snapshot(s: Settings, project: Project) -> Snapshot:
    """The project's code as of the newest commit GitHub will show, fetching it if the branch has moved."""
    key = (project.id, project.repo.lower())
    with _guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        now = time.monotonic()
        snap = _snapshots.get(key)
        if snap and now - snap.checked_at < CHECK_EVERY:
            snap.used_at = now
            return snap
        if not snap and key in _failed and now < _failed[key][0]:
            raise CodeError(_failed[key][1])  # a big download just failed; asking again straight away won't help
        try:
            head = github.code_head(s, project)
            if not snap or snap.sha != head["sha"]:
                try:
                    with github.code_archive(s, project, head["sha"], MAX_ARCHIVE) as download:
                        files, left_out, partial = unpack(download)
                except github.CodeBlocked:
                    raise
                except (github.GitHubError, CodeError) as e:
                    _failed[key] = (now + RETRY_AFTER, str(e))
                    raise
                snap = Snapshot(project.repo, head["sha"], head["date"], files, left_out, partial)
                log.info("code for %s fetched at %s: %d files", project.id, head["sha"][:7], len(files))
        except github.CodeBlocked as e:
            with _guard:
                _snapshots.pop(key, None)  # the token lost its access, so the copy goes too
            raise CodeError(str(e))
        except (github.GitHubError, CodeError) as e:
            if not snap:
                raise CodeError(str(e))
            log.warning("serving the last copy of %s: %s", project.id, e)  # GitHub is down or busy; the copy is still good
        snap.checked_at = snap.used_at = now
        with _guard:
            _snapshots[key] = snap
            for old in sorted(_snapshots, key=lambda k: _snapshots[k].used_at)[:-KEEP]:
                del _snapshots[old]
        return snap


def _inside(path: str, where: str) -> bool:
    if not where:
        return True
    if any(ch in where for ch in "*?"):
        return fnmatch.fnmatchcase(path.lower(), where.lower()) or fnmatch.fnmatchcase(posixpath.basename(path).lower(), where.lower())
    return path == where or path.startswith(where + "/")


def _lines(text: str) -> list[str]:
    lines = text.split("\n")
    return lines[:-1] if len(lines) > 1 and lines[-1] == "" else lines


def _cut(line: str) -> str:
    line = line.strip()
    return line if len(line) <= LINE_CUT else line[:LINE_CUT] + "…"


# Words that say nothing about code. They are dropped from a search when it has other words.
_COMMON = frozenset("the and for with that this are was has have does did how what where when who why can could should our your you not any "
                    "there here use used using get from into about".split())


def _words(query: str) -> list[str]:
    words = [w.strip("?!.,;:\"'()[]{}") for w in re.split(r"[\s,]+", (query or "").lower())]
    return list(dict.fromkeys(w for w in words if w))[:12]


def _terms(words: list[str]) -> list[str]:
    useful = [w for w in words if len(w) >= 3 and w not in _COMMON]
    return (useful or [w for w in words if len(w) >= 2])[:8]


def _run_of(words: list[str]) -> re.Pattern | None:
    """Matches the words next to each other however code joins them: "sign in", sign_in, sign-in, signIn."""
    return re.compile(r"[\s_\-./]*".join(re.escape(w) for w in words)) if len(words) > 1 else None


def search(snap: Snapshot, query: str, path: str = "") -> dict:
    """Files that mention the words in `query`, best match first, each with a few of the lines that matched.

    A word counts for more the fewer files it appears in, and for less when it only turns up inside a longer word
    ("two" in "network"). Words found next to each other count for most, then words in the file's name. Tests rank a
    little below the code they test."""
    words = _words(query)
    terms = _terms(words)
    if not terms:
        raise CodeError("Say what to look for: a word or two that would appear in the code.")
    where = path if any(ch in (path or "") for ch in "*?") else clean_path(path)
    runs = [r for r in {(_run_of(words) or re.compile("$^")).pattern: _run_of(words), (_run_of(terms) or re.compile("$^")).pattern: _run_of(terms)}.values() if r]
    starts = {t: re.compile(r"(?<![a-z0-9])" + re.escape(t)) for t in terms}

    def together(low: str) -> bool:
        return any(r.search(low) for r in runs)

    def worth(t: str, low: str) -> float:
        """0 if the word isn't there, 1 if it starts a word, a little if it is only part of one."""
        return 0.0 if t not in low else 1.0 if starts[t].search(low) else 0.25

    rows = []
    seen_in = dict.fromkeys(terms, 0)
    for p, text in snap.files.items():
        if not _inside(p, where):
            continue
        low, low_path = text.lower(), p.lower()
        in_text = {t: worth(t, low) for t in terms}
        named = [t for t in terms if t in low_path]
        if not named and not any(in_text.values()):
            continue
        for t in terms:
            if in_text[t] or t in named:
                seen_in[t] += 1
        rows.append((p, text, low, in_text, named))
    weight = {t: 1 + math.log((len(rows) + 1) / (seen_in[t] + 1)) for t in terms}
    found = []
    side_by_side = 0
    for p, text, low, in_text, named in rows:
        score = sum(weight[t] * in_text[t] * (0.5 + min(low.count(t), 10) / 20) for t in terms) + sum(weight[t] for t in named)
        if together(low):
            score += 2 * sum(weight.values())
            side_by_side += 1
        parts = p.lower().split("/")
        if any(x in ("test", "tests", "spec", "specs", "__tests__") for x in parts[:-1]) or re.match(r"test_|.*[._]test\.|.*[._]spec\.", parts[-1]):
            score *= 0.7
        found.append((score, p, text, [t for t in terms if in_text[t]]))
    found.sort(key=lambda f: (-f[0], len(f[1]), f[1]))
    results = []
    for _, p, text, in_text in found[:SEARCH_FILES]:
        scored = []
        for n, line in enumerate(_lines(text), 1):
            low = line.lower()
            k = sum(weight[t] * worth(t, low) for t in in_text)
            if k:
                scored.append((-(k + (100 if together(low) else 0)), n, line))
        scored.sort()
        lines = [{"line": n, "text": _cut(line)} for _, n, line in sorted(scored[:SEARCH_LINES], key=lambda x: x[1])]
        results.append({"path": p, "lines": lines} if lines else {"path": p, "matched": "the file's name"})
    out = {**snap.about, "looked_for": terms, "files_matched": len(found), "results": results}
    if len(found) > len(results):
        out["note"] = f"Showing the best {len(results)} of {len(found)} files. Add a word or a path to narrow it."
    if found and runs and not side_by_side:
        out["note"] = "No file has these words together, so each result mentions only some of them. That often means it isn't there under this name."
    if not found:
        out["note"] = "Nothing in the code mentions that. Try another word for it before saying it doesn't exist."
    return out


def _listing(snap: Snapshot, folder: str) -> dict | None:
    prefix = folder + "/" if folder else ""
    folders: dict[str, int] = {}
    here = []
    for p in sorted([*snap.files, *snap.left_out]):
        if not p.startswith(prefix):
            continue
        rest = p[len(prefix):]
        if "/" in rest:
            name = rest.split("/", 1)[0]
            folders[name] = folders.get(name, 0) + 1
        elif p in snap.files:
            here.append({"file": rest, "lines": len(_lines(snap.files[p]))})
        else:
            here.append({"file": rest, "not_readable": snap.left_out[p]})
    if not folders and not here:
        return None
    entries = [{"folder": name + "/", "files": n} for name, n in sorted(folders.items())] + here
    out = {**snap.about, "folder": folder or "(top of the repo)", "contains": entries[:LIST_MAX]}
    if len(entries) > LIST_MAX:
        out["note"] = f"Showing {LIST_MAX} of {len(entries)} entries. Use search_code to find a file by name."
    return out


def read(snap: Snapshot, path: str = "", start_line: int = 1) -> dict:
    """A folder's contents, or part of a file with line numbers."""
    path = clean_path(path)
    if path in snap.left_out:
        raise CodeError(f"{path} can't be read from here because {snap.left_out[path]}.")
    if path not in snap.files:
        listing = _listing(snap, path)
        if listing:
            return listing
        name = posixpath.basename(path).lower()
        close = sorted((p for p in snap.files if posixpath.basename(p).lower() == name), key=len)[:5]
        raise CodeError(f"There's no file or folder at {path}." + (" Did you mean: " + ", ".join(close) + "?" if close else " Use search_code to find it."))
    lines = _lines(snap.files[path])
    start = max(1, int(start_line or 1))
    if start > len(lines):
        raise CodeError(f"{path} has {len(lines)} lines.")
    shown, size = [], 0
    for n in range(start, min(len(lines), start + READ_LINES - 1) + 1):
        line = lines[n - 1].rstrip("\r")
        line = line if len(line) <= 400 else line[:400] + "…"
        size += len(line) + 8
        if size > READ_CHARS and shown:
            break
        shown.append(f"{n}: {line}")
    end = start + len(shown) - 1
    out = {**snap.about, "path": path, "lines": f"{start}-{end} of {len(lines)}", "text": "\n".join(shown)}
    if end < len(lines):
        out["more"] = f"Call read_code again with start_line={end + 1} for the rest."
    return out
