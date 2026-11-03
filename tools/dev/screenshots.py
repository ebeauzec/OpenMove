# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Take the screenshots used in docs/ (docs/images/*.png) from the real interface, in demonstration mode, so no real data is ever shown.

It starts OpenMove in process with the built-in demonstration cloud, brings it to each state of the wizard by calling the same handlers the
page calls, and renders the real page with a headless Edge or Chrome (a link such as /#preflight opens that step). Nothing is drawn by hand.

    python tools/dev/screenshots.py            # all of them
    python tools/dev/screenshots.py preflight  # the ones whose name contains the word

A browser is needed (Edge ships with Windows). With --lab the Move step is also taken from the OpenStack lab on this machine, if it is running.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from openmove import demo, server as S  # noqa: E402
from openmove.demo.simulate import Simulation  # noqa: E402

OUT = ROOT / "docs" / "images"
WIDTH = 1200
BROWSERS = (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe", "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def browser():
    for b in BROWSERS:
        if Path(b).exists():
            return b
    found = shutil.which("msedge") or shutil.which("chrome") or shutil.which("chromium")
    if not found:
        raise SystemExit("no Edge or Chrome found: install one, or set it in BROWSERS")
    return found


def shot(base, token, name, tab, height, only):
    if only and not any(o in name for o in only):
        return
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / (name + ".png")
    prof = tempfile.mkdtemp(prefix="om-shot-")
    try:
        cmd = [browser(), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--user-data-dir=" + prof,
               "--force-device-scale-factor=1", "--window-size=%d,%d" % (WIDTH, height), "--virtual-time-budget=9000",
               "--screenshot=" + str(target), "%s/?token=%s#%s" % (base, token, tab)]
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    finally:
        shutil.rmtree(prof, ignore_errors=True)
    print("  %-34s %6d bytes" % (target.name, target.stat().st_size), flush=True)


def lab_move(only, volumes=("lab-a-detached-8", "lab-a-detached-4")):
    """The Move step with the real engine, against the OpenStack lab on this machine (docs/LAB.md). Moves two small volumes to the target back-end."""
    from openmove import lab as lab_mod
    home = Path(tempfile.mkdtemp(prefix="om-shots-lab-"))
    srv, state = S.create_server("127.0.0.1", 0, home)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    try:
        lab_mod.connect(state)
        S.api_discover(state, {}, {})
        S.api_inventory_run(state, {}, {})
        S.api_audit_run(state, {}, {})
        S.api_checks_run(state, {"source_backend": "sf-sim", "target_backend": "pure-sim", "access": "api"}, {})
        S.api_lab_approve(state, {}, {})
        ids = [v["id"] for v in state.inventory["volumes"] if v.get("name") in volumes]
        S.api_selection_set(state, {"action": "set", "ids": ids, "by": "Olive Operator"}, {})
        S.api_selection_confirm(state, {"by": "Olive Operator"}, {})
        S.api_engine_dry_run(state, {"type_map": {"*": "pure-sim"}}, {})
        S.api_engine_start(state, {"by": "Olive Operator", "type_map": {"*": "pure-sim"}}, {})
        end = time.time() + 300
        while time.time() < end and not all(r.get("phase") == "validate" for r in S.api_engine_get(state, {}, {})["volumes"]) :
            time.sleep(2)
        shot(base, state.token, "13-move-waiting-second-person", "move", 900, only)
        S.api_volume_gate(state, {"gate": "G2", "ids": ids, "by": "Sam Second", "evidence": "checked the before-state and the target"}, {})
        S.api_volume_gate(state, {"gate": "G3", "ids": ids, "by": "Sam Second", "evidence": "checked the before-state and the target"}, {})
        S.api_release(state, {"by": "Olive Operator", "ids": ids}, {})
        end = time.time() + 600
        while time.time() < end and not all(r.get("phase") == "postcheck" for r in S.api_engine_get(state, {}, {})["volumes"]):
            time.sleep(3)
        shot(base, state.token, "14-move-postcheck", "move", 900, only)
        S.api_volume_gate(state, {"gate": "G4", "ids": ids, "by": "Sam Second", "evidence": "verified from the new path"}, {})
        time.sleep(8)
        shot(base, state.token, "15-move-soak", "move", 900, only)
    finally:
        try:
            S.api_engine_stop(state, {}, {})
        finally:
            srv.shutdown()
            srv.server_close()
            state.disconnect()
            state.journal.close()
            shutil.rmtree(home, ignore_errors=True)


def main(argv):
    only = [a for a in argv if not a.startswith("--")]
    home = Path(tempfile.mkdtemp(prefix="om-shots-home-"))
    srv, state = S.create_server("127.0.0.1", 0, home)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    demo.start(state, scenario="sf-to-pure")
    info = S._demo_info(state)
    token = state.token
    try:
        print("screenshots into", OUT)
        shot(base, token, "01-connect", "connect", 640, only)
        S.api_discover(state, {}, {})
        shot(base, token, "02-platform", "platform", 900, only)
        S.api_inventory_run(state, {"backend": info["source"]}, {})
        S.api_demo_dbexport(state, {}, {})
        shot(base, token, "03-inventory", "inventory", 1100, only)
        S.api_audit_run(state, {}, {})
        shot(base, token, "04-audit", "audit", 1100, only)
        S.api_checks_run(state, {"source_backend": info["source"], "target_backend": info["target"], "access": "full"}, {})
        shot(base, token, "05-preflight-open-gate", "preflight", 1500, only)
        S.api_demo_approve(state, {}, {})
        shot(base, token, "06-preflight-approved", "preflight", 1500, only)
        shot(base, token, "07-select", "select", 1500, only)
        S.api_selection_confirm(state, {"by": "Olive Operator"}, {})
        shot(base, token, "08-select-confirmed", "select", 900, only)
        shot(base, token, "09-move-demo", "move", 700, only)
        shot(base, token, "10-reports", "reports", 700, only)
        state.options = {"hold_before_cutover": False, "stop_after_failures": 0}
        state.auto_sign = "Demo reviewer (synthetic)"
        sim = Simulation(state, lambda: S._gate(state), tick=0)
        state.sim = sim
        sim.start()
        sim._thread.join(timeout=120)
        shot(base, token, "11-live", "live", 1500, only)
        shot(base, token, "12-journal", "journal", 1100, only)
        if "--lab" in argv:
            lab_move(only)
    finally:
        if state.sim:
            state.sim.stop()
        srv.shutdown()
        srv.server_close()
        state.disconnect()
        demo.stop(state)
        state.journal.close()
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1:])
