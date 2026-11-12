"""Pinned trial contracts, encrypted snapshots and host-private qualification inputs."""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import improvement_store as IM
from improvement_blobs import MAX_ASSET_BYTES

_HASH = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}")
_SNAPSHOT_KEYS = {"version", "source_ref", "source_version", "captured_at", "timezone", "data_period", "cutoff",
                  "schema", "grant_hash", "freshness", "redaction", "sufficient_source"}
_PIN_KEYS = {"experiment_id", "base_bundle_hash", "candidate_bundle_hash", "contract_hash", "case_set_hash",
             "engine_config_hash", "grant_hash", "policy_version", "scope_hash"}


def _frozen(value):
    try:
        return json.loads(IM._json(value))
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("invalid_trial_json") from error


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or not set(required) <= set(value):
        raise ValueError("invalid_trial_fields")


def _text(value, limit=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError("invalid_trial_text")
    return value


def _digest(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError("invalid_trial_hash")


def output_path(value):
    """One declared relative artifact path has the same meaning on Windows and Unix."""
    if (not isinstance(value, str) or not value or len(value.encode("utf-8")) > 400
            or any(c in value for c in '\\<>:"|?*') or any(ord(c) < 32 for c in value)):
        raise ValueError("invalid_trial_output_path")
    for part in value.split("/"):
        if (not part or part in (".", "..") or part.endswith((".", " "))
                or re.fullmatch(r"(?:con|prn|aux|nul|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])",
                                part.split(".", 1)[0].rstrip(" "), re.I)):
            raise ValueError("invalid_trial_output_path")
    return value


def _contract(value):
    _fields(value, {"version", "objective", "output", "criteria", "primary_metric", "evaluator", "unknown_conditions", "output_hash_policy"})
    if type(value["version"]) is not int or value["version"] != 1 or value["output_hash_policy"] != "raw_sha256":
        raise ValueError("unsupported_trial_contract")
    _text(value["objective"], 4000)
    _fields(value["output"], {"kind", "path", "media_type"})
    if value["output"]["kind"] != "text":
        raise ValueError("unsupported_trial_output_kind")
    output_path(value["output"]["path"])
    _text(value["output"]["media_type"], 128)
    criteria = value["criteria"]
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= 12:
        raise ValueError("invalid_trial_criteria")
    identifiers, categories = set(), set()
    for criterion in criteria:
        _fields(criterion, {"id", "category", "critical", "description", "kind", "config"})
        _text(criterion["description"], 2000)
        if (not isinstance(criterion["id"], str) or not _ID.fullmatch(criterion["id"])
                or criterion["id"] in identifiers or criterion["category"] not in ("mandatory", "business")
                or type(criterion["critical"]) is not bool):
            raise ValueError("invalid_trial_criterion")
        identifiers.add(criterion["id"])
        categories.add(criterion["category"])
        kind, config = criterion["kind"], criterion["config"]
        if criterion["category"] == "business" and kind not in ("host_checker", "command_ok", "owner_confirm"):
            raise ValueError("business_evaluator_required")
        if kind in ("host_checker", "command_ok"):
            _fields(config, {"checker_id", "version", "hash"})
            _text(config["checker_id"], 128)
            _text(config["version"], 128)
            _digest(config["hash"])
        else:
            allowed = {"file_exists": ({"path"}, set()), "file_contains": ({"path"}, {"min_chars", "must_contain"}),
                       "json_keys": ({"path", "keys"}, set()), "owner_confirm": (set(), {"path", "target"})}
            if not isinstance(kind, str) or kind not in allowed:
                raise ValueError("unsupported_trial_criterion")
            _fields(config, *allowed[kind])
            import team_acceptance as TA
            if "path" in config and (not isinstance(config["path"], str) or len(config["path"]) > 400):
                raise ValueError("invalid_trial_output_path")
            for field, limit, width in (("keys", 20, 100), ("must_contain", 10, 200)):
                if field in config and (not isinstance(config[field], list) or len(config[field]) > limit
                        or any(not isinstance(item, str) or not item or len(item) > width for item in config[field])):
                    raise ValueError("invalid_trial_criterion_config")
            if "min_chars" in config and (type(config["min_chars"]) is not int or config["min_chars"] < 0):
                raise ValueError("invalid_trial_criterion_config")
            TA.clean_criteria([{"kind": kind, **config}])
    if categories != {"mandatory", "business"}:
        raise ValueError("business_criteria_required")
    metric = value["primary_metric"]
    _fields(metric, {"criterion_id", "direction", "aggregation"})
    business = {item["id"] for item in criteria if item["category"] == "business"}
    if (not isinstance(metric["criterion_id"], str) or metric["criterion_id"] not in business
            or metric["direction"] not in ("higher", "lower")
            or metric["aggregation"] != "all_pass"):
        raise ValueError("unsupported_trial_metric")
    _fields(value["evaluator"], {"id", "version", "hash"})
    _text(value["evaluator"]["id"], 128)
    _text(value["evaluator"]["version"], 128)
    _digest(value["evaluator"]["hash"])
    if not isinstance(value["unknown_conditions"], list) or not value["unknown_conditions"]:
        raise ValueError("unknown_conditions_required")
    for condition in value["unknown_conditions"]:
        _text(condition, 128)
    IM._json(value)


def public_contract(value):
    """Preserve the owner's acceptance intent while keeping evaluator internals private."""
    contract = _frozen(value)
    _contract(contract)
    criteria = []
    for item in contract["criteria"]:
        public = {key: item[key] for key in ("id", "category", "critical", "description")}
        if item["kind"] in ("host_checker", "command_ok"):
            public["kind"] = "independent_check"
        else:
            public.update(kind=item["kind"], config=item["config"])
        criteria.append(public)
    return {key: contract[key] for key in ("version", "objective", "output")} | {"criteria": criteria}


def validate_public_contract(value):
    """Reject private evaluator material even when a caller recomputes the public hash."""
    value = _frozen(value)
    _fields(value, {"version", "objective", "output", "criteria"})
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("unsupported_public_trial_contract")
    _text(value["objective"], 4000)
    _fields(value["output"], {"kind", "path", "media_type"})
    if value["output"]["kind"] != "text":
        raise ValueError("unsupported_trial_output_kind")
    output_path(value["output"]["path"])
    _text(value["output"]["media_type"], 128)
    if not isinstance(value["criteria"], list) or not 1 <= len(value["criteria"]) <= 12:
        raise ValueError("invalid_trial_criteria")
    identities, categories = set(), set()
    for item in value["criteria"]:
        _fields(item, {"id", "category", "critical", "description", "kind"}, {"config"})
        if (not isinstance(item["id"], str) or not _ID.fullmatch(item["id"]) or item["id"] in identities
                or item["category"] not in ("mandatory", "business") or type(item["critical"]) is not bool):
            raise ValueError("invalid_trial_criterion")
        identities.add(item["id"])
        categories.add(item["category"])
        _text(item["description"], 2000)
        kind = item["kind"]
        if kind == "independent_check":
            if "config" in item:
                raise ValueError("private_trial_checker_material")
            continue
        allowed = {"file_exists": ({"path"}, set()), "file_contains": ({"path"}, {"min_chars", "must_contain"}),
                   "json_keys": ({"path", "keys"}, set()), "owner_confirm": (set(), {"path", "target"})}
        if not isinstance(kind, str) or kind not in allowed or (item["category"] == "business" and kind != "owner_confirm"):
            raise ValueError("invalid_public_trial_criterion")
        config = item.get("config")
        _fields(config, *allowed[kind])
        import team_acceptance as TA
        TA.clean_criteria([{"kind": kind, **config}])
    if categories != {"mandatory", "business"}:
        raise ValueError("business_criteria_required")
    return value


class CaseStore:
    def __init__(self, improvement_store):
        self.assets = improvement_store
        self.store = improvement_store.store
        self.blobs = improvement_store.blobs

    def _readable(self):
        return IM._schema_version(self.store) >= 2

    @staticmethod
    def _row(connection, table, brain, identifier):
        row = connection.execute(f"SELECT * FROM {table} WHERE id=? AND brain_id=?", (identifier, brain)).fetchone()
        if not row:
            raise PermissionError("trial_record_unavailable")
        return row

    @staticmethod
    def _enabled(connection, brain):
        if IM.ImprovementStore._policy(connection, brain)["mode"] == "off":
            raise PermissionError("improvement_disabled")

    def _event(self, connection, principal, key, operation, payload, result):
        IM.ImprovementStore._event(connection, principal, key, operation, payload, result)

    def _put(self, principal, table, prefix, manifest, event_key, extra=None):
        IM._brain(principal)
        IM.ensure(self.store)
        with self.store._Tx(self.store) as connection:
            return self._put_in(connection, principal, table, prefix, manifest, event_key, extra)

    def _own_transaction(self, connection):
        """Private composition accepts only an active transaction on this exact DB."""
        if type(connection) is not sqlite3.Connection or not connection.in_transaction:
            raise ValueError("case_store_transaction_required")
        databases = connection.execute("PRAGMA database_list").fetchall()
        expected = getattr(self.store, "path", None)
        if (expected is None or len(databases) != 1 or databases[0][1] != "main"
                or Path(databases[0][2]).resolve() != Path(expected).resolve()):
            raise ValueError("case_store_transaction_mismatch")

    def _put_in(self, connection, principal, table, prefix, manifest, event_key, extra=None):
        self._own_transaction(connection)
        brain = IM._brain(principal)
        hashed = IM._hash(manifest)
        replay = IM.ImprovementStore._replay(connection, brain, event_key, prefix, manifest)
        if replay is not None:
            return replay
        self._enabled(connection, brain)
        row = connection.execute(f"SELECT id FROM {table} WHERE brain_id=? AND manifest_hash=?", (brain, hashed)).fetchone()
        identifier = row["id"] if row else prefix + "_" + secrets.token_hex(12)
        result = {"contract_id" if prefix == "ic" else "snapshot_id": identifier, "manifest_hash": hashed}
        if extra:
            result.update(extra(connection, identifier, row is None))
        elif row is None:
            connection.execute(f"INSERT INTO {table} VALUES(?,?,?,?,?)", (identifier, brain, hashed, IM._json(manifest), time.time()))
        self._event(connection, principal, event_key, prefix, manifest, result)
        return result

    def put_contract(self, principal, contract, *, event_key):
        IM._brain(principal)
        contract = _frozen(contract)
        _contract(contract)
        return self._put(principal, "improvement_contracts", "ic", contract, event_key)

    def get_contract(self, principal, contract_id):
        brain = IM._brain(principal)
        if not self._readable():
            raise PermissionError("trial_record_unavailable")
        with closing(self.store._conn()) as connection:
            row = self._row(connection, "improvement_contracts", brain, contract_id)
            return {"contract_id": row["id"], "manifest_hash": row["manifest_hash"], "contract": json.loads(row["contract_json"])}

    def put_snapshot(self, principal, manifest, raw: bytes, fixture: bytes, *, event_key):
        self._snapshot_inputs(principal, manifest, raw, fixture)
        IM.ensure(self.store)
        with self.store._Tx(self.store) as connection:
            return self._put_snapshot_in(connection, principal, manifest, raw, fixture, event_key=event_key)

    @staticmethod
    def _snapshot_inputs(principal, manifest, raw, fixture):
        brain = IM._brain(principal)
        manifest = _frozen(manifest)
        _fields(manifest, _SNAPSHOT_KEYS)
        if (type(manifest["version"]) is not int or manifest["version"] != 1
                or type(manifest["sufficient_source"]) is not bool or manifest["freshness"] not in ("current", "stale")
                or not isinstance(raw, bytes) or not isinstance(fixture, bytes)
                or max(len(raw), len(fixture)) > MAX_ASSET_BYTES):
            raise ValueError("invalid_source_snapshot")
        for field in _SNAPSHOT_KEYS - {"version", "sufficient_source"}:
            _text(manifest[field])
        _digest(manifest["grant_hash"])
        try:
            ZoneInfo(manifest["timezone"])
            for field in ("captured_at", "cutoff"):
                if datetime.fromisoformat(manifest[field]).utcoffset() is None:
                    raise ValueError("timezone_required")
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("invalid_source_time") from error
        # The fixture passed into a text-only solver must be actual UTF-8, never replacement-decoded.
        fixture.decode("utf-8", errors="strict")
        import hashlib
        source_hash, fixture_hash = hashlib.sha256(raw).hexdigest(), hashlib.sha256(fixture).hexdigest()
        status = "sufficient" if manifest["sufficient_source"] and manifest["freshness"] == "current" else "insufficient_source"
        pinned = {**manifest, "source_hash": source_hash, "fixture_hash": fixture_hash, "status": status}
        return brain, manifest, source_hash, fixture_hash, status, pinned

    def _put_snapshot_in(self, connection, principal, manifest, raw: bytes, fixture: bytes, *, event_key):
        self._own_transaction(connection)
        brain, manifest, source_hash, fixture_hash, status, pinned = self._snapshot_inputs(principal, manifest, raw, fixture)

        def write(connection, identifier, fresh):
            if fresh:
                self.blobs.write(brain, raw)
                self.blobs.write(brain, fixture)
                connection.execute("INSERT INTO improvement_snapshots VALUES(?,?,?,?,?,?,?,?)",
                    (identifier, brain, IM._hash(pinned), IM._json(manifest), source_hash, fixture_hash, status, time.time()))
            return {"source_hash": source_hash, "fixture_hash": fixture_hash, "status": status}

        return self._put_in(connection, principal, "improvement_snapshots", "is", pinned, event_key, write)

    def get_snapshot(self, principal, snapshot_id):
        brain = IM._brain(principal)
        if not self._readable():
            raise PermissionError("trial_record_unavailable")
        with closing(self.store._conn()) as connection:
            row = self._row(connection, "improvement_snapshots", brain, snapshot_id)
            return {"snapshot_id": row["id"], "manifest_hash": row["manifest_hash"], "manifest": json.loads(row["manifest_json"]),
                    "source_hash": row["source_hash"], "fixture_hash": row["fixture_hash"], "status": row["status"]}

    def put_case_set(self, principal, cases, seed, *, event_key):
        brain = IM._brain(principal)
        cases = _frozen(cases)
        if not isinstance(cases, list) or not 1 <= len(cases) <= 1000 or type(seed) is not int:
            raise ValueError("invalid_trial_cases")
        IM.ensure(self.store)
        with self.store._Tx(self.store) as connection:
            return self._put_case_set_in(connection, principal, cases, seed, event_key=event_key)[0]

    def _put_case_set_in(self, connection, principal, cases, seed, *, event_key):
        self._own_transaction(connection)
        brain = IM._brain(principal)
        cases = _frozen(cases)
        if not isinstance(cases, list) or not 1 <= len(cases) <= 1000 or type(seed) is not int:
            raise ValueError("invalid_trial_cases")
        self._enabled(connection, brain)
        rows, private, identities, families, sources = [], {}, set(), set(), set()
        for case in cases:
            _fields(case, {"id", "family_key", "split", "task", "snapshot_ids", "answer", "evaluator_refs"})
            if (not isinstance(case["id"], str) or not _ID.fullmatch(case["id"]) or case["id"] in identities
                    or case["split"] not in ("tuning", "qualification", "regression")):
                raise ValueError("invalid_trial_case")
            _text(case["family_key"], 500)
            _text(case["task"], MAX_ASSET_BYTES)
            if case["family_key"] in families:
                raise ValueError("duplicate_case_family")
            refs = case["snapshot_ids"]
            if (not isinstance(refs, list) or not refs or len(refs) > 100
                    or any(not isinstance(ref, str) for ref in refs) or len(set(refs)) != len(refs)):
                raise ValueError("invalid_case_sources")
            if not isinstance(case["evaluator_refs"], list) or len(case["evaluator_refs"]) > 100:
                raise ValueError("invalid_case_evaluators")
            for ref in case["evaluator_refs"]:
                _text(ref, 128)
            tokens = {"family:" + IM._hash(case["family_key"])}
            for snapshot_id in refs:
                snapshot = self._row(connection, "improvement_snapshots", brain, snapshot_id)
                if snapshot["status"] != "sufficient":
                    raise ValueError("insufficient_source")
                if snapshot["source_hash"] in sources:
                    raise ValueError("duplicate_case_source")
                sources.add(snapshot["source_hash"])
                for content_hash in {snapshot["source_hash"], snapshot["fixture_hash"]}:
                    self.blobs.read(brain, content_hash)
                tokens.add("source:" + snapshot["source_hash"])
            answer = IM._json(case["answer"]).encode("utf-8")
            if len(answer) > MAX_ASSET_BYTES:
                raise ValueError("case_answer_too_large")
            private[case["id"]] = answer
            rows.append({key: value for key, value in case.items() if key != "answer"} | {
                "answer_hash": IM._hash(case["answer"]), "fingerprints": sorted(tokens)})
            identities.add(case["id"])
            families.add(case["family_key"])
        manifest = {"version": 1, "seed": seed, "cases": rows}
        replay = IM.ImprovementStore._replay(connection, brain, event_key, "case_set", manifest)
        if replay is not None:
            return replay, False
        hashed = IM._hash(manifest)
        old = connection.execute("SELECT id FROM improvement_case_sets WHERE brain_id=? AND manifest_hash=?", (brain, hashed)).fetchone()
        identifier = old["id"] if old else "ics_" + secrets.token_hex(12)
        if not old:
            connection.execute("INSERT INTO improvement_case_sets VALUES(?,?,?,?,?)", (identifier, brain, hashed, IM._json(manifest), time.time()))
            for row in rows:
                self.blobs.write(brain, private[row["id"]])
                connection.execute("INSERT INTO improvement_cases VALUES(?,?,?,?,?)", (identifier, row["id"], brain, row["split"], IM._json(row)))
                for snapshot_id in row["snapshot_ids"]:
                    connection.execute("INSERT INTO improvement_case_sources VALUES(?,?,?,?)", (identifier, row["id"], brain, snapshot_id))
                for token in row["fingerprints"]:
                    connection.execute("INSERT INTO improvement_case_families VALUES(?,?,?,?,?)", (identifier, row["id"], brain, token, row["split"]))
        result = {"case_set_id": identifier, "manifest_hash": hashed, "counts": {
            split: sum(row["split"] == split for row in rows) for split in ("tuning", "qualification", "regression")}}
        self._event(connection, principal, event_key, "case_set", manifest, result)
        return result, old is None

    def _case(self, connection, brain, case_set_id, case_id):
        row = connection.execute("SELECT case_json FROM improvement_cases WHERE case_set_id=? AND id=? AND brain_id=?",
                                 (case_set_id, case_id, brain)).fetchone()
        if not row:
            raise PermissionError("trial_record_unavailable")
        return json.loads(row["case_json"])

    def _solver(self, connection, brain, case):
        fixtures = []
        for snapshot_id in case["snapshot_ids"]:
            row = self._row(connection, "improvement_snapshots", brain, snapshot_id)
            manifest = json.loads(row["manifest_json"])
            original = self.blobs.read(brain, row["source_hash"])
            fixture = original if row["source_hash"] == row["fixture_hash"] else self.blobs.read(brain, row["fixture_hash"])
            fixtures.append({"snapshot_id": snapshot_id, "hash": row["fixture_hash"],
                             "source": {key: manifest[key] for key in ("source_version", "data_period", "timezone", "cutoff", "schema")},
                             "content": fixture.decode("utf-8")})
        return {"id": case["id"], "task": case["task"], "fixtures": fixtures}

    def solver_case(self, principal, case_set_id, case_id):
        brain = IM._brain(principal)
        if not self._readable():
            raise PermissionError("trial_record_unavailable")
        with closing(self.store._conn()) as connection:
            return self._solver(connection, brain, self._case(connection, brain, case_set_id, case_id))

    def evaluator_case(self, principal, case_set_id, case_id):
        brain = IM._brain(principal)
        if not self._readable():
            raise PermissionError("trial_record_unavailable")
        with closing(self.store._conn()) as connection:
            case = self._case(connection, brain, case_set_id, case_id)
            return {"id": case_id, "answer": json.loads(self.blobs.read(brain, case["answer_hash"])),
                    "evaluator_refs": case["evaluator_refs"]}

    def improver_cases(self, principal, case_set_id):
        brain = IM._brain(principal)
        if not self._readable():
            raise PermissionError("trial_record_unavailable")
        with self.store._Tx(self.store) as connection:
            self._enabled(connection, brain)
            self._row(connection, "improvement_case_sets", brain, case_set_id)
            rows = [json.loads(row[0]) for row in connection.execute(
                "SELECT case_json FROM improvement_cases WHERE case_set_id=? AND brain_id=? AND split='tuning' ORDER BY id",
                (case_set_id, brain))]
            for case in rows:
                for token in case["fingerprints"]:
                    qualification = connection.execute("SELECT 1 FROM improvement_case_families WHERE brain_id=? "
                        "AND fingerprint=? AND split='qualification' LIMIT 1", (brain, token)).fetchone()
                    claim = connection.execute("SELECT q.status FROM improvement_qualification_claims c JOIN improvement_qualification q "
                        "ON q.id=c.reservation_id AND q.brain_id=c.brain_id WHERE c.brain_id=? AND c.fingerprint=?", (brain, token)).fetchone()
                    if qualification and (not claim or claim["status"] != "consumed"):
                        raise IM.ConflictError("qualification_exposure_denied")
            result = [self._solver(connection, brain, case) for case in rows]
            for case in rows:
                for token in case["fingerprints"]:
                    connection.execute("INSERT OR IGNORE INTO improvement_case_exposures VALUES(?,?,?,?)", (brain, token, case_set_id, time.time()))
            return result

    @staticmethod
    def _pin_shape(pins):
        _fields(pins, _PIN_KEYS)
        _text(pins["experiment_id"], 128)
        if type(pins["policy_version"]) is not int or pins["policy_version"] < 0:
            raise ValueError("invalid_trial_policy_pin")
        for key in _PIN_KEYS - {"experiment_id", "policy_version"}:
            _digest(pins[key])

    @staticmethod
    def _reservation(row):
        return {"reservation_id": row["id"], "case_set_id": row["case_set_id"], "pins_hash": row["pins_hash"], "status": row["status"]}

    def reserve_qualification(self, principal, case_set_id, pins, *, event_key):
        brain = IM._brain(principal)
        pins = _frozen(pins)
        self._pin_shape(pins)
        IM.ensure(self.store)
        with self.store._Tx(self.store) as connection:
            return self._reserve_qualification_in(connection, principal, case_set_id, pins, event_key=event_key)

    def _reserve_qualification_in(self, connection, principal, case_set_id, pins, *, event_key):
        self._own_transaction(connection)
        brain = IM._brain(principal)
        pins = _frozen(pins)
        self._pin_shape(pins)
        payload = {"case_set_id": case_set_id, "pins": pins}
        replay = IM.ImprovementStore._replay(connection, brain, event_key, "qualification_reserve", payload)
        if replay is not None:
            return replay
        from improvement_experiment_qualification import require_legacy_qualification
        require_legacy_qualification(connection, brain, case_set_id, pins)
        self._enabled(connection, brain)
        case_set = self._row(connection, "improvement_case_sets", brain, case_set_id)
        policy = IM.ImprovementStore._policy(connection, brain)
        if case_set["manifest_hash"] != pins["case_set_hash"] or policy["version"] != pins["policy_version"]:
            raise IM.ConflictError("stale_qualification_pin")
        old = connection.execute("SELECT * FROM improvement_qualification WHERE brain_id=? AND case_set_id=?", (brain, case_set_id)).fetchone()
        if old and old["pins_hash"] != IM._hash(pins):
            raise IM.ConflictError("qualification_already_reserved")
        if not old:
            if not connection.execute("SELECT 1 FROM improvement_contracts WHERE brain_id=? AND manifest_hash=?", (brain, pins["contract_hash"])).fetchone():
                raise PermissionError("trial_record_unavailable")
            for key in ("base_bundle_hash", "candidate_bundle_hash"):
                if not connection.execute("SELECT 1 FROM improvement_bundles WHERE brain_id=? AND manifest_hash=?", (brain, pins[key])).fetchone():
                    raise PermissionError("trial_record_unavailable")
            tokens = {row[0] for row in connection.execute("SELECT fingerprint FROM improvement_case_families WHERE case_set_id=? "
                "AND brain_id=? AND split='qualification'", (case_set_id, brain))}
            if not tokens:
                raise ValueError("qualification_cases_required")
            for token in tokens:
                if (connection.execute("SELECT 1 FROM improvement_qualification_claims WHERE brain_id=? AND fingerprint=?", (brain, token)).fetchone()
                        or connection.execute("SELECT 1 FROM improvement_case_exposures WHERE brain_id=? AND fingerprint=?", (brain, token)).fetchone()):
                    raise IM.ConflictError("qualification_family_unavailable")
            for row in connection.execute("SELECT DISTINCT s.manifest_json FROM improvement_case_sources c JOIN improvement_snapshots s "
                "ON s.id=c.snapshot_id AND s.brain_id=c.brain_id WHERE c.case_set_id=? AND c.brain_id=?", (case_set_id, brain)):
                if json.loads(row[0])["grant_hash"] != pins["grant_hash"]:
                    raise IM.ConflictError("stale_source_grant")
            identifier = "iq_" + secrets.token_hex(12)
            connection.execute("INSERT INTO improvement_qualification VALUES(?,?,?,?,?,?,?,?)", (
                identifier, brain, case_set_id, IM._hash(pins), IM._json(pins), "reserved", time.time(), None))
            for token in tokens:
                connection.execute("INSERT INTO improvement_qualification_claims VALUES(?,?,?)", (brain, token, identifier))
            old = self._row(connection, "improvement_qualification", brain, identifier)
        result = self._reservation(old)
        self._event(connection, principal, event_key, "qualification_reserve", payload, result)
        return result

    def consume_qualification(self, principal, reservation_id, pins, *, event_key):
        brain = IM._brain(principal)
        pins = _frozen(pins)
        self._pin_shape(pins)
        if not self._readable():
            raise PermissionError("trial_record_unavailable")
        payload = {"reservation_id": reservation_id, "pins": pins}
        with self.store._Tx(self.store) as connection:
            replay = IM.ImprovementStore._replay(connection, brain, event_key, "qualification_consume", payload)
            if replay is not None:
                return replay
            row = self._row(connection, "improvement_qualification", brain, reservation_id)
            from improvement_experiment_qualification import require_legacy_qualification
            require_legacy_qualification(connection, brain, row["case_set_id"], pins)
            if row["pins_hash"] != IM._hash(pins):
                raise IM.ConflictError("stale_qualification_pin")
            if row["status"] == "reserved":
                connection.execute("UPDATE improvement_qualification SET status='consumed',consumed_at=? WHERE id=?", (time.time(), reservation_id))
            result = {**self._reservation(row), "status": "consumed"}
            self._event(connection, principal, event_key, "qualification_consume", payload, result)
            return result
