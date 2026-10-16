"""Corporate Task Worker Re-Evaluation & Artifact Lineage Revocation (`G0-013`, `G0-016`, `G0-020`)."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from javisos.server.iam_contract.jcs import compute_action_payload_hash
from javisos.server.iam_contract.port import IamPdpAdapterPort


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CorporateTaskWorker:
    """Executes background/scheduled Corporate tasks with mandatory PDP re-evaluation (`G0-013`, `G0-020`).

    - Re-evaluates authorization + revocation at `TASK_START`, `PRE_COMMIT`, and `OFFLINE_RESUME`.
    - Enforces bounded retry schedule (`[60, 300, 900]` seconds, max 3 attempts) when PDP is unavailable
      before side-effect dispatch, transitioning to `USER_ACTION_REQUIRED` after 3 failed attempts.
    - Enforces Two-Phase Cancellation: if a task is cancelled/revoked while a downstream side-effect
      times out in an unknown state, transitions to `RECONCILIATION_REQUIRED` with `retry_scheduled=False`.
    """

    RETRY_BACKOFF_SECONDS = (60, 300, 900)

    def __init__(self, *, adapter: IamPdpAdapterPort) -> None:
        self._adapter = adapter
        self._tasks: dict[str, dict[str, Any]] = {}

    def enqueue_task(
        self,
        *,
        task_id: str,
        execution_context: Mapping[str, Any],
        resource_id: str,
        operation: str,
        payload: Mapping[str, Any],
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        payload_hash = compute_action_payload_hash(
            resource_id=resource_id,
            operation=operation,
            arguments=payload,
        )
        task = {
            "task_id": task_id,
            "execution_context": {**dict(execution_context), "resource_id": resource_id},
            "resource_id": resource_id,
            "operation": operation,
            "payload": dict(payload),
            "payload_hash": payload_hash,
            "approval_id": approval_id,
            "status": "QUEUED",
            "cancel_requested": False,
            "cancel_reason": None,
            "attempt_count": 0,
            "retry_scheduled": False,
            "next_retry_delay_seconds": None,
            "history": [],
        }
        self._tasks[task_id] = task
        return dict(task)

    def request_cancel(self, task_id: str, *, reason: str) -> dict[str, Any]:
        task = self._tasks[task_id]
        task["cancel_requested"] = True
        task["cancel_reason"] = reason
        if task["status"] == "QUEUED":
            task["status"] = "CANCELLED"
        else:
            task["status"] = "CANCEL_REQUESTED"
        task["history"].append({"stage": "CANCEL_SIGNAL", "reason": reason, "timestamp": _iso_now()})
        return dict(task)

    def _re_evaluate_stage(self, task: dict[str, Any], stage: str) -> tuple[bool, str]:
        if task["cancel_requested"]:
            return False, task["cancel_reason"] or "TASK_CANCEL_REQUESTED"

        ctx = task["execution_context"]
        try:
            rev = self._adapter.check_revocation(
                {
                    "execution_context": ctx,
                    "resource_ids": [task["resource_id"]],
                }
            )
            if rev.get("revoked"):
                reasons = ",".join(rev.get("revocation_reasons") or ["REVOKED"])
                return False, f"REVOKED:{reasons}"

            decision = self._adapter.evaluate_pdp(
                {
                    "schema_version": "1.0.0",
                    "execution_context": ctx,
                    "resource": {
                        "resource_id": task["resource_id"],
                        "tenant_id": ctx["tenant_id"],
                        "workspace_id": ctx["workspace_id"],
                    },
                    "operation": task["operation"],
                    "action_context": {
                        "payload_hash": task["payload_hash"],
                        "approval_id": task["approval_id"],
                        "idempotency_key": f"idem_{task['task_id']}",
                        "revalidation_stage": stage,
                    },
                }
            )
        except Exception:
            return False, "PDP_UNAVAILABLE"

        effect = decision.get("effect")
        if effect == "PDP_UNAVAILABLE":
            return False, "PDP_UNAVAILABLE"
        if effect != "ALLOW":
            return False, str(decision.get("reason_code") or f"EFFECT_{effect}")
        return True, "OK_GRANTED"

    def run_task(
        self,
        *,
        task_id: str,
        downstream_callable: Callable[[], Any],
        is_offline_resume: bool = False,
    ) -> dict[str, Any]:
        task = self._tasks[task_id]
        if task["status"] == "CANCELLED":
            return dict(task)

        initial_stage = "OFFLINE_RESUME" if is_offline_resume else "TASK_START"
        ok_start, reason_start = self._re_evaluate_stage(task, initial_stage)
        task["history"].append({"stage": initial_stage, "allowed": ok_start, "reason": reason_start})
        if not ok_start:
            if reason_start == "PDP_UNAVAILABLE":
                task["attempt_count"] += 1
                if task["attempt_count"] <= len(self.RETRY_BACKOFF_SECONDS):
                    task["status"] = "QUEUED"
                    task["retry_scheduled"] = True
                    task["next_retry_delay_seconds"] = self.RETRY_BACKOFF_SECONDS[task["attempt_count"] - 1]
                else:
                    task["status"] = "USER_ACTION_REQUIRED"
                    task["retry_scheduled"] = False
                    task["next_retry_delay_seconds"] = None
                return dict(task)
            task["status"] = "CANCELLED" if task["cancel_requested"] else "FAILED_AUTHZ"
            task["retry_scheduled"] = False
            return dict(task)

        task["status"] = "RUNNING"

        # Mandatory PRE_COMMIT re-evaluation right before external side-effect
        ok_pre, reason_pre = self._re_evaluate_stage(task, "PRE_COMMIT")
        task["history"].append({"stage": "PRE_COMMIT", "allowed": ok_pre, "reason": reason_pre})
        if not ok_pre:
            task["status"] = "CANCELLED" if (task["cancel_requested"] or reason_pre.startswith("REVOKED:")) else "FAILED_AUTHZ"
            task["retry_scheduled"] = False
            return dict(task)

        try:
            output = downstream_callable()
        except TimeoutError as exc:
            # Never blind-retry a timed-out side-effect; enter RECONCILIATION_REQUIRED
            task["status"] = "RECONCILIATION_REQUIRED"
            task["retry_scheduled"] = False
            task["error"] = str(exc)
            task["history"].append({"stage": "DOWNSTREAM_TIMEOUT", "reason": str(exc)})
            return dict(task)

        if task["cancel_requested"]:
            task["status"] = "CANCELLED"
            task["retry_scheduled"] = False
            return dict(task)

        task["status"] = "COMPLETED"
        task["retry_scheduled"] = False
        task["output"] = output
        return dict(task)


class ArtifactLineageTracker:
    """Tracks derived Corporate artifacts and propagates revocation across all 7 lineage stages (`G0-016`)."""

    VALID_STAGES = frozenset(
        {
            "root_resource",
            "extracted_content",
            "prompt_context",
            "output",
            "summary",
            "embedding_index",
            "memory",
            "cache",
        }
    )

    def __init__(self) -> None:
        self._nodes: dict[str, dict[str, Any]] = {}
        self._children: dict[str, set[str]] = {}

    def register_root_resource(
        self,
        *,
        resource_id: str,
        tenant_id: str,
        workspace_id: str,
        classification: str,
        policy_version: str,
    ) -> dict[str, Any]:
        node = {
            "artifact_id": resource_id,
            "parent_ids": [],
            "root_resource_ids": [resource_id],
            "derivation_stage": "root_resource",
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "classification": classification,
            "policy_version": policy_version,
            "access_revoked": False,
            "cleanup_status": "active",
            "created_at": _iso_now(),
        }
        self._nodes[resource_id] = node
        self._children.setdefault(resource_id, set())
        return dict(node)

    def register_derived_artifact(
        self,
        *,
        artifact_id: str,
        parent_ids: Sequence[str],
        derivation_stage: str,
        execution_id: str,
    ) -> dict[str, Any]:
        if derivation_stage not in self.VALID_STAGES:
            raise ValueError(f"Invalid derivation_stage: {derivation_stage}")
        if not parent_ids:
            raise ValueError("Derived artifact must specify at least one parent_id")

        parents = [self._nodes[pid] for pid in parent_ids]
        first = parents[0]
        root_ids: set[str] = set()
        any_revoked = False
        for p in parents:
            root_ids.update(p["root_resource_ids"])
            if p["access_revoked"]:
                any_revoked = True

        node = {
            "artifact_id": artifact_id,
            "parent_ids": list(parent_ids),
            "root_resource_ids": sorted(root_ids),
            "derivation_stage": derivation_stage,
            "execution_id": execution_id,
            "tenant_id": first["tenant_id"],
            "workspace_id": first["workspace_id"],
            "classification": first["classification"],
            "policy_version": first["policy_version"],
            "access_revoked": any_revoked,
            "cleanup_status": "cleanup_completed" if any_revoked else "active",
            "created_at": _iso_now(),
        }
        self._nodes[artifact_id] = node
        self._children.setdefault(artifact_id, set())
        for pid in parent_ids:
            self._children.setdefault(pid, set()).add(artifact_id)
        return dict(node)

    def propagate_revocation(self, source_id: str, *, reason: str) -> list[str]:
        if source_id not in self._nodes:
            return []
        self._nodes[source_id]["access_revoked"] = True
        self._nodes[source_id]["cleanup_status"] = "cleanup_completed"
        self._nodes[source_id]["revoke_reason"] = reason

        revoked_descendants: list[str] = []
        queue = deque(self._children.get(source_id, set()))
        visited: set[str] = set()
        while queue:
            cid = queue.popleft()
            if cid in visited:
                continue
            visited.add(cid)
            cnode = self._nodes[cid]
            cnode["access_revoked"] = True
            cnode["cleanup_status"] = "cleanup_completed"
            cnode["revoke_reason"] = reason
            revoked_descendants.append(cid)
            for grand in self._children.get(cid, set()):
                if grand not in visited:
                    queue.append(grand)
        return revoked_descendants

    def get_node(self, artifact_id: str) -> dict[str, Any]:
        return dict(self._nodes[artifact_id])

    def can_access_artifact(self, artifact_id: str) -> bool:
        node = self._nodes.get(artifact_id)
        if not node or node.get("access_revoked"):
            return False
        for rid in node.get("root_resource_ids", []):
            rnode = self._nodes.get(rid)
            if not rnode or rnode.get("access_revoked"):
                return False
        return True

    def read_for_rag_or_prompt(self, artifact_id: str) -> dict[str, Any]:
        if not self.can_access_artifact(artifact_id):
            raise PermissionError(f"LINEAGE_ACCESS_REVOKED: {artifact_id}")
        return dict(self._nodes[artifact_id])
