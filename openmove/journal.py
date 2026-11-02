# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The journal: an append-only record of events and of every step the engine takes.

A step is written down before it runs (intent) and completed afterwards. After a crash,
any step that has an intent and no result is returned by unresolved_steps() and must be
settled by reading the real state of the cloud (R5, R6).

Secrets never reach the journal: values stored under a key that looks like a secret are
replaced before they are written.
"""
import json
import re
import sqlite3
import threading
from datetime import datetime, timezone

SECRET_KEY = re.compile(r"pass(word|wd)?|secret|token|credential|private|api[_-]?key", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    kind    TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    data    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, subject);
CREATE TABLE IF NOT EXISTS steps (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job      TEXT NOT NULL,
    volume   TEXT NOT NULL DEFAULT '',
    name     TEXT NOT NULL,
    intent   TEXT NOT NULL,
    inverse  TEXT NOT NULL,
    status   TEXT NOT NULL,
    started  TEXT NOT NULL,
    finished TEXT,
    evidence TEXT
);
CREATE INDEX IF NOT EXISTS steps_status ON steps(status);
"""

STEP_FINAL = ("done", "failed", "reversed")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def scrub(value):
    """Return a copy of value with secret-looking fields replaced."""
    if isinstance(value, dict):
        return {k: ("[redacted]" if SECRET_KEY.search(str(k)) else scrub(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


class Journal:
    def __init__(self, path):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(SCHEMA)

    def close(self):
        with self._lock:
            self._db.close()

    @staticmethod
    def _dump(data):
        return json.dumps(scrub(data), sort_keys=True, default=str)

    # ---- events -------------------------------------------------------------
    def record(self, kind, subject="", data=None):
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events(ts, kind, subject, data) VALUES (?,?,?,?)",
                (now(), kind, subject, self._dump(data if data is not None else {})))
            return cur.lastrowid

    def events(self, kind=None, subject=None, limit=200):
        sql, args = "SELECT * FROM events", []
        cond = []
        if kind:
            cond.append("kind=?")
            args.append(kind)
        if subject is not None:
            cond.append("subject=?")
            args.append(subject)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "kind": r["kind"], "subject": r["subject"],
                 "data": json.loads(r["data"])} for r in rows]

    def latest(self, kind, subject=None):
        found = self.events(kind, subject, 1)
        return found[0] if found else None

    # ---- steps --------------------------------------------------------------
    def begin_step(self, job, name, intent, inverse, volume=""):
        """Write the intent before the action runs. inverse says how to undo it, or states that it cannot be undone."""
        if not inverse:
            raise ValueError("a step needs a recorded inverse (or an explicit 'cannot be undone')")
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO steps(job, volume, name, intent, inverse, status, started) VALUES (?,?,?,?,?,?,?)",
                (job, volume, name, self._dump(intent), self._dump(inverse), "intent", now()))
            return cur.lastrowid

    def finish_step(self, step_id, status, evidence=None):
        if status not in STEP_FINAL:
            raise ValueError("status must be one of %s" % (STEP_FINAL,))
        with self._lock:
            cur = self._db.execute(
                "UPDATE steps SET status=?, finished=?, evidence=? WHERE id=? AND status='intent'",
                (status, now(), self._dump(evidence if evidence is not None else {}), step_id))
            if cur.rowcount != 1:
                raise ValueError("step %s is not open" % step_id)

    def unresolved_steps(self):
        with self._lock:
            rows = self._db.execute("SELECT * FROM steps WHERE status='intent' ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def steps(self, job=None, limit=500):
        sql, args = "SELECT * FROM steps", []
        if job:
            sql += " WHERE job=?"
            args.append(job)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]
