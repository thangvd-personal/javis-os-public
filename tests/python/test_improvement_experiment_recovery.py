"""Experiment recovery touches only its actual immutable trial links."""
from _paths import ROOT, SERVER  # noqa: F401

import unittest
from contextlib import closing
from unittest.mock import patch

import test_improvement_experiment_service as fixtures


class ExperimentRecoveryScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Register fixture lifetimes on this real async TestCase/runner, rather
        # than invoking doCleanups on an uninitialized IsolatedAsyncioTestCase.
        await fixtures.ExperimentServiceTests.asyncSetUp(self)

    def old_trial(self):
        return self.h.h.h.admitted.trial_id

    def begin_stop(self):
        return self.plans.records.begin_stop(self.owner, "association-experiment", kind="cancelled",
            reason="owner_requested", expected_generation=self.h.frozen["generation"], event_key="stop-before-scoped-recovery")

    async def test_unlinked_corrupt_terminal_journal_cannot_block_experiment_closure(self):
        identifier = self.old_trial()
        old = self.service.trials.get(self.owner, identifier)
        self.service.trials.cancel(self.owner, identifier, expected_generation=old["generation"],
            event_key="cancel-old-unlinked-trial")
        _, journal = self.service.trials.workspace._paths(identifier, 1)
        journal.parent.mkdir(parents=True, exist_ok=True)
        before = b'{"state":"corrupt"}'
        journal.write_bytes(before)
        self.begin_stop()
        recovered = await self.service.recover(self.owner, "association-experiment", event_key="recover-empty-with-old-corrupt-journal")
        self.assertEqual(recovered["current"]["phase"], "cancelled")
        self.assertEqual(journal.read_bytes(), before)

    async def test_empty_experiment_recovery_does_not_settle_an_unlinked_expired_reservation(self):
        identifier = self.old_trial()
        old = self.service.trials.get(self.owner, identifier)
        self.begin_stop()
        deadline = old["request"]["deadline_at"]
        with patch("improvement_trials.time.time", return_value=deadline + 1):
            recovered = await self.service.recover(self.owner, "association-experiment", event_key="recover-empty-not-unlinked-reservation")
        self.assertEqual(recovered["current"]["phase"], "cancelled")
        self.assertEqual(self.service.trials.get(self.owner, identifier)["status"], "reserved")
        # Brain-wide recovery remains a separate explicit existing operation.
        settled = self.service.trials.recover(self.owner, now=deadline + 2)
        self.assertEqual(settled["not_run"], 1)

    async def test_actual_linked_expired_trial_is_settled_without_touching_unlinked_trial(self):
        linked = self.h.reserve()
        unrelated = self.old_trial()
        original = self.service.trials.get(self.owner, unrelated)
        deadline = max(linked["request"]["deadline_at"], original["request"]["deadline_at"])
        with patch("improvement_trials.time.time", return_value=deadline + 1):
            recovered = await self.service.recover(self.owner, "association-experiment", event_key="recover-only-actual-linked-expired")
        self.assertEqual(recovered["current"]["phase"], "probing")
        self.assertEqual(self.service.trials.get(self.owner, linked["trial_id"])["status"], "not_run")
        self.assertEqual(self.service.trials.get(self.owner, unrelated)["status"], "reserved")
        with closing(self.assets.store._conn()) as connection:
            call = connection.execute("SELECT c.status,c.engine_at FROM agent_calls c JOIN improvement_trial_attempts a ON a.call_id=c.id WHERE a.trial_id=?",
                                      (linked["trial_id"],)).fetchone()
        self.assertEqual(call["status"], "refunded")
        self.assertIsNone(call["engine_at"])


if __name__ == "__main__":
    unittest.main()
