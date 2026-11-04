# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Start OpenMove:  python -m openmove [--demo] [--host H] [--port P] [--home DIR] [--no-browser]"""
import argparse
import os
import shutil
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from .server import create_server


def open_browser(url, say):
    """Open the interface in the default browser. Falls back to the system handler on Windows and says so if nothing worked."""
    def go():
        try:
            if webbrowser.open(url):
                return
        except Exception:
            pass
        try:
            if os.name == "nt":
                os.startfile(url)  # the Windows default-browser handler
                return
        except Exception:
            pass
        say("The browser could not be opened automatically. Copy the address above into your browser.")
    threading.Timer(0.4, go).start()


def deployment(a):
    """--self-check and --host-setup: no server, no browser."""
    from . import paths
    from . import selfcheck
    if a.lab:
        from . import lab as lab_mod
        home = lab_mod.home_dir()
    else:
        if a.home:
            os.environ["OPENMOVE_HOME"] = a.home
        home = paths.app_home()
    if a.host_setup:
        from . import server_hosts
        out = Path(a.out or ".") / "prepare_host.sh"
        out.write_text(server_hosts.setup_script(), encoding="utf-8", newline="\n")
        print("Wrote %s. Copy it to the host and run:" % out)
        print()
        print("  " + server_hosts.setup_command(home, a.host_setup))
        return 0
    rows = selfcheck.run(home)
    width = max(len(r["name"]) for r in rows)
    for r in rows:
        print("%-5s %-*s  %s" % (r["status"], width, r["name"], r["detail"]))
    bad = [r for r in rows if r["status"] == "FAIL"]
    print()
    print("%s: %d check(s), %d failed, %d warning(s)" % ("NOT READY" if bad else "Ready", len(rows), len(bad), sum(1 for r in rows if r["status"] == "WARN")))
    return 1 if bad else 0


def support(a):
    """--bundle and --decode: no server, no browser. They work on the data folder of a run, also when the program is stopped."""
    from . import diag as diag_mod
    from . import paths
    if a.lab:
        from . import lab as lab_mod
        home = lab_mod.home_dir()
    else:
        if a.home:
            os.environ["OPENMOVE_HOME"] = a.home
        home = paths.app_home()
    if a.decode:
        value = diag_mod.decode(home, a.decode)
        if value is None:
            print("This data folder has no token %r." % a.decode, file=sys.stderr)
            return 1
        print(value)
        return 0
    from . import hosts as hosts_mod
    from .connectors import ssh
    from .journal import Journal
    d = diag_mod.Diag(home)
    d.attach()
    j = Journal(Path(home) / "journal.db")
    try:
        hs = [] if a.no_remote else [ssh.Host(h, home) for h in hosts_mod.load(home)]
        res = d.bundle(j, extras={"hosts": {"registry": hosts_mod.view(home)}}, level=a.redact, remote=not a.no_remote, reason="requested on the command line", hosts=hs,
                       out_dir=a.out)
    finally:
        j.close()
    print("Support bundle written: %s" % res["path"])
    print("Redaction: %s. Files: %d. Size: %d bytes." % (res["redaction"], res["files"], res["bytes"]))
    if res["redaction"] != "none":
        print("The map from token to the real value stays in %s; it is not in the bundle." % (Path(home) / "diag" / "redaction-map.json"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="openmove", description="OpenMove %s" % __version__)
    ap.add_argument("--demo", action="store_true",
                    help="demonstration mode: a built-in cloud with made-up data, a temporary data folder")
    ap.add_argument("--lab", action="store_true",
                    help="lab mode: connect to the OpenStack lab on this machine (see tools/lab) and use the real engine")
    ap.add_argument("--lab-reset", action="store_true", help="with --lab: forget the lab's journal and evidence first")
    ap.add_argument("--scenario", default=None, help="demonstration scenario (with --demo); see --list-scenarios")
    ap.add_argument("--list-scenarios", action="store_true", help="list the demonstration scenarios and exit")
    ap.add_argument("--host", default="127.0.0.1", help="address to listen on (default: this machine only)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--home", help="folder for the journal and snapshots (default: the user data folder)")
    ap.add_argument("--allow-host", action="append", default=[],
                    help="extra Host header value to accept, for example the name used behind a container port mapping")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--supervise", action="store_true",
                    help="run OpenMove under a supervisor that starts it again if it stops or stops answering; a move that was running is carried on (docs/RECOVERY.md)")
    ap.add_argument("--bundle", action="store_true", help="write a support bundle (everything needed to find a fault) and exit; add --redact to hide customer data")
    ap.add_argument("--redact", choices=("none", "standard", "strict"), default=None, help="with --bundle: how much of the data to replace with tokens (default: the setting, usually standard)")
    ap.add_argument("--no-remote", action="store_true", help="with --bundle: do not collect from the registered hosts")
    ap.add_argument("--out", help="with --bundle: the folder to write the bundle to (default: bundles in the data folder)")
    ap.add_argument("--host-setup", metavar="ROLE", choices=("mover", "cinder", "both"),
                    help="print the command that prepares a host for a role (mover, cinder or both) with OpenMove's key filled in, and write prepare_host.sh next to it; no server is started")
    ap.add_argument("--self-check", action="store_true", help="check that this installation holds everything class A, B and C need (program files, data mover, Cinder helper, SSH client, data folder) and exit")
    ap.add_argument("--askpass", action="store_true", help=argparse.SUPPRESS)       # called by ssh during a host setup: prints the password OpenMove put in its environment
    ap.add_argument("--decode", metavar="TOKEN", help="say which value a redaction token stands for (uses this site's token map) and exit")
    a = ap.parse_args(argv)
    if a.askpass:
        from .connectors import ssh
        sys.stdout.write(os.environ.get(ssh.PASSWORD_ENV, "") + "\n")
        return 0
    if a.supervise and not os.environ.get("OPENMOVE_SUPERVISED") and not (a.bundle or a.decode or a.self_check or a.host_setup or a.list_scenarios or a.askpass):
        from . import supervisor
        return supervisor.run(list(sys.argv[1:] if argv is None else argv))
    if a.bundle or a.decode:
        return support(a)
    if a.self_check or a.host_setup:
        return deployment(a)
    if a.list_scenarios:
        from .demo import scenarios
        for s in scenarios.listing():
            print("%-12s %s" % (s["id"], s["title"]))
            print("%-12s %s" % ("", s["summary"]))
        return 0
    home, temp_home = None, None
    if a.demo:  # demonstration mode always uses a throw-away data folder, so it never touches a real journal
        if a.home:
            print("Note: --home is ignored in demonstration mode; a temporary folder is used.", flush=True)
        temp_home = tempfile.mkdtemp(prefix="openmove-demo-")
        home = Path(temp_home)
    elif a.lab:
        from . import lab as lab_mod
        home = lab_mod.home_dir()
        if a.lab_reset and home.exists():
            shutil.rmtree(home, ignore_errors=True)
        home.mkdir(parents=True, exist_ok=True)
    elif a.home:
        os.environ["OPENMOVE_HOME"] = a.home
    from . import paths as paths_mod
    from . import recovery
    lock_home = home or (Path(a.home) if a.home else paths_mod.app_home())
    try:
        lock = recovery.acquire_lock(lock_home)                 # one OpenMove per data folder; kept until the process ends
    except recovery.AlreadyRunning as e:
        print(str(e), file=sys.stderr)
        return 2                                                # not started again by the supervisor
    try:
        server, state = create_server(a.host, a.port, home)
    except OSError as e:
        print("OpenMove could not listen on %s:%d (%s). Is another OpenMove already running on that port? Choose another with --port." % (a.host, a.port, e), file=sys.stderr)
        return 2
    state.instance_lock = lock
    state.extra_hosts = tuple(h.lower() for h in a.allow_host)
    if a.demo:
        from . import demo
        try:
            demo.start(state, scenario=a.scenario or demo.DEFAULT)
        except ValueError as e:
            server.server_close()
            state.journal.close()
            shutil.rmtree(temp_home, ignore_errors=True)
            print(str(e), file=sys.stderr)
            return 2
    if a.lab:
        try:
            lab_mod.connect(state)
        except lab_mod.LabError as e:
            server.server_close()
            state.journal.close()
            print(str(e), file=sys.stderr)
            return 2
        try:                                        # the migration host and the Cinder host of the lab, set up without a terminal
            from .lab import hosts as lab_hosts
            made = lab_hosts.prepare(state)
            print("Lab hosts: %s" % ("registered at %s; %s" % (made["address"], made["why"]) if made["ready"] is None else "ready; class C can be moved" if made["ready"] else "registered, not ready: %s" % made["why"]), flush=True)
        except Exception as e:                      # class C stays held back; the reason is in the log and the bundle
            state.diag.exception("preparing the lab hosts", e)
            print("Lab hosts: could not be prepared, class C stays held back (%s). Details are in the log." % e, flush=True)
    if a.lab:                                       # the lab signs in by itself, so the work saved before a stop is put back at once
        if recovery.start_restore(state):
            print("Putting back the work saved before the last stop ...", flush=True)
    guardian = recovery.Guardian(state)
    guardian.start()
    port = server.server_address[1]
    shown = "127.0.0.1" if a.host in ("0.0.0.0", "::") else a.host
    url = "http://%s:%d/?token=%s" % (shown, port, state.token)
    say = lambda text: print(text, flush=True)
    say("OpenMove %s%s" % (__version__, "  [DEMONSTRATION MODE: synthetic data, no real cloud]" if a.demo else "  [LAB MODE: the OpenStack lab on this machine, not a customer cloud]" if a.lab else ""))
    say("Data folder: %s" % state.home)
    say("Open: %s" % url)
    say("Stop with Ctrl+C or by closing this window.")
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        say("Warning: listening on %s. Anyone who can reach this port and has the token can use OpenMove." % a.host)
    if not a.no_browser:
        open_browser(url, say)
        if a.lab:                                   # lab mode opens the OpenStack dashboard too, in a second window
            cfg = lab_mod.load_config()
            if lab_mod.horizon_answers(cfg):
                say("OpenStack dashboard: %s   (signed in automatically as %s)" % (lab_mod.horizon_url(cfg), cfg.get("user", "admin")))
                open_browser("http://%s:%d/lab/dashboard?token=%s" % (urlsplit(cfg["auth_url"]).hostname or shown, port, state.token), say)
            else:
                say("The OpenStack dashboard (Horizon) does not answer at %s; it may still be starting, or not installed." % lab_mod.horizon_url(cfg))
    clean = False
    try:
        server.serve_forever()
        clean = True
    except KeyboardInterrupt:
        clean = True
    finally:
        guardian.stop()
        server.server_close()
        recovery.finish(state, clean)               # the context is saved once more; a normal stop is written in the journal
        state.disconnect("shutdown")                # a move that was running is carried on at the next start
        state.journal.close()
        if a.demo:
            demo.stop(state)
        if temp_home:
            shutil.rmtree(temp_home, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
