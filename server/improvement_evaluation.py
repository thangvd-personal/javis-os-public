"""Host-only trial acceptance over bounded, byte-exact UTF-8 artifact copies.

Registry callables are trusted data checkers installed by the host. This module never
loads an executable from an artifact, dispatches a command, or interprets output as
instructions. Coding checkers need a separate isolated runtime and remain unsupported.
"""
from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import team_acceptance as TA
import improvement_blobs
import improvement_cases
import improvement_store
from improvement_blobs import AssetUnavailable, MAX_ASSET_BYTES, check_ancestry
from improvement_cases import _ID, _contract, _digest, _fields, _frozen, _text, output_path
from improvement_store import _hash, _json

EVALUATOR_PIN = {"id": "improvement-evaluation", "version": "1",
                 "hash": _hash({name: hashlib.sha256(Path(path).read_bytes()).hexdigest() for name, path in {
                     "evaluator": __file__, "acceptance": TA.__file__, "artifact_guards": improvement_blobs.__file__,
                     "contracts": improvement_cases.__file__, "canonical_json": improvement_store.__file__}.items()})}


@dataclass(frozen=True)
class TrialArtifact:
    """A checker sees immutable data and a relative name, never the solver filesystem."""

    path: str
    sha256: str
    content: bytes
    text: str


@dataclass(frozen=True)
class OwnerDecision:
    """Construct only from host-authorized durable decisions, never from request JSON.

    Authentication, append-only history and decision CAS belong to the calling service.
    A body field claiming actor=owner is not an instance of this host record.
    """

    case_id: str
    criterion_id: str
    output_hash: str
    contract_hash: str
    verdict: str


class _Unavailable(ValueError):
    pass


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _artifact(root, relative):
    root = Path(root).absolute()
    path = root / relative
    try:
        check_ancestry(path)
        if not root.is_dir():
            raise _Unavailable("missing_artifact")
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            raise _Unavailable("invalid_artifact")
        if before.st_size > MAX_ASSET_BYTES:
            raise _Unavailable("artifact_too_large")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise _Unavailable("invalid_artifact")
            content = handle.read(MAX_ASSET_BYTES + 1)
            finished = os.fstat(handle.fileno())
        check_ancestry(path)
        after = path.stat()
        # Windows path stat and fstat have different ctime semantics; compare each
        # API with itself while checking device/inode/size/mtime across both APIs.
        if (len(content) > MAX_ASSET_BYTES or _stamp(before) != _stamp(opened)
                or _stamp(opened) != _stamp(finished) or _stamp(finished) != _stamp(after)
                or before.st_ctime_ns != after.st_ctime_ns or opened.st_ctime_ns != finished.st_ctime_ns):
            raise _Unavailable("artifact_changed")
        text = content.decode("utf-8", errors="strict")
        return TrialArtifact(relative, hashlib.sha256(content).hexdigest(), content, text)
    except _Unavailable:
        raise
    except FileNotFoundError as error:
        raise _Unavailable("missing_artifact") from error
    except UnicodeError as error:
        raise _Unavailable("artifact_not_utf8") from error
    except (AssetUnavailable, OSError, RuntimeError) as error:
        raise _Unavailable("artifact_unavailable") from error


def _summary(verdicts):
    values = list(verdicts)
    return "fail" if "fail" in values else "unknown" if not values or "unknown" in values else "pass"


def _result(contract, private, rows, artifact=None, reason=None):
    mandatory = _summary(row["verdict"] for row in rows if row["category"] == "mandatory")
    business = _summary(row["verdict"] for row in rows if row["category"] == "business")
    verdict = _summary((mandatory, business))
    primary = next(row for row in rows if row["id"] == contract["primary_metric"]["criterion_id"])
    return {"version": 1, "case_id": private["id"], "contract_hash": _hash(contract),
            "evaluator": dict(EVALUATOR_PIN), "verdict": verdict,
            "mandatory_verdict": mandatory, "business_verdict": business,
            "reason": reason or ("evaluated" if verdict == "pass" else "criterion_failed" if verdict == "fail" else "criterion_unknown"),
            "output_hash": artifact.sha256 if artifact else None,
            "output_manifest": [{"path": artifact.path, "sha256": artifact.sha256, "size": len(artifact.content)}] if artifact else [],
            "primary_metric": {**contract["primary_metric"], "verdict": primary["verdict"]}, "criteria": rows}


def _row(criterion, verdict, reason, evidence=None):
    return {key: criterion[key] for key in ("id", "kind", "category", "critical")} | {
        "verdict": verdict, "reason": reason, "evidence": evidence or {}}


def _unknown(contract, private, reason):
    return _result(contract, private, [_row(item, "unknown", reason) for item in contract["criteria"]], reason=reason)


def _owner(criterion, private, artifact, contract_hash, decisions):
    matching = [item for item in decisions if item.case_id == private["id"] and item.criterion_id == criterion["id"]]
    if not matching:
        return "unknown", "owner_confirmation_required"
    latest = matching[-1]
    if latest.output_hash != artifact.sha256 or latest.contract_hash != contract_hash:
        return "unknown", "stale_version"
    return latest.verdict, "owner_decision"


def _checker(config, private, artifact, registry):
    identifier = config["checker_id"]
    if identifier not in private["evaluator_refs"]:
        return "unknown", "checker_unavailable"
    entry = registry.get(identifier)
    if (not isinstance(entry, Mapping) or set(entry) != {"version", "hash", "check"}
            or entry["version"] != config["version"] or entry["hash"] != config["hash"] or not callable(entry["check"])):
        return "unknown", "checker_unavailable"
    try:
        result = _frozen(entry["check"](artifact, _frozen(private["answer"])))
        if (not isinstance(result, dict) or set(result) - {"verdict", "reason"}
                or result.get("verdict") not in TA.VERDICTS):
            return "unknown", "invalid_checker_result"
        reason = result.get("reason", "host_checker")
        _text(reason, 500)
        return result["verdict"], reason
    except Exception:
        # Private answer and checker exception text must not become output evidence.
        return "unknown", "checker_error"


def _checks(contract, private, artifact, registry, decisions):
    rows, contract_hash = [], _hash(contract)
    # team_acceptance reads only this host-created copy; it never receives the
    # solver workspace or a command runner. Every criterion uses the same bytes.
    with tempfile.TemporaryDirectory(prefix="improvement-check-") as directory:
        copied_root = Path(directory)
        copied = copied_root / artifact.path
        copied.parent.mkdir(parents=True, exist_ok=True)
        copied.write_bytes(artifact.content)
        for criterion in contract["criteria"]:
            kind, config = criterion["kind"], criterion["config"]
            evidence = {"path": artifact.path, "sha256": artifact.sha256, "size": len(artifact.content)}
            if kind == "command_ok":
                verdict, reason = "unknown", "checker_isolation_unavailable"
            elif kind == "host_checker":
                verdict, reason = _checker(config, private, artifact, registry)
            elif config.get("path", artifact.path) != artifact.path:
                verdict, reason = "unknown", "undeclared_artifact"
            elif kind == "owner_confirm":
                verdict, reason = _owner(criterion, private, artifact, contract_hash, decisions)
            else:
                try:
                    output_path(config["path"])
                    cleaned = TA.clean_criteria([{"kind": kind, **config}])[0]
                    verdict, check_evidence = TA.check_one(cleaned, copied_root)
                    # A temporary copy's mtime is unrelated to the trial product.
                    # Keep deterministic findings alongside the raw output hash.
                    evidence.update({key: check_evidence[key] for key in ("detail", "chars") if key in check_evidence})
                    reason = "structural_check"
                except (ValueError, OSError):
                    verdict, reason = "unknown", "structural_checker_unavailable"
            rows.append(_row(criterion, verdict, reason, evidence))
    return rows


def evaluate_trial(contract, artifact_root, case_private, checker_registry, *, owner_decisions=()) -> dict:
    """Evaluate a CaseStore private projection with host-pinned data-only checkers.

    ``checker_registry[id]`` is ``{version, hash, check}``; ``check(TrialArtifact,
    answer)`` returns ``{verdict, reason?}``. Callables and their pins are host-owned;
    caller-provided command strings or paths never become runnable checkers here.
    ``owner_decisions`` accepts only OwnerDecision records from authorized history.
    This function grants no authorization and never treats case answers as approvals.
    """
    contract, private = _frozen(contract), _frozen(case_private)
    _contract(contract)
    _fields(private, {"id", "answer", "evaluator_refs"})
    if not isinstance(private["id"], str) or not _ID.fullmatch(private["id"]):
        raise ValueError("invalid_trial_case")
    if not isinstance(private["evaluator_refs"], list) or len(private["evaluator_refs"]) > 100:
        raise ValueError("invalid_case_evaluators")
    for reference in private["evaluator_refs"]:
        _text(reference, 128)
    if len(_json(private["answer"]).encode("utf-8")) > MAX_ASSET_BYTES:
        raise ValueError("case_answer_too_large")
    if not isinstance(checker_registry, Mapping):
        raise ValueError("invalid_checker_registry")
    if not isinstance(owner_decisions, (list, tuple)) or len(owner_decisions) > 1000:
        raise ValueError("invalid_owner_decisions")
    for decision in owner_decisions:
        if not isinstance(decision, OwnerDecision) or decision.verdict not in TA.VERDICTS:
            raise ValueError("invalid_owner_decision")
        _text(decision.case_id, 128)
        _text(decision.criterion_id, 128)
        _digest(decision.output_hash)
        _digest(decision.contract_hash)
    if contract["evaluator"] != EVALUATOR_PIN:
        return _unknown(contract, private, "unsupported_evaluator")
    try:
        artifact = _artifact(artifact_root, contract["output"]["path"])
    except _Unavailable as error:
        return _unknown(contract, private, str(error))
    try:
        rows = _checks(contract, private, artifact, checker_registry, owner_decisions)
    except OSError:
        return _unknown(contract, private, "evaluation_storage_unavailable")
    try:
        if _artifact(artifact_root, artifact.path).sha256 != artifact.sha256:
            return _unknown(contract, private, "artifact_changed")
    except _Unavailable:
        return _unknown(contract, private, "artifact_changed")
    return _result(contract, private, rows, artifact)
