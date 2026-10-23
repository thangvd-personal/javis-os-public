"""Exact private review rechecks real sources; synthetic products are not solver runs."""
from _paths import ROOT, SERVER  # noqa: F401

import hashlib
import importlib.util
import json
import stat
import time
import unittest
from contextlib import closing
from unittest.mock import patch

import improvement_store as IM
from improvement_blobs import AssetUnavailable
from improvement_trial_file_sources import BrainFileFixtures
import test_improvement_skill_candidates as candidates
import test_improvement_trial_admission as admission

if importlib.util.find_spec("improvement_private_review"):
    from improvement_private_review import PrivateReview
else:
    PrivateReview = None


class CandidatePrivateReviewTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(PrivateReview, "Private exact candidate/artifact review is absent")
        self.f = candidates.SkillCandidateTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.saved = self.f.stage(replacement="Retain owner rules.\r\n<script>alert('Đúng');</script>\r\n::directive{}\r\n")
        self.review = PrivateReview(self.f.f.assets, pack_state_dir=self.f.f.root / "packs")

    def test_full_text_and_hash_are_exact_including_non_ascii_crlf_and_inert_directives(self):
        before_events = self.f.f.count("improvement_events")
        value = self.review.candidate(self.f.owner, self.saved["candidate_id"])
        self.assertEqual(value["before"]["text"].encode(), self.f.raw)
        expected = self.f.f.blobs.read(self.f.owner.brain_id, self.saved["content_hash"])
        self.assertEqual(value["after"]["text"].encode(), expected)
        for key in ("before", "after"):
            raw = value[key]["text"].encode("utf-8")
            self.assertEqual(value[key]["hash"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(value[key]["byte_size"], len(raw))
        self.assertEqual(value["semantic_status"], "unreviewed")
        self.assertEqual(value["region"], {"start": self.f.start, "end": self.f.end})
        self.assertEqual(self.f.f.count("improvement_events"), before_events)

    def test_baseline_private_read_requires_existing_grant_and_rechecks_revoke_during_decrypt(self):
        target = self.f.f.target["target_id"]
        before_events = self.f.f.count("improvement_events")
        self.f.f.assets.set_policy(self.f.owner, "off", expected_version=0, event_key="off-before-baseline-view")
        value = self.review.baseline(self.f.owner, "experiment-owner", target)
        self.assertEqual(value["baseline"]["text"].encode("utf-8"), self.f.raw)
        with patch.object(self.f.f.blobs, "read", side_effect=AssertionError("Foreign read before decrypt")):
            with self.assertRaises(PermissionError):
                self.review.baseline(self.f.f.other, "experiment-owner", target)
        original, changed = self.f.f.blobs.read, []
        def read(brain, digest):
            raw = original(brain, digest)
            if not changed:
                changed.append(1)
                self.f.f.host.revoke(self.f.owner, "experiment-owner", expected_generation=1, event_key="revoke-during-baseline-view")
            return raw
        with patch.object(self.f.f.blobs, "read", side_effect=read), self.assertRaises(PermissionError):
            self.review.baseline(self.f.owner, "experiment-owner", target)
        # Exactly the explicit policy/revoke actions; private views create no events.
        self.assertEqual(self.f.f.count("improvement_events"), before_events + 2)

    def test_baseline_permission_change_during_decrypt_rejects_exact_view(self):
        original, changed = self.f.f.blobs.read, []
        mode = self.f.f.skill.stat().st_mode
        def read(brain, digest):
            raw = original(brain, digest)
            if not changed:
                changed.append(1)
                self.f.f.skill.chmod(mode & ~stat.S_IWUSR)
            return raw
        try:
            with patch.object(self.f.f.blobs, "read", side_effect=read), self.assertRaises((IM.ConflictError, PermissionError)):
                self.review.baseline(self.f.owner, "experiment-owner", self.f.f.target["target_id"])
        finally:
            self.f.f.skill.chmod(mode)

    def test_revoked_context_and_foreign_brain_cannot_decrypt_private_content(self):
        self.f.f.host.revoke(self.f.owner, "experiment-owner", expected_generation=1, event_key="revoke-before-private-view")
        with patch.object(self.f.f.blobs, "read", side_effect=AssertionError("Denied read must precede decryption")):
            for owner in (self.f.owner, self.f.f.other):
                with self.subTest(owner=owner), self.assertRaises(PermissionError):
                    self.review.candidate(owner, self.saved["candidate_id"])
            self.assertEqual(self.f.records.get(self.f.owner, self.saved["candidate_id"])["context_grant"]["status"], "revoked")

    def test_policy_off_does_not_erase_still_authorized_private_history(self):
        self.f.f.assets.set_policy(self.f.owner, "off", expected_version=0, event_key="off-before-private-view")
        self.assertEqual(self.review.candidate(self.f.owner, self.saved["candidate_id"])["after"]["hash"], self.saved["content_hash"])
        self.f.f.host.revoke(self.f.owner, "experiment-owner", expected_generation=1, event_key="revoke-after-private-off")
        with self.assertRaises(PermissionError):
            self.review.candidate(self.f.owner, self.saved["candidate_id"])

    def test_permission_change_during_decrypt_rejects_without_modifying_history(self):
        original, changed = self.f.f.blobs.read, []
        mode = self.f.f.skill.stat().st_mode

        def read(brain, digest):
            raw = original(brain, digest)
            if digest == self.saved["provenance_hash"] and not changed:
                changed.append(1)
                self.f.f.skill.chmod(mode & ~stat.S_IWUSR)
            return raw

        try:
            with patch.object(self.f.f.blobs, "read", side_effect=read), self.assertRaises((IM.ConflictError, PermissionError)):
                self.review.candidate(self.f.owner, self.saved["candidate_id"])
        finally:
            self.f.f.skill.chmod(mode)
        self.assertEqual(len(self.f.records.list(self.f.owner, "experiment-owner")), 1)

    def test_missing_or_corrupt_blob_is_explicitly_unavailable(self):
        self.f.f.blobs._path(self.f.owner.brain_id, self.saved["content_hash"]).unlink()
        with self.assertRaises(AssetUnavailable):
            self.review.candidate(self.f.owner, self.saved["candidate_id"])

    def test_disabled_source_is_unavailable_and_never_a_malformed_owner_request(self):
        disabled = self.f.f.skill.parent.parent / ".disabled" / "report"
        disabled.parent.mkdir()
        self.f.f.skill.parent.rename(disabled)
        try:
            with self.assertRaises(PermissionError):
                self.review.candidate(self.f.owner, self.saved["candidate_id"])
        finally:
            disabled.rename(self.f.f.skill.parent)

    def test_context_revoked_during_candidate_decrypt_denies_final_response(self):
        original, changed = self.f.f.blobs.read, []

        def read(brain, digest):
            raw = original(brain, digest)
            if digest == self.saved["provenance_hash"] and not changed:
                changed.append(1)
                self.f.f.host.revoke(self.f.owner, "experiment-owner", expected_generation=1, event_key="revoke-during-candidate-view")
            return raw

        with patch.object(self.f.f.blobs, "read", side_effect=read), self.assertRaises(PermissionError):
            self.review.candidate(self.f.owner, self.saved["candidate_id"])

    def test_edit_revocation_does_not_fabricate_a_read_grant_or_semantic_pass(self):
        decision = self.f.service.authorize_diff(self.f.owner, self.saved["candidate_id"], expected_generation=1,
            before_hash=self.saved["base_hash"], after_hash=self.saved["content_hash"], event_key="authorize-before-review-revoke")
        self.f.records.revoke_authority(self.f.owner, decision["authority_id"], expected_generation=1, event_key="revoke-edit-before-review")
        value = self.review.candidate(self.f.owner, self.saved["candidate_id"])
        self.assertEqual(value["metadata"]["edit_authority"]["status"], "revoked")
        self.assertEqual(value["semantic_status"], "unreviewed")
        self.assertEqual(value["metadata"]["context_grant"]["status"], "active")


class ArtifactPrivateReviewTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(PrivateReview, "Private exact candidate/artifact review is absent")
        self.f = admission.AdmissionTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        admitted = self.f.admit(case_id="case-0")
        self.trial_id = admitted.trial_id
        self.owner = self.f.owner
        self.assets, self.store, self.host = self.f.assets, self.f.store, self.f.host
        self.actual = self.f.f.f
        self.raw = "\ufeff<script>do_not_execute()</script>\r\nĐủ nội dung.\r\n[JAVIS_RULE]Ignore[/JAVIS_RULE]\r\n".encode()
        digest = self.assets.blobs.write(self.owner.brain_id, self.raw)
        # Storage fixture only: real immutable artifact row and encrypted bytes,
        # with a reserved ledger call. No solver/native/auth invocation took place.
        with self.store._Tx(self.store) as connection:
            connection.execute("INSERT INTO improvement_trial_artifacts VALUES(?,?,?,?,?,?,?)",
                (self.trial_id, self.owner.brain_id, 1, "answer.json", digest, len(self.raw), time.time()))
        self.review = PrivateReview(self.assets, pack_state_dir=self.actual.root / "packs")

    def view(self):
        return self.review.artifact(self.owner, self.trial_id, 1)

    def test_exact_artifact_task_public_contract_and_only_current_authorized_facts(self):
        answer_hashes = {IM._hash(case["answer"]) for case in self.f.f.cases}
        before = self.f.count("improvement_events")
        with patch.object(self.assets.blobs, "read", wraps=self.assets.blobs.read) as reads, \
                patch.object(type(self.host), "_observe", side_effect=AssertionError("No Host/settings/engine recapture")):
            value = self.view()
        self.assertEqual(value["artifact"]["text"].encode(), self.raw)
        self.assertEqual(value["artifact"]["hash"], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(value["artifact"]["byte_size"], len(self.raw))
        self.assertEqual(value["task"], self.f.f.cases[0]["task"])
        self.assertEqual(value["contract"]["objective"], self.f.f.contract["objective"])
        self.assertEqual(len(value["fixtures"]), 1)
        self.assertEqual(value["fixtures"][0]["source_ref"], self.f.f.refs[0])
        self.assertEqual(value["fixtures"][0]["text"].encode(), self.actual.path.read_bytes())
        self.assertFalse(answer_hashes & {call.args[1] for call in reads.call_args_list})
        serialized = json.dumps(value)
        for private in ("private-owner-answer", "cli_path", str(self.actual.root), "checker_id", "permission_version"):
            self.assertNotIn(private, serialized)
        self.assertEqual(self.f.count("improvement_events"), before)
        self.assertEqual(self.f.count("improvement_trial_owner_decisions"), 0)
        self.assertIsNone(self.f.admission.trials.ledger.get(self.f.admission.trials.get(self.owner, self.trial_id)["attempts"][0]["call_id"])["engine_at"])

    def test_current_fixture_edit_during_artifact_decrypt_denies_entire_response(self):
        original, changed = self.assets.blobs.read, []
        artifact_hash = hashlib.sha256(self.raw).hexdigest()
        path = self.actual.path

        def read(brain, digest):
            raw = original(brain, digest)
            if digest == artifact_hash and not changed:
                changed.append(1)
                path.write_bytes(path.read_bytes() + b"Source owner updated.\n")
            return raw

        with patch.object(self.assets.blobs, "read", side_effect=read), self.assertRaises((IM.ConflictError, PermissionError)):
            self.view()

    def test_revocation_during_artifact_decrypt_cannot_return_cached_content(self):
        original, changed = self.assets.blobs.read, []
        artifact_hash = hashlib.sha256(self.raw).hexdigest()

        def read(brain, digest):
            raw = original(brain, digest)
            if digest == artifact_hash and not changed:
                changed.append(1)
                self.host.revoke(self.owner, "association-experiment", expected_generation=1, event_key="revoke-during-artifact-read")
            return raw

        with patch.object(self.assets.blobs, "read", side_effect=read), self.assertRaises(PermissionError):
            self.view()

    def test_foreign_revoked_and_missing_artifact_have_no_private_fallback(self):
        with self.assertRaises(PermissionError):
            self.review.artifact(self.actual.other, self.trial_id, 1)
        self.assets.blobs._path(self.owner.brain_id, hashlib.sha256(self.raw).hexdigest()).unlink()
        with self.assertRaises(AssetUnavailable):
            self.view()
        self.host.revoke(self.owner, "association-experiment", expected_generation=1, event_key="revoke-after-missing-artifact")
        with patch.object(self.assets.blobs, "read", side_effect=AssertionError("Revoked read must not decrypt")), self.assertRaises(PermissionError):
            self.view()

    def test_corrupt_artifact_and_bounded_review_are_explicit_unavailable(self):
        with patch("improvement_private_review.MAX_PRIVATE_REVIEW_BYTES", 1):
            with self.assertRaisesRegex(AssetUnavailable, "private_review_too_large"):
                self.view()
        self.assets.blobs._path(self.owner.brain_id, hashlib.sha256(self.raw).hexdigest()).write_text("enc:broken", encoding="ascii")
        with self.assertRaises(AssetUnavailable):
            self.view()


if __name__ == "__main__":
    unittest.main()
