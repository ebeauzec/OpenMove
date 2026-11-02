# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The migration engine (docs/ENGINE_PLAN.md).

This release moves class A and class B volumes with Cinder's own retype (migration policy on-demand), using only the OpenStack APIs.
The engine is a set of small steps driven by the journal: every tick it looks at where each volume is, reads the cloud afresh and does
at most one next thing. Nothing is remembered in memory that the journal cannot give back after a restart.

  evidence.py  the per-volume evidence folder
  actions.py   the only code that sends a change: lock, unlock, retype. Permit per call, step intent and inverse in the journal.
  plan.py      the checks made on fresh reads before a volume is moved, and the calls a dry run lists
  runner.py    the engine: dry run, run, reverse
"""
