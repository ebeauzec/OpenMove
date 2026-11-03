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
    a = ap.parse_args(argv)
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
    server, state = create_server(a.host, a.port, home)
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
                open_browser("http://%s:%d/lab/dashboard?token=%s" % (shown, port, state.token), say)
            else:
                say("The OpenStack dashboard (Horizon) does not answer at %s; it may still be starting, or not installed." % lab_mod.horizon_url(cfg))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        state.disconnect()
        state.journal.close()
        if a.demo:
            demo.stop(state)
        if temp_home:
            shutil.rmtree(temp_home, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
