# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Phase 0 as guided procedures: the playbook's lab tests (T1 to T23) and the tests OpenMove adds (M1 to M4), with a recorded result for each.

OpenMove cannot run these tests: they need real hosts, arrays and people. It lists each test (what it proves, what passing looks like, how to
do it), records the result a named person gives with their evidence, and refuses to record gate G0 from the tests until every test the
retype path needs has a passed result for this release and none has a failed one. The playbook is the authority on the tests.
"""
import json
from importlib import resources

KIND = "phase0_test"
RESULTS = ("pass", "fail")
_DATA = None


def tests():
    global _DATA
    if _DATA is None:
        _DATA = json.loads(resources.files("openmove.resources").joinpath("phase0_tests.json").read_text(encoding="utf-8"))["tests"]
    return _DATA


def find(test_id):
    return next((t for t in tests() if t["id"] == test_id), None)


def record(journal, release, test_id, result, by, evidence, environment="test"):
    t = find(test_id)
    if t is None:
        raise ValueError("unknown test %r" % test_id)
    if result not in RESULTS:
        raise ValueError("the result must be pass or fail")
    if len((by or "").strip()) < 3:
        raise ValueError("a name is needed")
    if len((evidence or "").strip()) < 40:
        raise ValueError("describe what you did and what came out (at least 40 characters)")
    journal.record(KIND, release, {"test": test_id, "result": result, "by": by.strip(), "evidence": evidence.strip(), "environment": (environment or "test")[:20]})


def latest(journal, release):
    """{test id: newest event data with its time}. The newest result of a test wins, so a failed test can be run again and pass."""
    out = {}
    for e in reversed(journal.events(KIND, release, 5000)):        # events come newest first; walk oldest to newest so the newest wins
        out[e["data"]["test"]] = dict(e["data"], ts=e["ts"])
    return out


def status(journal, release):
    got = latest(journal, release) if release else {}
    rows = []
    for t in tests():
        r = got.get(t["id"])
        rows.append(dict(t, result=(r or {}).get("result") or "not run", by=(r or {}).get("by"), evidence=(r or {}).get("evidence"), at=(r or {}).get("ts"),
                         required=(t["scope"] == "retype")))
    req = [r for r in rows if r["required"]]
    summary = {"required": len(req), "passed": sum(r["result"] == "pass" for r in req), "failed": sum(r["result"] == "fail" for r in req),
               "not_run": sum(r["result"] == "not run" for r in req), "staged_not_required": sum(not r["required"] for r in rows)}
    summary["ready"] = summary["passed"] == summary["required"]
    return {"release": release, "tests": rows, "summary": summary}


def evidence_text(journal, release):
    """The evidence line for gate G0, built from the recorded results. Raises ValueError if a required test has not passed."""
    st = status(journal, release)
    s = st["summary"]
    if not s["ready"]:
        todo = [r["id"] for r in st["tests"] if r["required"] and r["result"] != "pass"]
        raise ValueError("gate G0 needs every Phase 0 test of the retype path passed for this release; still open: %s" % ", ".join(todo))
    parts = ["%s by %s" % (r["id"], r["by"]) for r in st["tests"] if r["required"]]
    return "Phase 0 tests passed for %s (%d of %d required): %s" % (release, s["passed"], s["required"], "; ".join(parts))
