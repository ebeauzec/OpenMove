# Development

## One-time setup on each machine

```bash
git config core.hooksPath .githooks
git config user.name ebeauzec
git config user.email ebeauzec@users.noreply.github.com
```

The hooks need Python 3 on the path.

## Rules

- **Sole author.** Every commit is authored and committed by the owner. The pre-push hook refuses a commit with any other author or committer, and `commit-msg` removes any co-author, sign-off or reviewer trailer that names an assistant.
- **No cloud or customer data in git.** Credential files (`clouds.yaml`, `*openrc*`), exports, reports, databases, keys and screenshots stay local. Documents, tests and examples use placeholders: addresses from the documentation ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24), names such as `project-a`, and identifiers made up for the purpose.
- **Intellectual property.** The code belongs to Eugene Beauzec and Obi1 - FZCO (`LICENSE`, `LEGAL.md`). Every source file (`.py`, `.js`, `.ts`, `.html`, `.css`, `.ps1`, `.bat`) starts with `Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.` and a pointer to `LICENSE`; the guard refuses a file without it. Every new dependency is added to `THIRD_PARTY_NOTICES.md` after its licence is checked. Third-party code is kept under `vendor/` with its own licence file. No outside contribution is merged without a signed assignment.
- **Documented in the same commit.** A change to behaviour updates `README.md`, `CONTEXT.md`, `CHANGELOG.md` and the relevant file in `docs/`.
- **Never bypass the hooks.** No `--no-verify`.

## Sensitive-data guard

`tools/guard_sensitive.py` runs from the hooks. It can also be run by hand:

```bash
python tools/guard_sensitive.py --staged
python tools/guard_sensitive.py --push
python tools/guard_sensitive.py --tree HEAD
python tools/guard_sensitive.py --history
```

Run `--tree HEAD` and `--history` before the first push and after any history change.

It checks paths, tokens and keys, OpenStack credential settings, e-mail addresses, private IP addresses and the wording rule, and it compares content against local terms:

- `.openmove_guard_terms.json` (git-ignored) lists extra strings that must never appear: `{"terms": ["..."]}`.
- Values from the local OpenStack client configuration (`clouds.yaml`, `OS_*` variables) are added automatically.
- `.openmove_guard_allow.txt` (git-ignored) lists substrings that are fine on purpose, one per line.

## Versioning and release

Every code change is a release:

```bash
python tools/dev/release.py --title "Short summary" --kind Added
git add -A
git commit -m "OpenMove <version>: Short summary"
git push origin main
```

`release.py` bumps `version.json` and adds the `CHANGELOG.md` entry, then prints the commit message. Use `--minor` for a new feature area. Changelog entries and commit messages stay short and generic.
