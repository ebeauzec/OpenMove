# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The supervisor: starts OpenMove as a child process and starts it again if it stops by itself or stops answering (docs/RECOVERY.md).

    python -m openmove --supervise [the usual options]

The child keeps its journal, its saved context (openmove/recovery.py) and its access address across a restart, so for the person at the
screen a restart is a short wait: the page shows "OpenMove is starting again", then carries on. A move that was running is carried on by the
new process as soon as the cloud answers the sign-in (the lab signs in by itself).

When the supervisor does NOT start the child again:
  * the child ended normally (exit code 0: the person closed it), or refused to start because of a setting (exit code 2);
  * it stopped too often, too fast (MAX_RAPID stops, each within RAPID_SECONDS of its start): something is wrong that a restart will not fix.
    It says so and leaves everything as it is. The journal is safe; a support bundle can be made from the data folder with  python -m openmove --bundle.
"""
import os
import subprocess
import sys
import time
import urllib.request

CHILD_ENV = "OPENMOVE_SUPERVISED"
RAPID_SECONDS = 120               # a child that ran less than this and stopped counts as a quick stop
MAX_RAPID = 8                     # quick stops in a row before the supervisor gives up
PAUSES = (1, 2, 5, 10, 20, 30)    # seconds to wait before starting again, growing with the quick stops in a row
HEALTH_EVERY = 20                 # seconds between two health looks
HEALTH_FAILS = 4                  # looks that fail in a row before the child is stopped and started again (about 80 s without an answer)
NO_RESTART = (0, 2)               # normal end, and refusal to start because of a setting


def child_command(args, first):
    """The command of the child: this program with the same options, without --supervise, and without opening the browser again."""
    base = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "openmove"]
    out = [a for a in args if a != "--supervise"]
    if not first and "--no-browser" not in out:
        out.append("--no-browser")
    return base + out


def port_of(args):
    port, host = 8765, "127.0.0.1"
    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args):
            try:
                port = int(args[i + 1])
            except ValueError:
                pass
        elif a.startswith("--port="):
            try:
                port = int(a.split("=", 1)[1])
            except ValueError:
                pass
        elif a == "--host" and i + 1 < len(args):
            host = args[i + 1]
    return ("127.0.0.1" if host in ("0.0.0.0", "::", "localhost") else host), port


def healthy(host, port, timeout=5):
    try:
        with urllib.request.urlopen("http://%s:%d/healthz" % (host, port), timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def run(args, say=lambda t: print(t, flush=True), popen=subprocess.Popen, sleep=time.sleep, clock=time.time, check=healthy, health_every=HEALTH_EVERY):
    """Run the child until it ends normally. Returns the exit code of the supervisor."""
    host, port = port_of(args)
    env = dict(os.environ, **{CHILD_ENV: "1"})
    first, quick, starts = True, 0, 0
    while True:
        cmd = child_command(args, first)
        started, proc = clock(), popen(cmd, env=env)
        starts += 1
        fails, why, next_look = 0, None, started + 30         # the child gets 30 s to start before the first health look
        try:
            while proc.poll() is None:
                sleep(1)
                t = clock()
                if t < next_look:
                    continue
                next_look = t + health_every
                fails = 0 if check(host, port) else fails + 1
                if fails >= HEALTH_FAILS:
                    why = "it stopped answering for about %d seconds" % (HEALTH_FAILS * health_every)
                    proc.terminate()
                    try:
                        proc.wait(timeout=15)
                    except Exception:
                        proc.kill()
                    break
        except KeyboardInterrupt:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except Exception:
                proc.kill()
            return 0
        code = proc.poll() if proc.poll() is not None else -1
        ran = clock() - started
        if code in NO_RESTART and why is None:
            return code
        quick = quick + 1 if ran < RAPID_SECONDS else 0
        reason = why or "it stopped unexpectedly (exit code %s)" % code
        if quick >= MAX_RAPID:
            say("OpenMove stopped %d times in a row within %d seconds of starting (last: %s). It will not be started again by itself." % (quick, RAPID_SECONDS, reason))
            say("Nothing is lost: the journal and the saved work are in the data folder. Run  python -m openmove --bundle  to collect what is needed to find the fault, then start OpenMove again.")
            return 1
        pause = PAUSES[min(quick, len(PAUSES)) - 1] if quick else PAUSES[0]
        say("OpenMove stopped after %d s: %s. Starting it again in %d s. Your work is saved; the page in the browser carries on by itself." % (ran, reason, pause))
        sleep(pause)
        first = False
