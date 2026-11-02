# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The one place that decides whether OpenMove may change anything (requirement R13, docs/SAFETY.md).

Audit, inventory, checks and reports only read. By construction they cannot change a cloud: the HTTP client
refuses every request that is not a read unless it carries a Permit. A Permit is only handed out when
all of these hold, and it asks again before EVERY change, not once at the start:

  * the OpenStack release has a recorded, passed lab validation (profiles.require_mutation_allowed)
  * the pre-flight gate is approved: no failed check, every open item attested, two different people signed,
    and the pre-flight is not older than the limit (checks.gate.require_open)
  * nobody has pressed Halt

Any unexpected error while asking is a refusal. There is no override, no "force" and no retry loop in here.
"""
from . import profiles
from .checks import gate as gate_mod

HALT_KIND = "halt"
RESUME_KIND = "resume"
MIN_REASON = 8


class Refused(Exception):
    """A change was refused. Nothing was sent."""


class Halted(Refused):
    pass


def _who(by, reason):
    by, reason = (by or "").strip(), (reason or "").strip()
    if not by:
        raise ValueError("a name is required")
    if len(reason) < MIN_REASON:
        raise ValueError("give a reason of at least %d characters" % MIN_REASON)
    return by, reason


def halt(journal, by, reason):
    """Stop all changes at once. Any person may do this; it takes effect before the next change is sent."""
    by, reason = _who(by, reason)
    journal.record(HALT_KIND, "all", {"by": by, "reason": reason})


def resume(journal, by, reason):
    by, reason = _who(by, reason)
    journal.record(RESUME_KIND, "all", {"by": by, "reason": reason})


def halted(journal):
    """The halt that is in force ({'by', 'reason', 'at'}), or None. The newest of halt and resume wins."""
    last = None
    for kind in (HALT_KIND, RESUME_KIND):
        e = journal.latest(kind)
        if e and (last is None or e["id"] > last["id"]):
            last = dict(e, kind=kind)
    if last and last["kind"] == HALT_KIND:
        return {"by": last["data"].get("by"), "reason": last["data"].get("reason"), "at": last["ts"]}
    return None


class Permit:
    """Permission to change one cloud, valid only while every condition still holds.

    get_profile and get_gate are called each time, so a gate that went stale, a sign-off that was
    withdrawn or a Halt pressed a second ago stops the very next change."""

    def __init__(self, journal, get_profile, get_gate, scope=""):
        self.journal, self.get_profile, self.get_gate, self.scope = journal, get_profile, get_gate, scope
        self.checked = 0

    def check(self, method="", url=""):
        try:
            h = halted(self.journal)
            if h:
                raise Halted("all changes are halted by %s: %s" % (h["by"], h["reason"]))
            profiles.require_mutation_allowed(self.get_profile())
            gate_mod.require_open(self.get_gate())
        except Refused:
            raise
        except Exception as exc:  # anything unexpected is a refusal, never a pass
            raise Refused("%s" % (exc or type(exc).__name__)) from None
        self.checked += 1
        return True


def grant(journal, get_profile, get_gate, scope=""):
    """Return a Permit if changes are allowed right now, otherwise raise Refused."""
    p = Permit(journal, get_profile, get_gate, scope)
    p.check()
    return p
