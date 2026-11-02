#!/usr/bin/env python3
# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Bump the version and add a changelog entry.

    python tools/dev/release.py --title "Short summary" [--kind Added|Fixed|Changed] [--minor]

Updates version.json and CHANGELOG.md. Print the commit message to use:
"OpenMove <version>: <title>". Commit and push are done by hand.
"""
import argparse
import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VERSION = os.path.join(ROOT, "version.json")
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", required=True, help="short summary, also the version note")
    ap.add_argument("--notes", help="changelog line (default: the title)")
    ap.add_argument("--kind", default="Changed", choices=["Added", "Fixed", "Changed"])
    ap.add_argument("--minor", action="store_true", help="bump the minor number instead of the patch")
    a = ap.parse_args()

    with open(VERSION, encoding="utf-8") as f:
        v = json.load(f)
    if a.minor:
        v["minor"] += 1
        v["patch"] = 0
    else:
        v["patch"] += 1
    v["version"] = "%d.%d.%d" % (v["major"], v["minor"], v["patch"])
    v["date"] = datetime.date.today().isoformat()
    v["notes"] = a.title
    with open(VERSION, "w", encoding="utf-8", newline="\n") as f:
        json.dump(v, f, indent=2)
        f.write("\n")

    with open(CHANGELOG, encoding="utf-8") as f:
        text = f.read()
    entry = "## %s (%s)\n\n### %s\n- %s\n\n" % (v["version"], v["date"], a.kind, a.notes or a.title)
    text = re.sub(r"^(# Changelog\n\n)", lambda m: m.group(1) + entry, text, count=1)
    with open(CHANGELOG, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

    print("Version %s" % v["version"])
    print("Commit message: OpenMove %s: %s" % (v["version"], a.title))
    return 0


if __name__ == "__main__":
    sys.exit(main())
