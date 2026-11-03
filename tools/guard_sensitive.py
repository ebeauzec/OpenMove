#!/usr/bin/env python3
# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Sensitive-data guard for OpenMove.

Run by the git hooks in .githooks (git config core.hooksPath .githooks).
It stops a commit or push that would put credentials, customer or cloud
data, or any trace of an AI assistant into git.

    python tools/guard_sensitive.py --staged        files about to be committed
    python tools/guard_sensitive.py --push          every commit not yet on a remote
    python tools/guard_sensitive.py --tree HEAD     every file of one revision
    python tools/guard_sensitive.py --history       every commit of every branch

What it checks
  1. Paths that must stay on this machine (OpenStack credential files,
     exported reports, databases, keys, local notes).
  2. File content: tokens and keys, OpenStack credential settings, e-mail
     addresses, private (RFC 1918) IP addresses, and the wording rule.
  3. Local terms: .openmove_guard_terms.json (git-ignored) holds extra
     strings that must never appear (customer names, host names, project
     names). Values found in the local OpenStack client configuration
     (clouds.yaml, OS_* variables) are added automatically.
  4. Commits (--push, --history): commit messages, and the author and
     committer identity, which must be the owner.
  5. Copyright: every source file (.py .js .ts .html .css .ps1 .bat) must
     carry the owner's copyright notice in its first lines. Third-party code
     lives under vendor/ and is exempt.

A line that is fine on purpose goes in .openmove_guard_allow.txt (one
substring per line, '#' starts a comment). Findings are printed masked.
"""
import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TERMS_FILE = os.path.join(ROOT, ".openmove_guard_terms.json")
ALLOW_FILE = os.path.join(ROOT, ".openmove_guard_allow.txt")

# The only identity allowed as author and committer.
OWNER_EMAIL = "ebeauzec@users.noreply.github.com"

# The wording rule. Built from parts so this file does not contain the words.
_NAMES = ("cl" + "aude", "anthro" + "pic")
WORDING = re.compile(r"\b(?:%s)\b" % "|".join(_NAMES), re.I)

FORBIDDEN_PATHS = re.compile(
    r"(?:^|/)(?:clouds|secure)\.ya?ml$"
    r"|(?:^|/)[^/]*openrc[^/]*$"
    r"|(?:^|/)\.env(?:\.|$)"
    r"|\.(?:pem|key|pfx|p12|db|sqlite3?|docx|pptx|xlsx)$"
    r"|^(?:data|reports|exports|logs|var)/(?!README\.md$)"
    r"|^docs/images/[^/]*\.png$"
    r"|^\.openmove_guard_"
    r"|^CLAUD" + "E\\.md$"
    r"|^\.cl" + "aude/"
)
# The numbered documentation screenshots are taken by tools/dev/screenshots.py from the demonstration (made-up data) and the lab; look at them before a push
ALLOWED_PATHS = re.compile(r"^docs/images/(?:(?:architecture|workflow)_rendered|\d\d-[a-z0-9-]+)\.png$")

SOURCE_FILES = re.compile(r"\.(?:py|js|ts|html|css|ps1|bat)$", re.I)
VENDORED = re.compile(r"(?:^|/)(?:vendor|node_modules)/")
COPYRIGHT = re.compile(r"Copyright \(c\) 20\d\d(?:\S*20\d\d)? Eugene Beauzec and Obi1 - FZCO")

CONTENT_RULES = [
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key block", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("OpenStack credential",
     re.compile(r"(?i)\b(?:OS_PASSWORD|OS_APPLICATION_CREDENTIAL_SECRET|application_credential_secret|"
                r"OS_TOKEN)\b\s*[=:]\s*['\"]?[^\s'\"<>$%{(]{4,}")),
    ("auth token header", re.compile(r"(?i)\bx-auth-token\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}")),
    ("password setting", re.compile(r"(?i)^\s*password\s*:\s*['\"]?[^\s'\"<>$%{(]{4,}")),
]
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
EMAIL_OK = re.compile(r"@(?:users\.noreply\.github\.com|example\.(?:com|org|net)|openmove\.invalid)$", re.I)
IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")

# Words from the local OpenStack configuration that are ordinary text.
COMMON = {"admin", "default", "service", "services", "regionone", "demo", "public", "identity",
          "openstack", "cinder", "nova", "glance", "neutron", "keystone", "horizon", "true", "false"}


def git(*args, check=True, text=True, stdin=None):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=text, input=stdin)
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), (r.stderr or "").strip()))
    return r.stdout


def blob(sha):
    r = subprocess.run(["git", "cat-file", "blob", sha], cwd=ROOT, capture_output=True)
    return r.stdout if r.returncode == 0 else b""


def mask(s):
    s = s.strip()
    return s[:4] + "..." + "(%d chars)" % len(s) if len(s) > 8 else "(%d chars)" % len(s)


def load_allow():
    out = []
    try:
        with open(ALLOW_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append(line)
    except OSError:
        pass
    return out


def local_terms():
    terms = set()
    try:
        with open(TERMS_FILE, encoding="utf-8") as f:
            terms.update(t for t in json.load(f).get("terms", []) if isinstance(t, str) and t.strip())
    except (OSError, ValueError):
        pass
    # Values from the OpenStack client configuration on this machine.
    candidates = [os.environ.get("OS_CLIENT_CONFIG_FILE")]
    home = os.path.expanduser("~")
    candidates += [os.path.join(home, ".config", "openstack", "clouds.yaml"),
                   os.path.join(os.environ.get("APPDATA", home), "openstack", "clouds.yaml"),
                   os.path.join(home, "clouds.yaml")]
    key = re.compile(r"^\s*(?:auth_url|username|project_name|project_id|user_id|user_domain_name|"
                     r"project_domain_name|domain_name|region_name)\s*:\s*['\"]?([^'\"#\n]+?)['\"]?\s*(?:#.*)?$")
    for p in candidates:
        if p and os.path.isfile(p):
            try:
                with open(p, encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        m = key.match(line)
                        if m:
                            terms.add(m.group(1).strip())
            except OSError:
                pass
    for var in ("OS_AUTH_URL", "OS_USERNAME", "OS_PROJECT_NAME", "OS_PROJECT_ID", "OS_USER_DOMAIN_NAME"):
        if os.environ.get(var):
            terms.add(os.environ[var].strip())
    # Host part of any URL, plus the value itself.
    for t in list(terms):
        m = re.match(r"https?://([^/:]+)", t)
        if m:
            terms.add(m.group(1))
    return sorted(t for t in terms if len(t) >= 5 and t.lower() not in COMMON)


class Guard:
    def __init__(self):
        self.allow = load_allow()
        self.terms = [(t, t.lower()) for t in local_terms()]
        self.problems = []
        self.seen = set()

    def ok(self, text):
        return any(a in text for a in self.allow)

    def add(self, where, what, shown):
        self.problems.append("%s: %s %s" % (where, what, shown))

    def check_path(self, path, where):
        if ALLOWED_PATHS.match(path):
            return
        if FORBIDDEN_PATHS.search(path):
            self.add(where, "file must stay on this machine:", path)

    def check_text(self, text, where):
        low = text.lower()
        for raw, lt in self.terms:
            if lt in low:
                self.add(where, "local term found:", mask(raw))
        for n, line in enumerate(text.splitlines(), 1):
            loc = "%s:%d" % (where, n)
            if WORDING.search(line) and not self.ok(line):
                self.add(loc, "wording rule:", "(line hidden)")
            for name, rx in CONTENT_RULES:
                m = rx.search(line)
                if m and not self.ok(m.group(0)):
                    self.add(loc, name + ":", mask(m.group(0)))
            for m in EMAIL.finditer(line):
                if not EMAIL_OK.search(m.group(0)) and not self.ok(m.group(0)):
                    self.add(loc, "e-mail address:", mask(m.group(0)))
            for m in IPV4.finditer(line):
                a, b, c, d = (int(x) for x in m.groups())
                if max(a, b, c, d) > 255:
                    continue
                private = a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)
                if private and not self.ok(m.group(0)):
                    self.add(loc, "private IP address:", m.group(0))

    def check_blob(self, path, sha, where):
        self.check_path(path, where + ":" + path)
        if sha in self.seen:
            return
        self.seen.add(sha)
        data = blob(sha)
        if b"\0" in data[:8192]:
            return
        text = data.decode("utf-8", errors="replace")
        if SOURCE_FILES.search(path) and not VENDORED.search(path) and not COPYRIGHT.search(text[:1500]):
            self.add(where + ":" + path, "copyright notice missing:", "(first lines)")
        self.check_text(text, where + ":" + path)

    def check_message(self, sha, subject_body, author, committer):
        where = "commit " + sha[:10]
        for who, ident in (("author", author), ("committer", committer)):
            if ident.lower() != OWNER_EMAIL:
                self.add(where, who + " is not the owner:", ident)
        self.check_text(subject_body, where + " message")


def raw_entries(out):
    """Parse 'git diff --raw -z' output into (path, blob sha) pairs."""
    parts = out.split("\0")
    i = 0
    res = []
    while i + 1 < len(parts):
        meta = parts[i]
        if not meta.startswith(":"):
            i += 1
            continue
        f = meta.split()
        res.append((parts[i + 1], f[3]))
        i += 2
    return res


def commit_files(sha):
    out = git("diff-tree", "-r", "-z", "--root", "--no-commit-id", "--no-renames",
              "--diff-filter=AM", sha)
    return raw_entries(out)


def scan_commits(g, shas):
    for sha in shas:
        info = git("log", "-1", "--format=%ae%x00%ce%x00%B", sha)
        ae, ce, msg = info.split("\0", 2)
        g.check_message(sha, msg, ae, ce)
        for path, bsha in commit_files(sha):
            g.check_blob(path, bsha, "commit " + sha[:10])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--tree", metavar="REV")
    ap.add_argument("--history", action="store_true")
    a = ap.parse_args()
    if not (a.staged or a.push or a.tree or a.history):
        ap.print_help()
        return 2

    g = Guard()
    if a.staged:
        out = git("diff", "--cached", "--raw", "-z", "--no-renames", "--diff-filter=AM")
        for path, sha in raw_entries(out):
            g.check_blob(path, sha, "staged")
    if a.tree:
        out = git("ls-tree", "-r", "-z", a.tree)
        for rec in out.split("\0"):
            if not rec:
                continue
            meta, path = rec.split("\t", 1)
            g.check_blob(path, meta.split()[2], a.tree)
    if a.push:
        out = git("rev-list", "--reverse", "HEAD", "--not", "--remotes", check=False)
        scan_commits(g, out.split())
    if a.history:
        scan_commits(g, git("rev-list", "--reverse", "--all").split())

    if g.problems:
        print("Sensitive-data guard: %d problem(s)" % len(g.problems))
        for p in g.problems[:60]:
            print("  " + p)
        if len(g.problems) > 60:
            print("  ... and %d more" % (len(g.problems) - 60))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
