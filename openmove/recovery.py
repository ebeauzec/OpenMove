# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Failsafe: a stop of OpenMove, of the computer or of the network in the middle of a move must cost nothing (docs/RECOVERY.md).

What survives a stop, and where it is kept:
  * every step of every volume ........ the journal (SQLite, written before each change and again after it). The engine settles a step
                                         whose answer was never read, so a stop between "sent" and "answered" is found and read back.
  * the pre-flight, the selection, the options, the releases and the place the person had reached in the steps
                                       .... resume.json in the data folder (this file). No password or token of the cloud is ever in it.
  * the access address of the page .... token file in the data folder, so the page that is open keeps working after a restart.
  * the approvals ..................... the journal (sign-offs and confirmations are kept per pre-flight run).

What a restart does: it finds that the last run of the program did not end cleanly, keeps resume.json as it is, and waits for the cloud
sign-in (the lab signs in by itself). Then it reads the cloud again, puts the saved context back, and carries on the move that was running.
Before every change the same permit as always is asked (release validated, gate approved, no halt), so a restart can never do
something the person had not already allowed. Nothing here sends a change to a cloud by itself except through the engine.
"""
import json
import os
import secrets
import sys
import threading
import time
from pathlib import Path

RESUME_FILE = "resume.json"
TOKEN_FILE = "access-token"
BEAT_EVERY = 15                      # seconds between two saves of the resume file while the program runs
GUARD_EVERY = 10                     # seconds between two looks of the guardian
MAX_AGE_HOURS = 24 * 14              # a resume file older than this is not put back; the person starts again from the pre-flight


# ---- the access token ------------------------------------------------------------------------------------------
def token(home):
    """The address token of this data folder. Kept in the folder so that the page that is open (and the printed address) still works after a restart.
    The file is readable by the person who runs OpenMove only. Delete it to get a new one."""
    p = Path(home) / TOKEN_FILE
    try:
        t = p.read_text(encoding="utf-8").strip()
        if len(t) >= 20:
            return t
    except OSError:
        pass
    t = secrets.token_urlsafe(24)
    try:
        Path(home).mkdir(parents=True, exist_ok=True)
        p.write_text(t, encoding="utf-8")
        from .connectors import ssh
        ssh.protect_key(str(p))
    except Exception:                                   # not writable: the token lives for this run only, as before
        pass
    return t



# ---- one OpenMove per data folder ------------------------------------------------------------------------------
LOCK_FILE = "openmove.lock"


class AlreadyRunning(Exception):
    """Another OpenMove holds this data folder."""


def acquire_lock(home):
    """Take the lock of the data folder for as long as this process lives (the system lets it go when the process ends, however it ends, so a crash never
    leaves a lock behind). Two programs on one journal would each move the same volumes. Returns the open file: keep it."""
    Path(home).mkdir(parents=True, exist_ok=True)
    f = open(Path(home) / LOCK_FILE, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        pid = ""
        try:
            pid = (Path(home) / "openmove.pid").read_text(encoding="utf-8").strip()
        except OSError:
            pass
        raise AlreadyRunning("OpenMove is already running with this data folder%s. Use that one (its address was printed in its window), or close it first." % (" (process %s)" % pid if pid else "")) from None
    try:
        (Path(home) / "openmove.pid").write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass
    return f


# ---- the saved context -----------------------------------------------------------------------------------------
def _path(state):
    return Path(state.home) / RESUME_FILE


def load(home):
    try:
        d = json.loads((Path(home) / RESUME_FILE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _iso(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def capture(state):
    """What must be put back after a restart, as plain data. None when there is nothing worth saving (not connected, demonstration)."""
    s = state.session
    if state.demo or s is None or state.inventory is None:
        return None
    from . import selection, wizard
    scan = getattr(state, "scan_args", None) or {}
    e = state.engine
    return {
        "saved_at": _iso(), "beat": _iso(),
        "connection": {"auth_url": s.auth_url, "username": s.username, "project_name": s.project_name, "user_domain": s.user_domain,
                       "project_domain": s.project_domain, "region": s.region, "interface": s.interface, "verify": s.verify},
        "lab": bool(state.lab), "access": state.access,
        "scan": scan, "checks": state.checks,
        "selection": None if state.selection is None else sorted(state.selection),
        "options": state.options, "released": sorted(state.released),
        "selection_confirmed": bool(state.checks) and wizard.selection_confirmed(state),
        "type_map": dict(e.type_map) if e else {},
        "dry_ok": ({"run_id": e.dry_ok["run_id"], "ids": sorted(e.dry_ok["ids"])} if e and getattr(e, "dry_ok", None) else None),
    }


def save(state, force=False):
    """Write the context to resume.json (whole file, then renamed over the old one, so a stop in the middle leaves the old file).
    Does nothing while the program waits for the sign-in after a restart: the saved file must not be replaced by an empty state."""
    rec = getattr(state, "recovery", None) or {}
    if rec.get("status") in ("waiting", "restoring", "failed"):
        return False
    with state.lock:
        d = capture(state)
    if d is None:
        return False
    enc = lambda x: json.dumps(x, sort_keys=True, default=lambda o: sorted(o) if isinstance(o, (set, frozenset)) else str(o))
    core = enc({k: v for k, v in d.items() if k not in ("beat", "saved_at")})
    now = time.time()
    if not force and core == getattr(state, "_resume_core", None) and now - getattr(state, "_resume_at", 0) < BEAT_EVERY:
        return False                                     # nothing changed since the last save and it was not long ago
    body = enc(d)
    p = _path(state)
    tmp = p.with_suffix(".tmp")
    try:
        tmp.write_text(body, encoding="utf-8")
        os.replace(str(tmp), str(p))
    except OSError as exc:
        if state.diag:
            state.diag.exception("saving the resume file", exc)
        return False
    state._resume_core, state._resume_at = core, now
    return True


def forget(state):
    try:
        _path(state).unlink()
    except OSError:
        pass
    state._resume_core = None


# ---- what the last run left behind -----------------------------------------------------------------------------
def engine_should_resume(journal):
    """True when the last run of the engine was started by a person and did not end by their stop or by finishing: the program or the computer stopped under it."""
    run = journal.latest("engine_run")
    if not run:
        return False
    end = journal.latest("engine_end")
    if end and end["id"] > run["id"]:
        return (end["data"] or {}).get("reason") in ("shutdown", "error")
    return True


def begin(state):
    """At start: find out whether the last run of the program ended cleanly, keep the saved context, and note this start in the journal."""
    j = state.journal
    last_start, last_stop = j.latest("server_start"), j.latest("server_stop")
    unclean = bool(last_start) and (not last_stop or last_stop["id"] < last_start["id"])
    saved = load(state.home)
    if saved and saved.get("saved_at"):
        try:
            age = (time.time() - time.mktime(time.strptime(saved["saved_at"], "%Y-%m-%dT%H:%M:%SZ")) + time.timezone) / 3600.0
            if age > MAX_AGE_HOURS:
                saved = None
        except ValueError:
            pass
    resume = engine_should_resume(j)
    j.record("server_start", "", {"pid": os.getpid(), "after_unclean_stop": unclean, "saved_context": bool(saved), "engine_was_running": resume,
                                  "supervised": bool(os.environ.get("OPENMOVE_SUPERVISED"))})
    state.recovery = {"status": "none", "supervised": bool(os.environ.get("OPENMOVE_SUPERVISED")), "unclean": unclean, "engine_was_running": resume,
                      "down_since": (saved or {}).get("beat"), "message": "", "connection": None, "resumed": 0}
    if saved and saved.get("scan") and (saved.get("checks") is not None or unclean or resume):
        state.recovery.update(status="waiting", connection=saved.get("connection"), lab=bool(saved.get("lab")),
                              message=_waiting_text(state, saved, unclean, resume))
    elif unclean or resume:
        state.recovery.update(status="info", message="OpenMove was stopped without closing. Nothing was lost: every step is in the history. "
                                                     "%s" % ("A move was running; read the volumes again and start it from step 5 to carry on." if resume else ""))
    return state.recovery


def _waiting_text(state, saved, unclean, resume):
    open_n = len(_open_volumes(state))
    what = "OpenMove stopped" if unclean else "OpenMove was closed"
    bits = ["%s while the work was open." % what]
    if resume:
        bits.append("A move was running%s." % (" and %d volume%s partly moved" % (open_n, "" if open_n == 1 else "s") if open_n else ""))
    bits.append("Nothing was lost: every step is saved. Sign in to the cloud again and OpenMove puts everything back and carries on by itself.")
    return " ".join(bits)


def _open_staged(state):
    try:
        from . import lifecycle as L
        return [v for v in _open_volumes(state) if L.method_of(L.history(state.journal, v)) == L.STAGED]
    except Exception:
        return []


def _open_volumes(state):
    try:
        from . import lifecycle as L
        return [v for v, e in L.states(state.journal).items() if e["phase"] not in L.TERMINAL]
    except Exception:
        return []


def finish(state, clean=True):
    """At a normal stop: save the context once more and say so in the journal, so the next start knows this stop was meant."""
    try:
        save(state, force=True)
    except Exception:
        pass
    if clean:
        state.journal.record("server_stop", "", {"pid": os.getpid()})


# ---- put it back -----------------------------------------------------------------------------------------------
def same_cloud(state, saved):
    c, s = (saved or {}).get("connection") or {}, state.session
    return bool(s) and (c.get("auth_url") or "").rstrip("/") == (s.auth_url or "").rstrip("/") and c.get("project_name") == s.project_name


def start_restore(state, auto_resume=True):
    """Put the saved context back, in the background, once a cloud session exists. Returns True when a restore was started."""
    rec = getattr(state, "recovery", None) or {}
    if rec.get("status") not in ("waiting", "failed") or state.session is None:
        return False
    saved = load(state.home)
    if not saved or not same_cloud(state, saved):
        if saved:
            rec["message"] = ("You signed in to a different cloud or project than the one saved. The saved work is kept. "
                              "Sign in again to the same one, or choose Start fresh.")
        return False
    rec.update(status="restoring", message="Getting back to where you were. OpenMove is reading the cloud again.")
    t = threading.Thread(target=_restore, args=(state, saved, auto_resume), name="openmove-recovery", daemon=True)
    t.start()
    return True


def _restore(state, saved, auto_resume):
    from . import server as S
    from . import selection, wizard
    rec = state.recovery
    try:
        sc = saved.get("scan") or {}
        S.api_discover(state, {}, {})
        S.api_inventory_run(state, {"backend": sc.get("backend"), "window_days": sc.get("window_days") or 14}, {})
        S.api_audit_run(state, {"backend": sc.get("backend")}, {})
        with state.lock:
            state.checks = saved.get("checks")
            state.access = saved.get("access") or state.access
            state.selection = None if saved.get("selection") is None else set(saved["selection"])
            state.options = saved.get("options")
            state.released = set(saved.get("released") or [])
        if saved.get("selection_confirmed") and state.checks:
            wizard.confirm_selection(state)
        held = ""
        if state.checks and (state.access == "full" or _open_staged(state)):
            try:                                          # the migration host and the Cinder host: read-only readiness checks over SSH; class C volumes in flight need them
                from . import server_hosts
                hc = server_hosts.refresh_fabric(state)
                if not hc.get("ready"):
                    held = "The migration host is not ready (%s), so volumes moved with the careful copy wait until it is." % (hc.get("why") or "not checked")
            except Exception as exc:
                held = "The migration host could not be checked (%s), so volumes moved with the careful copy wait until it is." % exc
        resumed = 0
        if auto_resume and engine_should_resume(state.journal) and state.checks:
            e = S._engine(state)
            e.type_map = dict(saved.get("type_map") or {})
            dk = saved.get("dry_ok")
            if dk and dk.get("run_id") == state.checks.get("run_id"):
                e.dry_ok = {"run_id": dk["run_id"], "ids": set(dk.get("ids") or [])}
            try:
                S._refusals(lambda: e.start(None, None))
                resumed = len(e.active) + len(e.queue)
                state.journal.record("engine_resume", "batch", {"by": "OpenMove (automatic, after a restart)", "volumes": resumed})
            except S.ApiError as exc:                  # the permit said no (a halt, a stale pre-flight): the person decides
                rec["resume_refused"] = str(exc)
        text = ["Back where you were."]
        if resumed:
            text.append("The move carried on by itself with %d volume%s." % (resumed, "" if resumed == 1 else "s"))
        elif rec.get("resume_refused"):
            text.append("The move was not carried on: %s" % rec["resume_refused"])
        if held:
            text.append(held)
        rec["held"] = held
        rec.update(status="done", resumed=resumed, message=" ".join(text))
        state.journal.record("recovery", "restore", {"ok": True, "resumed": resumed, "refused": rec.get("resume_refused", "")})
    except Exception as exc:                           # say what failed; the saved file stays, so the person can try again
        rec.update(status="failed", message="OpenMove could not put everything back: %s. Your work is still saved. Try again, or start fresh." % exc)
        if state.diag:
            state.diag.exception("restoring after a restart", exc)
        state.journal.record("recovery", "restore", {"ok": False, "error": str(exc)[:300]})


def discard(state):
    """The person chose to start fresh: forget the saved context. The journal (every step taken) is not touched."""
    forget(state)
    state.recovery = dict(getattr(state, "recovery", None) or {}, status="none", message="", connection=None)
    state.journal.record("recovery", "discard", {"note": "saved context forgotten by the operator"})


# ---- the guardian ----------------------------------------------------------------------------------------------
class Guardian(threading.Thread):
    """Runs beside the server: saves the context every few seconds, starts the engine's thread again if it ever died, and asks the computer
    not to go to sleep while a move is running. One look is one `look()`, so it can be tested without waiting."""

    def __init__(self, state, every=GUARD_EVERY):
        super().__init__(name="openmove-guardian", daemon=True)
        self.state, self.every, self._end, self.awake = state, every, threading.Event(), False
        self._last_save = 0.0

    def run(self):
        while not self._end.wait(self.every):
            try:
                self.look()
            except Exception as exc:                   # the guardian itself must never stop
                if self.state.diag:
                    self.state.diag.exception("the guardian", exc)
        self._awake(False)

    def stop(self):
        self._end.set()

    def look(self):
        st, e = self.state, self.state.engine
        running = bool(e and e.status == "running")
        if running:
            e.ensure_alive()
        self._awake(running)
        if time.time() - self._last_save >= BEAT_EVERY:
            self._last_save = time.time()
            save(st)

    def _awake(self, on):
        if on == self.awake:
            return
        self.awake = on
        keep_awake(on)


def keep_awake(on):
    """Windows: ask not to go to sleep by itself while a move runs (the screen may still turn off). Other systems: nothing is done here;
    docs/RECOVERY.md says how to do it there. Must be called from the same thread each time (the setting belongs to the thread)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
        return True
    except Exception:
        return False
