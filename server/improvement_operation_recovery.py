"""Explicit selected role recovery under the real brain and attempt OS locks."""
from __future__ import annotations

import json
import math
import time

import improvement_store as IM
from improvement_experiment_protocol import definition
from improvement_operation_execution import OperationExecution
from improvement_operation_schema import TERMINAL
from improvement_operation_workspace import OperationWorkspaceCleanupError


class OperationRecovery:
    def __init__(self, execution):
        if type(execution) is not OperationExecution:
            raise TypeError("concrete_operation_recovery_required")
        self.execution = execution

    def recover(self, principal, identifier, *, now=None):
        cutoff = time.time() if now is None else now
        if type(cutoff) not in (int, float) or not math.isfinite(cutoff):
            raise ValueError("invalid_operation_recovery_clock")
        initial = self.execution.get(principal, identifier)
        result = {"recovered": 0, "cleaned": 0, "busy": 0, "fresh": 0}
        for snapshot in initial["attempts"]:
            number = snapshot["attempt"]
            handles = self.execution.workspace.acquire_execution(initial["brain_id"], identifier, number)
            if handles is None:
                result["busy"] += 1
                continue
            try:
                cleanup = False
                with self.execution.store._Tx(self.execution.store) as connection:
                    row = self.execution._row(connection, principal, identifier)
                    attempt = connection.execute("SELECT * FROM improvement_operation_attempts WHERE operation_id=? AND brain_id=? AND attempt=?",
                                                 (identifier, row["brain_id"], number)).fetchone()
                    if attempt is None:
                        raise IM.ConflictError("operation_attempt_unavailable")
                    if attempt["status"] not in TERMINAL:
                        experiment = self.execution.plans.records._row(connection, row["brain_id"], row["experiment_id"])
                        deadline = self.execution.deadline(connection, row, definition(json.loads(experiment["definition_json"]))["timeout_seconds"])
                        expired = (attempt["lease_until"] is not None and attempt["lease_until"] <= cutoff) or (
                            attempt["lease_until"] is None and deadline <= cutoff)
                        if row["attempt"] != number or row["status"] in TERMINAL or not expired:
                            result["fresh"] += 1
                            continue
                        self.execution._stop_in(connection, row, attempt, time.time(), "timeout")
                        current = self.execution._row(connection, principal, identifier)
                        self.execution.assets._event(connection, principal, "operation-recover:" + identifier + ":" + str(number),
                            "operation_recover", {"operation_id": identifier, "attempt": number}, self.execution._public(connection, current))
                        result["recovered"] += 1
                        cleanup = True
                    else:
                        cleanup = True
                if cleanup:
                    # The kernel locks outlive DB terminal state and all filesystem
                    # work. A live foreign host cannot be cleaned based on expiry.
                    try:
                        receipt = json.loads(attempt["workspace_json"]) if attempt["workspace_json"] else None
                        self.execution.workspace.cleanup_owned(identifier, number, receipt, handles)
                    except Exception:
                        raise OperationWorkspaceCleanupError() from None
                    result["cleaned"] += 1
            finally:
                self.execution.workspace.release_execution(handles)
        return result
