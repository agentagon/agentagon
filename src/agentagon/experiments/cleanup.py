"""Freshly measured shipping cleanup, preserving the originally selected winner."""

import copy

from agentagon.core.records import AuditError
from agentagon.experiments import checkouts, delivery, engine, evaluation
from agentagon.experiments.spec import text
from agentagon.experiments.store import load_run, locked


def start(workspace, run_id: str, operation_id: str, author: str) -> dict:
    text(operation_id, "cleanup operation identity")
    data = load_run(workspace, run_id)
    operation = f"cleanup:{operation_id}"
    existing = next(
        (c for c in data["candidates"].values() if c.get("operation_id") == operation), None
    )
    if existing:
        if existing.get("author") != author or not existing.get("cleanup_of"):
            raise AuditError("cleanup operation already belongs to another request")
        original_id = existing["cleanup_of"]
    else:
        original = delivery._selected(workspace, data, None)
        original_id = original["candidate_id"]
    return engine.new(
        workspace,
        run_id,
        parent_id=original_id,
        hypothesis="Remove unnecessary application changes while preserving every measured objective",
        author=author,
        operation_id=operation,
        cleanup_of=original_id,
    )


def finish(workspace, run_id: str, candidate_id: str) -> dict:
    with locked(workspace, run_id):
        data = load_run(workspace, run_id)
        candidate = engine._candidate(data, candidate_id)
        original_id = candidate.get("cleanup_of")
        if not original_id:
            raise AuditError("candidate is not a reserved shipping cleanup")
        if candidate.get("cleanup_outcome"):
            return copy.deepcopy(candidate["cleanup_outcome"])
        original = data["candidates"][original_id]
        selection = candidate["cleanup_original_selection"]
        if data.get("selected") != selection:
            raise AuditError("user selection changed during cleanup; no candidate was substituted")
        engine._verified_evidence(workspace, data, original)
        if (
            checkouts.git(workspace.root, "rev-parse", f"refs/heads/{selection['branch']}")
            != selection["source_revision"]
        ):
            raise AuditError("original selected branch changed during cleanup")
        invalidated = evaluation.invalidated(data, candidate)
        if (
            not invalidated
            and candidate["state"] not in engine.TERMINAL
            and candidate["state"] != "cancelled"
        ):
            return {
                "state": "pending_verification",
                "candidate_id": candidate_id,
                "original_candidate_id": original_id,
                "next_action": "Execute the frozen benchmark and obtain a fresh independent review",
            }
        comparisons = []
        outcome = {
            "candidate_id": candidate_id,
            "original_candidate_id": original_id,
            "comparisons": comparisons,
            "original_selection": selection,
        }
        if candidate["state"] == "verified" and not invalidated:
            engine._verified_evidence(workspace, data, candidate)
            for field in ("metrics", "task_metrics"):
                for name, definition in data["spec"].get(field, {}).items():
                    before, after = original[field][name], candidate[field][name]
                    comparisons.append(
                        {
                            "objective": name,
                            "kind": field,
                            "direction": definition["direction"],
                            "original": before,
                            "cleanup": after,
                            "no_worse": after <= before
                            if definition["direction"] == "min"
                            else after >= before,
                        }
                    )
            if candidate_id in evaluation.frontier(data) and all(
                item["no_worse"] for item in comparisons
            ):
                # Selection and its reviewed comparison are saved together under the run lock.
                outcome["state"] = "selected_cleanup"
                candidate["cleanup_outcome"] = outcome
                engine._select_locked(workspace, data, candidate_id)
            else:
                outcome.update(
                    state="selection_required",
                    reason="Cleanup changes the measured tradeoff or is dominated; the original selection is preserved",
                )
        else:
            outcome.update(
                state="retained_original",
                reason="Cleanup failed measurement, checks or independent review; the original selection is preserved",
            )
        candidate["cleanup_outcome"] = outcome
        engine._save(workspace, data)
    return copy.deepcopy(outcome)
