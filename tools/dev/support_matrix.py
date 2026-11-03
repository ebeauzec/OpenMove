#!/usr/bin/env python3
# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Write docs/SUPPORT_MATRIX.md from openmove/resources/backends.json (requirement R8). A test fails when the file is out of date.

    python tools/dev/support_matrix.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from openmove import backends  # noqa: E402


def render():
    d = backends.data()
    out = ["# Support matrix",
           "",
           "Generated from `openmove/resources/backends.json` by `tools/dev/support_matrix.py`; a test keeps this file in step. Do not edit it by hand: change the data, run the tool.",
           "",
           "OpenMove recognises a back-end by what its Cinder driver reports (vendor name and protocol). A back-end that matches no family below is **unsupported** for every pair, "
           "check P3 fails with the reason, and nothing is attempted on it. A known pair that nobody has verified is checked and planned, but reported as **not verified**, and a named "
           "person must attest it.",
           "",
           "## Families",
           "",
           "| Family | Recognised by (vendor name, protocol as the driver reports them) |",
           "|---|---|"]
    for key, f in d["families"].items():
        out.append("| %s | vendor %s%s |" % (f["title"], " or ".join("`%s`" % v for v in f["vendors"]),
                                              (", protocol " + " or ".join("`%s`" % p for p in f["protocols"])) if f["protocols"] else ""))
    out += ["", "## Method", "", d["method"], "", d["staged_method"], "", "## Verified pairs", "", "| From | To | Where | What it covers |", "|---|---|---|---|"]
    for v in d["verified"]:
        out.append("| %s | %s | %s | %s |" % (d["families"][v["from"]]["title"], d["families"][v["to"]]["title"], v["where"], v["covers"]))
    out += ["", "A verification in the lab proves the OpenStack side of the engine only. It says nothing about another release or about the arrays of a customer; "
            "Phase 0 (gate G0) is still needed for those.", "", "## Every pair", "",
            "| From | To | Classes A and B | Class C | Verification |", "|---|---|---|---|---|"]
    for r in backends.matrix():
        out.append("| %s | %s | %s | %s | %s |" % (r["from"], r["to"], r["class_a_b"], r["class_c"], r["verified"]))
    out += ["", "## Adding a back-end", "",
            "Add a family (and, when a pair has been verified, a `verified` entry) to `openmove/resources/backends.json`, with a test; run `python tools/dev/support_matrix.py`. "
            "The engine is not changed: it never tests for a vendor name.", ""]
    return "\n".join(out)


if __name__ == "__main__":
    (ROOT / "docs" / "SUPPORT_MATRIX.md").write_text(render(), encoding="utf-8", newline="\n")
    print("docs/SUPPORT_MATRIX.md written")
