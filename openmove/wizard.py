# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The gated wizard: which step the operator may open, and what is missing for the next one.

The steps run in a fixed order. A step is open only when every step before it is done, so the checks and the safety procedures cannot be
walked around: no inventory before the platform is known, no pre-flight before the audit, no selection before the gate is approved, no
move before the selection is confirmed and a dry run has been read, and no live view or report before that.

This module only answers "what is done". The rules that guard a change in the cloud (permit, gate, validation, signatures) stay where
they were, and are asked again at every call; the wizard is the order in which the person is led to them.
"""

ORDER = ("connect", "platform", "inventory", "audit", "preflight", "select", "move", "reports", "live")

# what is missing for a step, said to the person, while the step before it is not done
NEEDS = {
    "platform": "Connect to your cloud first (step 1).",
    "inventory": "Scan the cloud first (step 2).",
    "audit": "Scan the cloud first (step 2).",
    "preflight": "Scan the cloud first (step 2).",
    "select": "Finish the safety checks first (step 3): run them, confirm what needs a person, and get two different people to approve.",
    "move": "Confirm your choice first (step 4).",
    "reports": "Rehearse the move first (step 5).",
    "live": "Rehearse the move first (step 5).",
}


def _run_id(state):
    return (state.checks or {}).get("run_id")


def selection_key(state):
    """The pre-flight run and the exact volumes chosen. A new run or any change of the selection makes an earlier confirmation void."""
    from . import selection
    return (_run_id(state), tuple(sorted(selection.selected_ids(state))))


def confirm_selection(state):
    state.selection_ok = selection_key(state)
    return state.selection_ok


def selection_confirmed(state):
    return bool(state.checks) and getattr(state, "selection_ok", None) == selection_key(state)


def movable(state):
    """The selected volumes the engine moves (class A and B). Class C is held back with a cause and is not moved by this method."""
    from .engine.runner import MOVED
    from . import selection
    cls = {v["id"]: v.get("class") for v in (state.inventory or {}).get("volumes", [])}
    return [i for i in selection.selected_ids(state) if cls.get(i) in MOVED]


def dry_run_current(state):
    """A dry run was read for this pre-flight run, and it covered every volume the engine would move."""
    e = getattr(state, "engine", None)
    return bool(e) and bool(state.checks) and e.dry_covers(movable(state))


def start_refusal(state, engine, ids=None):
    """Why a real run may not start yet, or None. A run is always preceded by a dry run read for the same volumes; an earlier run may be resumed."""
    movable_now = movable(state)
    wanted = [i for i in (ids or movable_now) if i in set(movable_now)]
    if engine.open_volumes() or engine.dry_covers(wanted):
        return None
    return "run the dry run first (the Rehearse button in step 5, Move): it shows every call before any is sent, for the volumes this run would move"


def done(state, gate):
    """Which steps are done, by what the tool holds right now."""
    approved = bool(gate) and gate.get("status") == "approved"
    d = {
        "connect": state.session is not None,
        "platform": state.platform is not None,
        "inventory": state.inventory is not None,
        "audit": state.audit is not None,
        "preflight": state.checks is not None and approved,
    }
    d["select"] = d["preflight"] and selection_confirmed(state)
    # in demonstration mode the engine does not run (the simulation is on the Live tab); with nothing the engine moves there is nothing to read
    d["move"] = d["select"] and (state.demo or not movable(state) or dry_run_current(state) or _run_started(state))
    d["reports"] = d["live"] = d["move"]
    return d


def _run_started(state):
    """A run that already exists (for example after a restart) keeps the Move step done: its volumes are in the journal."""
    e = getattr(state, "engine", None)
    return bool(e) and bool(e.open_volumes())


def steps(state, gate):
    """[{id, done, open, needs}] in order. `open` means every step before it is done; `needs` says what is missing, empty when open."""
    d = done(state, gate)
    out, previous = [], True
    for sid in ORDER:
        needs = "" if previous else NEEDS[sid]
        if needs and sid == "select" and d["audit"] and gate and gate.get("message"):
            needs += " The gate says: " + str(gate["message"])
        out.append({"id": sid, "done": bool(d[sid]), "open": previous, "needs": needs})
        previous = previous and bool(d[sid])
    return out
