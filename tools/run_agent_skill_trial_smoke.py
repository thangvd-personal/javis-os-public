"""Controlled synthetic engine feasibility only; this is not a business or dashboard pilot.

Default operation captures the explicit persisted selection and verified-binary pin
without authentication or inference. Only --live verifies native authentication and
runs one tuning baseline. There are no provider/model/reasoning overrides, retries,
promotions, activations, account credential copies or production context claims.
The fresh output root retains private encrypted history and its disposable Fernet
key; losing that key makes the encrypted history unrecoverable.

Run with explicit absolute --settings, --cli and fresh --output-root paths. Review
the default dry-run report first. Append --live only after independent runtime
qualification; a dry-run is not evidence that authentication or isolation passed.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import io
import inspect
import json
import os
import re
import stat
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SURFACE = "controlled_synthetic_engine_feasibility"


def _absolute(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise argparse.ArgumentTypeError("an explicit absolute path is required")
    return path


def _timeout(value):
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("timeout must be an integer from 1 through 300") from None
    if not 1 <= result <= 300:
        raise argparse.ArgumentTypeError("timeout must be an integer from 1 through 300")
    return result


def parser():
    result = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    result.add_argument("--settings", type=_absolute, required=True, help="read-only persisted settings JSON")
    result.add_argument("--cli", type=_absolute, required=True, help="exact currently selected native executable")
    result.add_argument("--output-root", type=_absolute, required=True, help="fresh private directory; never reused")
    result.add_argument("--timeout-seconds", type=_timeout, default=300)
    result.add_argument("--live", action="store_true", help="explicitly permit verified native auth and one query")
    return result


def _windows_units(value):
    return len(str(value).encode("utf-16-le")) // 2


def _check_output_budget(root):
    """This profile uses ordinary Windows paths, without extended-path assumptions."""
    if os.name != "nt":
        return
    digest = "a" * 64
    workspace = root / "state" / "trials" / ("it_" + "a" * 24) / "attempt-1"
    known_paths = (
        root / "state" / "assets" / digest / (digest + ".enc"),
        root / "state" / "trials" / ".journal" / (digest + ".json"),
        root / "state" / "trials" / ".locks" / (digest + ".lock"),
        workspace / "outputs" / "answer.json",
        workspace / "input.json",
    )
    # MAX_PATH includes the terminating NUL. Count UTF-16 code units, not UTF-8
    # bytes or Python characters; non-BMP folder characters consume two units.
    if max(_windows_units(path) for path in known_paths) >= 260:
        raise ValueError("smoke_output_path_too_long")


def _prepare_root(root):
    """Isolate all application state before imports; secure the root before key writes."""
    _check_output_budget(root)
    if "config" in sys.modules:
        raise RuntimeError("smoke_requires_fresh_process")
    for component in reversed((root.parent, *root.parent.parents)):
        info = component.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("unsafe_smoke_output_parent")
    root.mkdir(mode=0o700, exist_ok=False)
    for key, child in {"JAVIS_STATE_DIR": "state", "BRAIN_PATH": "brain", "BRAINS_DIR": "brains",
                       "OBSIDIAN_VAULT_PATH": "vault", "SOURCES_PATH": "sources",
                       "JAVIS_SESSIONS_DB": "state/sessions.sqlite3",
                       "JAVIS_CLAUDE_PROJECTS_DIR": "native-logs"}.items():
        os.environ[key] = str(root / child)
    # The fresh ledger may contain one baseline and cannot borrow a production cap.
    os.environ["JAVIS_RESONANCE_CALL_CEILING"] = "1"
    sys.path.insert(0, str(ROOT / "server"))
    from improvement_sandbox import _private_directory
    _private_directory(root)
    (root / "private").mkdir(mode=0o700)
    (root / "brain").mkdir(mode=0o700)


def _selection_module():
    import improvement_trial_selection
    return improvement_trial_selection


def _adapter_type():
    from improvement_claude import ClaudeTrialAdapter
    return ClaudeTrialAdapter


def _base_report():
    return {"version": 1, "surface": SURFACE, "production_context_coverage": "unavailable",
            "business_pilot": False, "candidate_executed": False, "promotion_or_activation": False,
            "status": "unavailable", "reason": None, "inference_attempted": False,
            "engine_start_marked": False,
            "inference_kind": "none", "requested": None, "pin": None,
            "runtime_capability": {"verified": False}, "trial": None, "ledger": [], "usage": None}


def _safe_reason(error):
    reason = str(error)
    return reason if re.fullmatch(r"(?:trial_selection_|unsupported_trial_)[a-z_]+", reason) else "smoke_unavailable"


def _runtime_summary(capability, *, synthetic=False):
    runtime = capability.get("runtime") or {}
    return {"verified": capability.get("supported") is True and not synthetic,
            "capability_hash": capability.get("capability_hash"),
            "reason": capability.get("reason"),
            "observations": {key: runtime[key] for key in
                ("platform", "cli_hash", "cli_version", "sdk_version", "profile_hash", "managed_policy") if key in runtime}}


def _recover_report(root, report, *, synthetic_no_inference=False):
    """Keep durable engine-start/call facts visible if later report assembly failed."""
    path = root / "state" / "resonance.sqlite3"
    if not path.is_file():
        return
    try:
        with contextlib.closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1.0)) as connection:
            connection.row_factory = sqlite3.Row
            deadline = time.monotonic() + 2.0
            connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            report["ledger"] = [dict(row) for row in connection.execute(
                "SELECT id,purpose,status,engine,model,attempt,engine_at FROM agent_calls ORDER BY id LIMIT 2")]
            # The durable start marker is independent of optional trial/usage views.
            marked = any(row["engine_at"] is not None for row in report["ledger"])
            report.update(engine_start_marked=marked,
                inference_attempted=marked and not synthetic_no_inference,
                inference_kind="synthetic_no_inference" if synthetic_no_inference else "native" if marked else "none")
            if marked:
                report["status"] = "harness_error"
            row = connection.execute("SELECT id,status,attempt,request_json FROM improvement_trials LIMIT 1").fetchone()
            if row:
                request = json.loads(row["request_json"])
                report["trial"] = {"trial_id": row["id"], "status": row["status"], "attempt": row["attempt"],
                                   "variant": request["variant"], "repeat": request["repeat"]}
                report["evidence"] = {"database": "state/resonance.sqlite3", "encrypted_blobs": "state/assets",
                    "trace": [dict(item) for item in connection.execute(
                        "SELECT sequence,kind,payload_hash FROM improvement_trial_observations WHERE trial_id=? ORDER BY sequence LIMIT 1000", (row["id"],))],
                    "results": [dict(item) for item in connection.execute(
                        "SELECT id,result_hash,blob_hash FROM improvement_trial_results WHERE trial_id=? ORDER BY created_at LIMIT 2", (row["id"],))]}
        if row:
            try:
                from improvement_usage import trial_usage
                report["usage"] = trial_usage(request["pins"]["experiment_id"], row["id"])
            except Exception:
                report.update(usage=None, recovery_coverage="usage_unavailable")
    except Exception:
        report["recovery_coverage"] = "unknown"


def _checker(artifact, answer):
    try:
        valid = json.loads(artifact.text) == answer["expected"]
    except (ValueError, KeyError):
        valid = False
    return {"verdict": "pass" if valid else "fail", "reason": "synthetic_fixture_check"}


def _write_report(root, report):
    from evidence_store import redact
    key_file = root / "private" / "smoke.key"
    if key_file.is_file() and not report.get("encryption"):
        report = {**report, "encryption": {"codec": "Fernet", "key_file": "private/smoke.key",
            "retention": "retain_private_output_root",
            "recovery": "Losing the retained disposable key makes encrypted history unrecoverable."}}
    public = redact(report)
    if public != report:
        public["redaction"] = "sensitive_output_removed"
    raw = json.dumps(public, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    with (root / "report.json").open("x", encoding="utf-8") as handle:
        handle.write(raw + "\n")
    if key_file.is_file():
        from cryptography.fernet import Fernet
        encrypted = Fernet(key_file.read_bytes()).encrypt(raw.encode("utf-8"))
        with (root / "report.json.enc").open("xb") as handle:
            handle.write(b"enc:" + encrypted)
    return raw


def _run_one(options, pin, capability, *, selected_engine, adapter_factory, synthetic_no_inference=False):
    """Trusted harness seam. Test adapters are programmatic only, never CLI options."""
    from cryptography.fernet import Fernet
    import improvement_store as IM
    from improvement_assets import capture_source
    from improvement_blobs import RevisionBlobs
    from improvement_cases import CaseStore
    from improvement_evaluation import EVALUATOR_PIN
    from improvement_telemetry import NativeUsageRecorder
    from improvement_trial_context import ContextSource, capture_context
    from improvement_trial_file_sources import BrainFileFixtures
    from improvement_trial_host import HostContextStore, TARGET_FIELDS
    from improvement_trial_runner import TrialRunner
    from improvement_trials import TrialStore
    from improvement_usage import trial_usage
    from resonance_store import GoalStore, Principal

    root, brain = options.output_root, options.output_root / "brain"
    # Root ACL/0700 has already been established. Descendants inherit that private ACL.
    key = Fernet.generate_key()
    descriptor = os.open(root / "private" / "smoke.key", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(key)
    codec = Fernet(key)
    blobs = RevisionBlobs(root / "state" / "assets",
        lambda text: "enc:" + codec.encrypt(text.encode("utf-8")).decode("ascii"),
        lambda text: codec.decrypt(text.removeprefix("enc:").encode("ascii")).decode("utf-8"))
    store = GoalStore(root / "state" / "resonance.sqlite3")
    assets, owner = IM.ImprovementStore(store, blobs), Principal("owner", "synthetic-smoke-owner", str(brain))
    cases = CaseStore(assets)
    skill = brain / "skills" / "controlled-smoke" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes(b"---\nname: Controlled synthetic smoke\ndescription: Report authorized synthetic fixture JSON.\n---\n"
                      b"Use the fixture_read tool for the supplied snapshot. Return only the requested JSON object as your final response.\n")
    source, content = capture_source(brain, "skill", "controlled-smoke", pack_state_dir=root / "state" / "packs-state")
    target = assets.capture(owner, source, content, event_key="smoke-capture-skill")
    bundle = assets.create_bundle(owner, [target["revision_id"]], event_key="smoke-baseline-bundle")
    owner_file = brain / "synthetic-owner.md"
    owner_file.write_bytes(b"Controlled synthetic engine feasibility. All supplied sources are synthetic and host-owned.\n"
                           b"Read only the authorized fixture tool and return the declared final JSON artifact.\n"
                           b"Do not access accounts, production data, other tools, or external services.\n")
    surface = {"kind": SURFACE, "session_id": "", "ordinary_dashboard_complete": False}

    def context_capture(principal, requested_surface, targets):
        if principal != owner or requested_surface != surface:
            raise PermissionError("synthetic_smoke_scope_changed")
        return capture_context([ContextSource("synthetic-owner", brain, owner_file.name)],
                               surface=surface, complete_plan=True)

    def observe_targets(principal, identifiers):
        if principal != owner or identifiers != [target["target_id"]]:
            raise PermissionError("synthetic_smoke_target_changed")
        current, _ = capture_source(brain, "skill", "controlled-smoke", pack_state_dir=root / "state" / "packs-state")
        row = next(item for item in assets.targets(owner) if item["id"] == target["target_id"])
        for name in ("kind", "canonical_ref", "identity", "source_hash", "protected_hash", "ownership"):
            row[name] = getattr(current, name)
        return [{name: row[name] for name in TARGET_FIELDS}]

    def same_engine():
        current = selected_engine()
        if current != pin:
            raise IM.ConflictError("synthetic_smoke_selection_changed")
        return current

    now = datetime.now(timezone.utc).isoformat()
    metadata = {"timezone": "UTC", "data_period": "synthetic-smoke", "cutoff": now,
                "schema": "application/json", "freshness_policy": "require_current_version"}
    refs, expected = [], []
    for index, value in enumerate(("alpha-3", "beta-7", "gamma-11"), 1):
        path = brain / "fixtures" / f"case-{index}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"value": value}), encoding="utf-8", newline="")
        refs.append("brain-file:" + path.relative_to(brain).as_posix())
        expected.append({"value": value})
    fixtures = BrainFileFixtures({ref: metadata for ref in refs})
    host = HostContextStore(assets, context_capture=context_capture, selected_engine=same_engine,
                            target_observer=observe_targets, fixture_observer=fixtures)
    binding = host.create(owner, "synthetic-smoke", surface, [target["target_id"]],
                          source_refs=refs, event_key="smoke-host-context")
    inventory = fixtures(owner, refs)
    by_ref = {entry["source_ref"]: entry for entry in inventory}
    checker_hash = hashlib.sha256(inspect.getsource(_checker).encode("utf-8")).hexdigest()
    contract = {"version": 1, "objective": "Return the authorized synthetic fixture object as JSON without Markdown.",
        "output": {"kind": "text", "path": "answer.json", "media_type": "application/json"},
        "criteria": [
            {"id": "shape", "category": "mandatory", "critical": True, "kind": "json_keys",
             "description": "The final artifact must be a JSON object containing value.", "config": {"path": "answer.json", "keys": ["value"]}},
            {"id": "fixture", "category": "business", "critical": True, "kind": "host_checker",
             "description": "The JSON object must exactly represent the selected authorized synthetic fixture.",
             "config": {"checker_id": "synthetic-fixture", "version": "1", "hash": checker_hash}}],
        "primary_metric": {"criterion_id": "fixture", "direction": "higher", "aggregation": "all_pass"},
        "evaluator": EVALUATOR_PIN, "unknown_conditions": ["missing_artifact", "unverified_installation", "config_unknown"],
        "output_hash_policy": "raw_sha256"}
    saved_contract = cases.put_contract(owner, contract, event_key="smoke-contract")
    items, snapshots = [], []
    for index, (ref, split) in enumerate(zip(refs, ("tuning", "qualification", "regression")), 1):
        captured = fixtures.capture(owner, ref, expected_entry=by_ref[ref])
        manifest = {**captured["manifest"], "grant_hash": binding["grant_hash"], "captured_at": now}
        snapshot = cases.put_snapshot(owner, manifest, captured["raw"], captured["fixture"], event_key=f"smoke-snapshot-{index}")
        snapshots.append({"snapshot_id": snapshot["snapshot_id"], "source_hash": by_ref[ref]["source_hash"],
                          "fixture_hash": by_ref[ref]["fixture_hash"], "source_version": by_ref[ref]["source_version"]})
        items.append({"id": f"smoke-case-{index}", "family_key": f"synthetic-family-{index}", "split": split,
            "task": "Read the authorized fixture snapshot and return its JSON object exactly as your final artifact, without Markdown.",
            "snapshot_ids": [snapshot["snapshot_id"]], "answer": {"expected": expected[index - 1]}, "evaluator_refs": ["synthetic-fixture"]})
    case_set = cases.put_case_set(owner, items, 20261023, event_key="smoke-case-set")
    pins = {key: binding[key] for key in ("experiment_id", "scope_hash", "grant_hash", "engine_config_hash", "policy_version")}
    pins.update(base_bundle_hash=bundle["manifest_hash"], candidate_bundle_hash=bundle["manifest_hash"],
                contract_hash=saved_contract["manifest_hash"], case_set_hash=case_set["manifest_hash"])
    request = {"version": 1, "pins": pins, "case_set_id": case_set["case_set_id"], "case_id": "smoke-case-1",
        "variant": "baseline", "repeat": 1, "engine_config": pin, "baseline_context_hash": binding["baseline_context_hash"],
        "target_generations": binding["target_generations"], "deadline_at": time.time() + options.timeout_seconds}
    trials = TrialStore(assets)
    # Runtime capability was verified before reaching this function; guard again before reservation.
    if capability.get("supported") is not True:
        raise PermissionError("synthetic_smoke_runtime_unavailable")
    host.live_guard(owner, request)
    trial = trials.reserve(owner, request, event_key="smoke-one-baseline")
    runner = TrialRunner(trials, context_loader=host.context_loader, live_guard=host.live_guard,
        adapter_factory=adapter_factory, recorder_factory=NativeUsageRecorder,
        checker_registry={"synthetic-fixture": {"version": "1", "hash": checker_hash, "check": _checker}})
    result = asyncio.run(runner.run(owner, trial["trial_id"]))
    report = _base_report()
    report.update(status=result["status"], reason=result["attempts"][-1]["failure_reason"], pin=pin,
        inference_kind="synthetic_no_inference" if synthetic_no_inference else "native",
        engine_start_marked=result["attempts"][-1]["engine_at"] is not None,
        inference_attempted=not synthetic_no_inference and result["attempts"][-1]["engine_at"] is not None,
        runtime_capability=_runtime_summary(capability, synthetic=synthetic_no_inference),
        trial={"trial_id": trial["trial_id"], "attempt": result["attempt"], "status": result["status"], "variant": "baseline", "repeat": 1},
        pins=pins, target=target, snapshots=snapshots, case_counts=case_set["counts"],
        usage=trial_usage(pins["experiment_id"], trial["trial_id"]),
        encryption={"codec": "Fernet", "key_file": "private/smoke.key", "retention": "retain_private_output_root",
                    "recovery": "Losing the retained disposable key makes encrypted history unrecoverable."})
    report["ledger"] = [{key: row.get(key) for key in ("id", "purpose", "status", "engine", "model", "attempt", "engine_at")}
                         for row in trials.ledger.rows()]
    receipt = None
    try:
        receipt = trials.read_receipt(owner, trial["trial_id"], result["attempt"])
    except PermissionError:
        pass
    report["observed"] = {
        "models": {"values": (receipt or {}).get("actual_models", []), "status": (receipt or {}).get("coverage", {}).get("model", "unknown")},
        "reasoning": {"values": (receipt or {}).get("actual_reasoning", []), "status": (receipt or {}).get("coverage", {}).get("reasoning", "unknown")}}
    with contextlib.closing(store._conn()) as connection:
        rows = connection.execute("SELECT sequence,kind,payload_hash FROM improvement_trial_observations WHERE trial_id=? ORDER BY sequence",
                                  (trial["trial_id"],)).fetchall()
        saved = connection.execute("SELECT id,result_hash,blob_hash FROM improvement_trial_results WHERE trial_id=? ORDER BY created_at",
                                   (trial["trial_id"],)).fetchall()
    stored_result = result.get("result") or {}
    evaluation = stored_result.get("evaluation") or {}
    report["evidence"] = {"database": "state/resonance.sqlite3", "encrypted_blobs": "state/assets", "trace_count": len(rows),
        "trace": [dict(row) for row in rows], "results": [dict(row) for row in saved],
        "artifact_hash": stored_result.get("artifact_hash"),
        "receipt_hash": IM._hash(receipt) if receipt else None,
        "verdict": stored_result.get("verdict", "unknown"),
        "evaluation": {key: evaluation.get(key, "unknown" if key != "reason" else None)
                       for key in ("verdict", "mandatory_verdict", "business_verdict", "reason")},
        "execution_checks": [{key: item.get(key) for key in ("id", "verdict", "reason")}
                             for item in stored_result.get("mandatory", [])]}
    return report


def main(argv=None, *, _synthetic_no_inference=False):
    """CLI uses native execution; the private test label has no command-line option."""
    options = parser().parse_args(argv)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            _prepare_root(options.output_root)
    except Exception as error:
        reason = "smoke_output_path_too_long" if str(error) == "smoke_output_path_too_long" else "fresh_private_output_required"
        print(json.dumps({**_base_report(), "reason": reason}))
        return 2
    except (asyncio.CancelledError, KeyboardInterrupt):
        print(json.dumps({**_base_report(), "status": "cancelled", "reason": "private_output_preparation_cancelled"}))
        return 130
    report = _base_report()
    exit_code = 0
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            selection = _selection_module()
            report["requested"] = selection.read_selection(options.settings)
            from evidence_store import redact
            if redact(report["requested"]) != report["requested"]:
                raise ValueError("sensitive_selection_unavailable")
            pin = selection.create_pin(options.settings, options.cli)
            report["pin"] = pin
            if not options.live:
                report.update(status="dry_run", reason="live_flag_required")
            else:
                adapter_type = _adapter_type()
                capability = adapter_type(pin, workspace_root=options.output_root / "state" / "trials").verify()
                report["runtime_capability"] = _runtime_summary(capability, synthetic=_synthetic_no_inference)
                if capability.get("supported") is not True:
                    report["reason"] = "runtime_profile_unavailable"
                else:
                    requested = report["requested"]
                    report = _run_one(options, pin, capability,
                        selected_engine=lambda: selection.create_pin(options.settings, options.cli), adapter_factory=adapter_type,
                        synthetic_no_inference=_synthetic_no_inference)
                    report["requested"] = requested
    except (asyncio.CancelledError, KeyboardInterrupt):
        with contextlib.redirect_stdout(io.StringIO()):
            _recover_report(options.output_root, report, synthetic_no_inference=_synthetic_no_inference)
        report.update(status="cancelled", reason="cancelled")
        exit_code = 130
    except Exception as error:
        report.update(status="unavailable", reason=_safe_reason(error))
        with contextlib.redirect_stdout(io.StringIO()):
            _recover_report(options.output_root, report, synthetic_no_inference=_synthetic_no_inference)
    raw = _write_report(options.output_root, report)
    print(raw)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
