# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Proof of the staged method (class C) against the OpenMove lab (docs/LAB.md, docs/MOVER.md).

It drives OpenMove's own handlers, the same ones the screen calls, against the real lab cloud and the two lab hosts, signs the gates as a
made-up second person (lab only), and reads the result back. It refuses any volume that is not named lab-*, and any cloud but the lab.

    python tools/lab/lab_staged.py status                 what each lab volume is, and the hosts' readiness
    python tools/lab/lab_staged.py dry NAME [NAME ...]    the dry run
    python tools/lab/lab_staged.py run NAME [NAME ...]    move them; sign every gate; wait for the soak
    python tools/lab/lab_staged.py fill NAME [MiB]        write random data into a lab volume (its snapshots keep the older content)
    python tools/lab/lab_staged.py snapshot NAME [COUNT]  make snapshots of a lab volume (forced when it is attached)
    python tools/lab/lab_staged.py run NAME --resume      carry on what the journal holds (after a crash or a stop)
    python tools/lab/lab_staged.py reverse NAME           put a moved volume back (R3), then carry on to the soak
    add --home=NAME to any of them to use a separate journal (a volume that was rolled back is finished in its journal: start it in a new one)
"""
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from openmove import lab as lab_mod  # noqa: E402
from openmove import server as S  # noqa: E402
from openmove.lab import hosts as lab_hosts  # noqa: E402

HOME = lab_mod.lab_dir() / "run-home"
for _a in sys.argv:                                             # --home NAME: a separate journal, so one volume's state does not hide another's
    if _a.startswith("--home="):
        HOME = lab_mod.lab_dir() / ("run-home-" + _a.split("=", 1)[1])
SRC, TGT = lab_mod.SOURCE_BACKEND, lab_mod.TARGET_BACKEND
BY, SECOND = "Olive Operator", "Sam Second"


def open_state(fresh=False):
    if fresh:
        shutil.rmtree(HOME, ignore_errors=True)
    HOME.mkdir(parents=True, exist_ok=True)
    srv, state = S.create_server("127.0.0.1", 0, HOME)
    lab_mod.connect(state)
    return srv, state


def prepare(state):
    res = lab_hosts.prepare(state)
    print("hosts:", "; ".join(res["done"]), "at", res["address"])
    plat = S.api_discover(state, {}, {})
    print("release:", plat["release"], "| profile:", plat["profile"]["status"])
    S.api_inventory_run(state, {}, {})
    inv = S._need(state, "inventory")
    state.names = {v["id"]: v["name"] for v in inv["volumes"]}
    res = S.api_checks_run(state, {"source_backend": SRC, "target_backend": TGT}, {})
    hc = state.host_checks or {}
    print("access:", state.access, "| fabric ready:", hc.get("ready"), hc.get("why") or "")
    for c in hc.get("checks", []):
        print("  %-3s %-5s %-34s %s" % (c["id"], c["status"], c["title"], c["detail"][:110]))
    S.api_lab_approve(state, {}, {})
    print("gate:", S._gate(state)["status"], "| counts:", res["counts"])
    return inv


def ids_of(state, names):
    rev = {n: i for i, n in state.names.items()}
    missing = [n for n in names if n not in rev]
    if missing or any(not n.startswith("lab-") for n in names):
        sys.exit("not lab volumes, or not found: %s" % ", ".join(missing or names))
    return [rev[n] for n in names]


def show(state, last):
    view = S.api_engine_get(state, {}, {})
    for row in view["volumes"]:
        key = (row.get("phase"), row.get("note"), row.get("error"))
        if last.get(row["id"]) != key:
            last[row["id"]] = key
            print("[%s] %-18s %-9s %s%s" % (time.strftime("%H:%M:%S"), state.names.get(row["id"]), row.get("phase"), (row.get("note") or "")[:160],
                                           ("  ERROR: " + row["error"]) if row.get("error") else ""), flush=True)
    return view


def run(state, names, resume=False, limit=2400):
    ids = ids_of(state, names)
    if resume:
        print("resuming what the journal holds (no dry run: the volume is already in the engine)")
    else:
        dry = S.api_engine_dry_run(state, {"ids": ids, "type_map": {"*": TGT}}, {})
        print("dry run:", dry["counts"])
        for r in dry.get("volumes") or dry.get("rows", []):
            print("  %-18s class %s %s" % (state.names.get(r["id"]), r["class"], r["verdict"]))
            for c in r["checks"]:
                if not c["ok"]:
                    print("     x", c["name"], "-", c["detail"])
    v = S.api_engine_start(state, {"by": BY, "ids": ids, "type_map": {"*": TGT}}, {})
    print("started; queued", v.get("queued"))
    last, signed, released, end = {}, set(), False, time.time() + limit
    while time.time() < end:
        view = show(state, last)
        for row in view["volumes"]:
            ph, vid = row.get("phase"), row["id"]
            if ph in ("validate", "sync", "recheck") and ("G23", vid) not in signed:
                ok = True
                for g in ("G2", "G3"):
                    try:
                        S.api_volume_gate(state, {"gate": g, "ids": [vid], "by": SECOND, "evidence": "lab: checked the before-state, the copy and the target"}, {})
                    except Exception as exc:
                        ok = False
                        if (g, vid) not in signed:
                            print("   sign %s not yet: %s" % (g, str(exc)[:120]))
                            signed.add((g, vid))
                if ok:
                    signed.add(("G23", vid))
            if ph == "postcheck" and ("G4", vid) not in signed:
                try:
                    S.api_volume_gate(state, {"gate": "G4", "ids": [vid], "by": SECOND, "evidence": "lab: verified from the new path"}, {})
                    signed.add(("G4", vid))
                except Exception as exc:
                    print("   sign G4 refused:", str(exc)[:120])
        if not released and any(r.get("phase") in ("sync", "recheck", "validate") and ("G23", r["id"]) in signed for r in view["volumes"]):
            try:
                S.api_release(state, {"by": BY, "ids": ids}, {})
                released = True
                print("   released for the cutover")
            except Exception as exc:
                print("   release not yet:", str(exc)[:140])
        if view["volumes"] and all(r.get("phase") in ("soak", "done", "aborted") for r in view["volumes"]):
            break
        time.sleep(4)
    print("final:", {state.names.get(r["id"]): r.get("phase") for r in S.api_engine_get(state, {}, {})["volumes"]})
    S.api_engine_stop(state, {}, {})


def lab_cloud():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import seed
    return seed.Cloud(lab_mod.load_config())


def lab_volume(c, name):
    if not name.startswith("lab-"):
        sys.exit("only lab-* volumes")
    vols = [v for v in c.call("GET", "volumev3", "/volumes/detail?all_tenants=1")["volumes"] if v["name"] == name and v["status"] not in ("deleting", "error_deleting")]
    if len(vols) != 1:
        sys.exit("%d volumes named %s" % (len(vols), name))
    return vols[0]


def fill(name, mib=48):
    c = lab_cloud()
    v = lab_volume(c, name)
    nid = v.get("os-vol-mig-status-attr:name_id") or v["id"]
    be = v["os-vol-host-attr:host"].split("#")[0].split("@")[-1]
    dev = "/dev/stack-volumes-%s/volume-%s" % (be, nid)
    script = "\n".join(["set -e", "sz=$(blockdev --getsize64 %s)" % dev, "mib=$((sz/1048576))", "n=%d" % mib, "[ $n -gt $mib ] && n=$mib",
                        "off=$(( RANDOM %% (mib - n + 1) ))".replace("%%", "%"),
                        "dd if=/dev/urandom of=%s bs=1M count=$n seek=$off conv=notrunc status=none" % dev, "sync", "sha256sum %s" % dev, ""])
    rc, out, err = lab_hosts.run(lab_mod.load_config().get("wsl_distro") or "openmove-lab", script, timeout=300)
    print(out.strip() or err.strip())


def snapshot(name, count=1):
    c = lab_cloud()
    v = lab_volume(c, name)
    for i in range(count):
        snap = c.call("POST", "volumev3", "/snapshots", {"snapshot": {"volume_id": v["id"], "force": True, "name": "lab-snap-%s-%d" % (name, int(time.time()) + i)}})["snapshot"]
        c.wait("snapshot " + snap["id"], lambda s=snap["id"]: c.call("GET", "volumev3", "/snapshots/" + s)["snapshot"]["status"], ("available",))
        print("snapshot", snap["id"])


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    names = [n for n in argv[2:] if not n.startswith("--")]
    if cmd == "reverse":
        argv = argv + ["--keep", "--resume"]
    if cmd == "fill":
        return fill(names[0], int(names[1]) if len(names) > 1 else 48)
    if cmd == "snapshot":
        return snapshot(names[0], int(names[1]) if len(names) > 1 else 1)
    srv, state = open_state(fresh=(cmd in ("dry", "run") and "--keep" not in argv and "--resume" not in argv))
    try:
        inv = prepare(state)
        if cmd == "status":
            for v in sorted(inv["volumes"], key=lambda x: x["name"]):
                print("  %-20s class %s %-9s %s snaps %s" % (v["name"], v.get("class"), v.get("status"), v.get("backend"), v.get("snapshots")))
        elif cmd == "dry":
            r = S.api_engine_dry_run(state, {"ids": ids_of(state, names), "type_map": {"*": TGT}}, {})
            print(json.dumps(r, indent=1, default=str)[:6000])
        elif cmd == "run":
            run(state, names, resume="--resume" in argv)
        elif cmd == "reverse":
            S.api_engine_reverse(state, {"id": ids_of(state, names)[0], "by": BY, "reason": "lab: proof of the way back (R3) for a class C volume"}, {})
            run(state, names, resume=True)
        else:
            sys.exit("unknown command %s" % cmd)
    finally:
        state.journal.close()
        srv.server_close()


if __name__ == "__main__":
    main(sys.argv)
