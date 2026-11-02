# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Start OpenMove:  python -m openmove [--host H] [--port P] [--home DIR] [--no-browser]"""
import argparse
import os
import sys
import webbrowser

from . import __version__
from .server import create_server


def main(argv=None):
    ap = argparse.ArgumentParser(prog="openmove", description="OpenMove %s" % __version__)
    ap.add_argument("--host", default="127.0.0.1", help="address to listen on (default: this machine only)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--home", help="folder for the journal and snapshots (default: the user data folder)")
    ap.add_argument("--allow-host", action="append", default=[],
                    help="extra Host header value to accept, for example the name used behind a container port mapping")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    if a.home:
        os.environ["OPENMOVE_HOME"] = a.home
    server, state = create_server(a.host, a.port)
    state.extra_hosts = tuple(h.lower() for h in a.allow_host)
    port = server.server_address[1]
    shown = "127.0.0.1" if a.host in ("0.0.0.0", "::") else a.host
    url = "http://%s:%d/?token=%s" % (shown, port, state.token)
    print("OpenMove %s" % __version__)
    print("Data folder: %s" % state.home)
    print("Open: %s" % url)
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print("Warning: listening on %s. Anyone who can reach this port and has the token can use OpenMove." % a.host)
    if not a.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        state.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
