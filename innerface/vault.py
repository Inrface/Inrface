"""Encrypts tokens before they are saved, when INNERFACE_SECRET_KEY is set.

The key lives in the environment, not in the data folder, so a copy of the database or a backup
doesn't hold usable tokens. Without a key, values are saved as they are.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from . import db
from .config import Settings

PREFIX = "enc:v1:"


class VaultError(Exception):
    """A saved value can't be read: the key is missing or has changed."""


def _fernet(s: Settings) -> Fernet | None:
    if not s.secret_key:
        return None
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(s.secret_key.encode()).digest()))


def seal(s: Settings, text: str) -> str:
    f = _fernet(s)
    if not f or not text:
        return text
    return PREFIX + f.encrypt(text.encode()).decode()


def unseal(s: Settings, blob: str) -> str:
    if not blob.startswith(PREFIX):
        return blob
    f = _fernet(s)
    if not f:
        raise VaultError("INNERFACE_SECRET_KEY is not set, so saved tokens can't be read.")
    try:
        return f.decrypt(blob[len(PREFIX):].encode()).decode()
    except InvalidToken:
        raise VaultError("INNERFACE_SECRET_KEY has changed, so saved tokens can't be read.")


def seal_existing(s: Settings) -> int:
    """Encrypt anything saved before the key was set. Runs at startup; does nothing without a key."""
    if not s.secret_key:
        return 0
    n = 0
    with db.connect(s) as c:
        for table, column, key in (("project_github", "token", "project_id"), ("connectors", "secrets", "id")):
            for r in c.execute(f"SELECT {key} AS k, {column} AS v FROM {table} WHERE {column} != '' AND {column} NOT LIKE ?", (PREFIX + "%",)).fetchall():
                c.execute(f"UPDATE {table} SET {column}=? WHERE {key}=?", (seal(s, r["v"]), r["k"]))
                n += 1
    return n
