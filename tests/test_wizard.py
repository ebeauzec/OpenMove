# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The gated wizard: a step opens only when every step before it is done, and a change that matters closes the later steps again."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from openmove import demo, wizard
from openmove import server as S
from openmove.server import create_server

APPROVED = {"status": "approved", "message": "ok"}
BLOCKED = {"status": "blocked", "message": "2 checks failed"}


def fake(**kw):
    base = dict(session=None, platform=None, inventory=None, audit=None, checks=None, demo=False, selection=None, selection_ok=None, engine=None)
    base.update(kw)
    return SimpleNamespace(**base)


def opened(steps):
    return [s["id"] for s in steps if s["open"]]


def finished(steps):
    return [s["id"] for s in steps if s["done"]]


class StepOrderTests(unittest.TestCase):
    def test_a_fresh_tool_opens_only_the_first_step_and_says_what_is_missing(self):
        steps = wizard.steps(fake(), None)
        self.assertEqual(opened(steps), ["connect"])
        self.assertEqual(steps[1]["needs"], wizard.NEEDS["platform"])
        self.assertTrue(all(s["needs"] for s in steps[1:]))
        self.assertEqual(steps[0]["needs"], "")

    def test_each_step_opens_the_next_and_no_further(self):
        order = [("session", object()), ("platform", {}), ("inventory", {"volumes": []}), ("audit", {})]
        st = fake()
        for attr, value in order:
            setattr(st, attr, value)
            steps = wizard.steps(st, None)
            self.assertEqual(opened(steps)[-1], wizard.ORDER[len(finished(steps))], attr)
        self.assertEqual(opened(wizard.steps(st, None)), ["connect", "platform", "inventory", "audit", "preflight"])

    def test_a_later_step_cannot_be_open_while_an_earlier_one_is_not_done(self):
        st = fake(session=object(), platform=None, inventory={"volumes": []}, audit={})        # inventory and audit exist, the platform does not
        steps = wizard.steps(st, None)
        self.assertEqual(opened(steps), ["connect", "platform"])
        self.assertFalse(any(s["open"] for s in steps[2:]))

    def test_the_pre_flight_is_done_only_when_the_gate_is_approved(self):
        st = fake(session=object(), platform={}, inventory={"volumes": []}, audit={}, checks={"run_id": "r1", "admitted": []})
        blocked = wizard.steps(st, BLOCKED)
        self.assertNotIn("preflight", finished(blocked))
        self.assertNotIn("select", opened(blocked))
        self.assertIn("2 checks failed", [s for s in blocked if s["id"] == "select"][0]["needs"])
        self.assertIn("select", opened(wizard.steps(st, APPROVED)))


class SelectionConfirmationTests(unittest.TestCase):
    def state(self):
        vols = [{"id": "a", "class": "A"}, {"id": "b", "class": "A"}, {"id": "c", "class": "C"}]
        return fake(session=object(), platform={}, inventory={"volumes": vols}, audit={}, checks={"run_id": "r1", "admitted": ["a", "b", "c"], "risk": {}})

    def test_the_move_step_waits_for_the_confirmation_of_the_selection(self):
        st = self.state()
        self.assertNotIn("move", opened(wizard.steps(st, APPROVED)))
        wizard.confirm_selection(st)
        self.assertIn("move", opened(wizard.steps(st, APPROVED)))

    def test_a_changed_selection_voids_the_confirmation(self):
        st = self.state()
        wizard.confirm_selection(st)
        st.selection = ["a"]
        self.assertNotIn("move", opened(wizard.steps(st, APPROVED)))

    def test_a_new_pre_flight_run_voids_the_confirmation(self):
        st = self.state()
        wizard.confirm_selection(st)
        st.checks = dict(st.checks, run_id="r2")
        self.assertNotIn("move", opened(wizard.steps(st, APPROVED)))

    def test_reports_and_live_wait_for_a_dry_run_that_covers_every_volume_the_engine_would_move(self):
        st = self.state()
        wizard.confirm_selection(st)
        self.assertNotIn("reports", opened(wizard.steps(st, APPROVED)))
        st.engine = SimpleNamespace(dry_covers=lambda ids: set(ids) == {"a", "b"}, open_volumes=lambda: [])
        self.assertEqual(wizard.movable(st), ["a", "b"])                    # class C is not moved by this method
        self.assertEqual(opened(wizard.steps(st, APPROVED))[-2:], ["reports", "live"])
        st.engine = SimpleNamespace(dry_covers=lambda ids: set(ids) == {"a"}, open_volumes=lambda: [])
        self.assertNotIn("reports", opened(wizard.steps(st, APPROVED)))   # the dry run left a volume out

    def test_a_real_run_is_refused_without_a_dry_run_that_covers_its_volumes(self):
        st = self.state()
        e = SimpleNamespace(dry_covers=lambda ids: False, open_volumes=lambda: [])
        self.assertIn("dry run", wizard.start_refusal(st, e))
        e = SimpleNamespace(dry_covers=lambda ids: set(ids) == {"a", "b"}, open_volumes=lambda: [])
        self.assertIsNone(wizard.start_refusal(st, e))
        self.assertIn("dry run", wizard.start_refusal(st, SimpleNamespace(dry_covers=lambda ids: set(ids) == {"a"}, open_volumes=lambda: [])))
        self.assertIsNone(wizard.start_refusal(st, SimpleNamespace(dry_covers=lambda ids: False, open_volumes=lambda: ["a"])))      # a restart resumes

    def test_a_run_that_already_exists_keeps_the_later_steps_open(self):
        st = self.state()
        wizard.confirm_selection(st)
        st.engine = SimpleNamespace(dry_covers=lambda ids: False, open_volumes=lambda: ["a"])
        self.assertIn("live", opened(wizard.steps(st, APPROVED)))

    def test_with_nothing_the_engine_moves_there_is_no_dry_run_to_wait_for(self):
        st = self.state()
        st.selection = ["c"]                                                 # only a class C volume
        wizard.confirm_selection(st)
        self.assertEqual(wizard.movable(st), [])
        self.assertIn("live", opened(wizard.steps(st, APPROVED)))


class DemoFlowTests(unittest.TestCase):
    """The same rules through the real handlers, in demonstration mode."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.server, self.state = create_server("127.0.0.1", 0, Path(self.dir.name))
        demo.start(self.state, scenario="sf-to-pure")
        self.info = S._demo_info(self.state)

    def tearDown(self):
        self.server.server_close()
        self.state.disconnect()
        demo.stop(self.state)
        self.state.journal.close()
        self.dir.cleanup()

    def wiz(self):
        return S.api_status(self.state, {}, {})["wizard"]

    def checks(self):
        return S.api_checks_run(self.state, {"source_backend": self.info["source"], "target_backend": self.info["target"]}, {})

    def test_the_steps_open_one_by_one_as_the_work_is_done(self):
        self.assertEqual(opened(self.wiz()), ["connect", "platform"])
        S.api_discover(self.state, {}, {})
        self.assertEqual(opened(self.wiz())[-1], "inventory")
        S.api_inventory_run(self.state, {"backend": self.info["source"]}, {})
        self.assertEqual(opened(self.wiz())[-1], "audit")
        S.api_audit_run(self.state, {}, {})
        self.assertEqual(opened(self.wiz())[-1], "preflight")
        self.checks()
        self.assertEqual(opened(self.wiz())[-1], "preflight")                  # run, but the gate is not approved yet
        S.api_demo_approve(self.state, {}, {})
        self.assertEqual(opened(self.wiz())[-1], "select")
        self.assertEqual(S.api_selection_confirm(self.state, {"by": "Olive Operator"}, {})["ok"], True)
        self.assertEqual(opened(self.wiz()), list(wizard.ORDER))

    def test_the_selection_cannot_be_confirmed_before_the_gate_is_approved(self):
        S.api_discover(self.state, {}, {})
        S.api_inventory_run(self.state, {"backend": self.info["source"]}, {})
        S.api_audit_run(self.state, {}, {})
        self.checks()
        with self.assertRaises(S.ApiError):
            S.api_selection_confirm(self.state, {"by": "Olive Operator"}, {})
        S.api_demo_approve(self.state, {}, {})
        with self.assertRaises(ValueError):
            S.api_selection_confirm(self.state, {"by": "ab"}, {})                # a name is needed
        self.assertIsNotNone(self.state.journal.latest("preflight"))
        S.api_selection_confirm(self.state, {"by": "Olive Operator"}, {})
        self.assertIsNotNone(self.state.journal.latest("selection_confirmed"))

    def test_running_the_pre_flight_again_closes_the_steps_after_it(self):
        S.api_discover(self.state, {}, {})
        S.api_inventory_run(self.state, {"backend": self.info["source"]}, {})
        S.api_audit_run(self.state, {}, {})
        self.checks()
        S.api_demo_approve(self.state, {}, {})
        S.api_selection_confirm(self.state, {"by": "Olive Operator"}, {})
        self.checks()                                                          # a new run: new run id, new gate, nothing signed
        self.assertEqual(opened(self.wiz())[-1], "preflight")

    def test_disconnecting_closes_everything_after_the_first_step(self):
        S.api_discover(self.state, {}, {})
        self.state.disconnect()
        self.assertEqual(opened(self.wiz()), ["connect"])


if __name__ == "__main__":
    unittest.main()
