"""Owner trial status projects stored evidence without refreshing ambient usage."""
from _paths import ROOT, SERVER  # noqa: F401

import importlib.util
import hashlib
import json
import unittest
from unittest.mock import patch

import test_improvement_trial_results as fixtures
import improvement_store as IM
from resonance_store import Principal

if importlib.util.find_spec("improvement_trial_views"):
    import improvement_trial_views as views
else:
    views = None


class TrialViewTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(views, "Cold safe trial views are missing")
        self.f = fixtures.TrialResultTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.owner = self.f.f.owner
        self.assets = self.f.f.assets

    def test_reserved_status_has_actual_ledger_and_unknown_usage_without_sources(self):
        trial = self.f.f.reserve()
        wrapper = IM.read_existing(self.assets.store.path, self.assets.blobs)
        with patch("improvement_usage.trial_usage", side_effect=AssertionError("No usage refresh")), \
                patch("usage_index.improvement_events", side_effect=AssertionError("No index ingestion")):
            result = views.read_trial(wrapper, self.owner, trial["trial_id"])
        self.assertEqual(result["status"], "reserved")
        self.assertEqual(result["attempts"][0]["call_id"], trial["attempts"][0]["call_id"])
        self.assertIsNone(result["attempts"][0]["engine_at"])
        self.assertIsNone(result["usage"]["requests"])
        self.assertIsNone(result["usage"]["tokens_in"])
        self.assertEqual(result["usage"]["origin"], "unavailable")
        self.assertIsNone(result["evaluation"])
        serialized = json.dumps(result)
        for forbidden in ("cli_path", "synthetic-unused-cli", "private-answer", "source_ref", "deadline_at"):
            self.assertNotIn(forbidden, serialized)

    def test_terminal_product_and_execution_verdicts_remain_distinct(self):
        trial, token, raw = self.f.staged()
        self.f.finish(trial, token)
        wrapper = IM.read_existing(self.assets.store.path, self.assets.blobs)
        with patch("improvement_usage.trial_usage", side_effect=AssertionError("No live refresh")):
            result = views.read_trial(wrapper, self.owner, trial["trial_id"])
        report = self.f.results.read(self.owner, trial["trial_id"], 1)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["verdict"], report["verdict"])
        self.assertEqual(result["evaluation"]["verdict"], report["evaluation"]["verdict"])
        self.assertEqual(result["execution_checks"], [{key: row[key] for key in ("id", "verdict")}
                                                    for row in report["mandatory"]])
        self.assertEqual(result["usage"]["origin"], "stored_result")
        self.assertEqual(result["usage"]["coverage"]["status"], "partial")
        self.assertIsNone(result["usage"]["requests"])
        serialized = json.dumps(result)
        for forbidden in ("private-answer", "output_manifest", "live_guard", "capability_hash", "evidence"):
            self.assertNotIn(forbidden, serialized)

    def test_foreign_and_non_owner_status_are_denied(self):
        trial = self.f.f.reserve()
        for principal in (self.f.f.other, Principal("agent", "agent", self.owner.brain_id)):
            with self.subTest(principal=principal), self.assertRaises(PermissionError):
                views.read_trial(self.assets, principal, trial["trial_id"])

    def test_corrupt_result_is_unavailable_instead_of_hidden_as_no_result(self):
        trial, token, _ = self.f.staged()
        self.f.finish(trial, token)
        with patch.object(self.assets.blobs, "read", return_value=b"corrupted"):
            with self.assertRaises((IM.AssetUnavailable, ValueError)):
                views.read_trial(self.assets, self.owner, trial["trial_id"])

    def test_private_checker_details_are_dropped_and_known_usage_zero_is_preserved(self):
        trial, token, _ = self.f.staged(supported=True)
        evaluate = fixtures.EVAL.evaluate_trial
        secret = "Private expected result and owner-only checker explanation"
        def private_details(*args, **kwargs):
            result = evaluate(*args, **kwargs)
            result["criteria"][-1].update(reason=secret, evidence={"expected": secret})
            return result
        self.f.usage.update(tokens_in=0, tokens_out=19, requests=1,
            money={"currency": "USD", "reported_usd": None, "estimated_usd": None,
                   "subscription_equivalent_usd": 0.12})
        with patch.object(fixtures.EVAL, "evaluate_trial", private_details):
            self.f.finish(trial, token)
        self.assertIn(secret, json.dumps(self.f.results.read(self.owner, trial["trial_id"], 1)))
        wrapper = IM.read_existing(self.assets.store.path, self.assets.blobs)
        def files():
            return {p: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in self.f.f.root.rglob("*") if p.is_file()}
        before = files()
        result = views.read_trial(wrapper, self.owner, trial["trial_id"])
        after = files()
        self.assertEqual(set(before), set(after))
        self.assertEqual({p: h for p, h in before.items() if not p.name.endswith("-shm")},
                         {p: h for p, h in after.items() if not p.name.endswith("-shm")})
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(result["usage"]["tokens_in"], 0)
        self.assertEqual(result["usage"]["tokens_out"], 19)
        self.assertEqual(result["usage"]["requests"], 1)
        self.assertEqual(result["usage"]["money"]["subscription_equivalent_usd"], 0.12)
        self.assertIsNone(result["usage"]["money"]["reported_usd"])

    def test_broken_existing_result_pointer_is_not_reported_as_unknown_without_a_result(self):
        trial, token, _ = self.f.staged()
        self.f.finish(trial, token)
        with self.assets.store._conn() as connection:
            pointer = json.loads(connection.execute("SELECT result_json FROM improvement_trial_attempts").fetchone()[0])
            pointer["result_id"] = "missing-result"
            connection.execute("UPDATE improvement_trial_attempts SET result_json=?", (json.dumps(pointer),))
        with self.assertRaises(PermissionError):
            views.read_trial(self.assets, self.owner, trial["trial_id"])

    def test_captured_pointer_must_match_both_immutable_result_hash_and_blob_reference(self):
        trial, token, _ = self.f.staged()
        self.f.finish(trial, token)
        with self.assets.store._conn() as connection:
            pointer = json.loads(connection.execute("SELECT result_json FROM improvement_trial_attempts").fetchone()[0])
            pointer["blob_hash"] = "f" * 64
            connection.execute("UPDATE improvement_trial_attempts SET result_json=?", (json.dumps(pointer),))
        wrapper = IM.read_existing(self.assets.store.path, self.assets.blobs)
        with self.assertRaises(IM.AssetUnavailable):
            views.read_trial(wrapper, self.owner, trial["trial_id"])


if __name__ == "__main__":
    unittest.main()
