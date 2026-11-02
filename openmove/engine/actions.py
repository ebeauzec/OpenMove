# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The only code that sends a change to the cloud for the engine: lock and unlock an instance, retype a volume.

Every change:
  * asks for a permit right now (release validated, gate approved, no halt); a refusal sends nothing and writes no step
  * writes the step with its intent and its inverse to the journal BEFORE the call
  * is recorded in the evidence folder
  * is never retried. If the cloud's answer is lost (timeout, dropped connection, a 5xx), the outcome is unknown: the step stays open,
    all changes are halted, and the engine reads the state back before it decides anything (docs/SAFETY.md).
Reads are the same calls the inventory makes (docs/API_CALLS.md, B3, B4, N2), for one volume or one instance.
"""
from .. import safety
from ..connectors.httpclient import ConnectorError, ReadOnlyViolation
from ..connectors.openstack import BLOCK_STORAGE_TYPES, COMPUTE_TYPES

JOB = "retype:"            # the journal job of a volume is JOB + volume id
COMPUTE_LOCK_VERSION = "2.9"    # the first Nova microversion that shows the lock state of an instance


class CallFailed(Exception):
    """The cloud answered with an error, or the call was never sent. The change did not happen."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class OutcomeUnknown(Exception):
    """A change may or may not have happened. Everything is halted; the state must be read back."""


class Actions:
    def __init__(self, session, journal, permit, evidence):
        self.s, self.journal, self.permit, self.ev = session, journal, permit, evidence

    # ---- reads -----------------------------------------------------------------------------------------------
    def volume(self, vid):
        return self.s.get(BLOCK_STORAGE_TYPES, "/volumes/" + vid)["volume"]

    def snapshots(self, vid):
        return self.s.get(BLOCK_STORAGE_TYPES, "/snapshots/detail", {"volume_id": vid, "all_tenants": 1})["snapshots"]

    def server(self, sid):
        return self.s.get(COMPUTE_TYPES, "/servers/" + sid, microversion=COMPUTE_LOCK_VERSION)["server"]

    def volume_types(self):
        return self.s.get(BLOCK_STORAGE_TYPES, "/types")["volume_types"]

    # ---- changes ---------------------------------------------------------------------------------------------
    def lock(self, vid, sid):
        return self._change(vid, "lock", {"server": sid, "why": "keep the instance from being changed while its volume moves"},
                            {"undo": "unlock the instance", "server": sid}, COMPUTE_TYPES, "/servers/%s/action" % sid, {"lock": None}, "2.1")

    def unlock(self, vid, sid):
        return self._change(vid, "unlock", {"server": sid}, {"undo": "lock the instance again", "server": sid},
                            COMPUTE_TYPES, "/servers/%s/action" % sid, {"unlock": None}, "2.1")

    def retype(self, vid, new_type, back_to, name="retype"):
        """Retype with on-demand migration. back_to is the type the volume has now: the inverse is a retype back to it."""
        return self._change(vid, name, {"volume": vid, "new_type": new_type, "migration_policy": "on-demand"},
                            {"undo": "retype the volume back", "volume": vid, "new_type": back_to, "migration_policy": "on-demand"},
                            BLOCK_STORAGE_TYPES, "/volumes/%s/action" % vid,
                            {"os-retype": {"new_type": new_type, "migration_policy": "on-demand"}}, None)

    def _change(self, vid, name, intent, inverse, types, path, body, microversion):
        permit = self.permit()           # raises safety.Refused: nothing is written, nothing is sent
        step = self.journal.begin_step(JOB + vid, name, intent, inverse, vid)
        self.ev.call(vid, {"step": name, "path": path, "body": body})
        try:
            status, _, _ = self.s.mutate(permit, "POST", types, path, body, microversion)
        except ReadOnlyViolation as exc:
            self.journal.finish_step(step, "failed", {"sent": False, "error": str(exc)})
            raise safety.Refused(str(exc)) from None
        except ConnectorError as exc:
            if exc.outcome_unknown or (exc.status or 0) >= 500:
                self.ev.call(vid, {"step": name, "outcome": "unknown", "error": str(exc)})
                safety.halt(self.journal, "OpenMove (automatic)", "the outcome of %s on %s is unknown: %s" % (name, vid, str(exc)[:150]))
                raise OutcomeUnknown("the outcome of %s is unknown; all changes are halted. %s" % (name, exc)) from None
            self.journal.finish_step(step, "failed", {"sent": exc.status is not None, "status": exc.status, "error": str(exc)})
            self.ev.call(vid, {"step": name, "outcome": "failed", "status": exc.status, "error": str(exc)})
            raise CallFailed(str(exc), exc.status) from None
        self.journal.finish_step(step, "done", {"status": status})
        self.ev.call(vid, {"step": name, "outcome": "done", "status": status})
        return status

    # ---- what the journal knows about the steps of one volume --------------------------------------------------
    def last_step(self, vid, name):
        for row in self.journal.steps(JOB + vid, 500):          # newest first
            if row["name"] == name:
                return row
        return None

    def open_steps(self, vid):
        return [r for r in self.journal.unresolved_steps() if r["volume"] == vid]

    def locked_by_us(self, vid):
        lock, unlock = self.last_step(vid, "lock"), self.last_step(vid, "unlock")
        return bool(lock and lock["status"] == "done" and not (unlock and unlock["status"] == "done" and unlock["id"] > lock["id"]))
