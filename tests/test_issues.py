"""Issue history uses evidence chronology independently of hashed audit identities."""

import pytest
from support.audit import finish

from agentagon.core.records import identifier
from agentagon.operations import import_traces, start
from agentagon.storage.issues import list_issues


@pytest.mark.parametrize(
    ("older_created", "newer_created"),
    [
        ("2026-09-09T00:00:00+00:00", "2026-09-10T00:00:00+00:00"),
        ("2026-09-10T00:00:00+00:00", "2026-09-10T00:00:00+00:00"),
        ("2026-09-10T04:00:00+05:00", "2026-09-10T00:00:00+00:00"),
    ],
    ids=["creation-time", "assignment-tiebreak", "timezone-aware"],
)
def test_latest_issue_audit_uses_timestamps_with_reverse_sorted_ids(
    workspace, fixtures, monkeypatch, older_created, newer_created
):
    requests = sorted(
        ["first-occurrence", "second-occurrence"],
        key=lambda request: identifier("audit", str(workspace.root), request),
        reverse=True,
    )
    audits = []
    for request, created, assigned in zip(
        requests,
        [older_created, newer_created],
        ["2026-09-11T00:00:00+00:00", "2026-09-12T00:00:00+00:00"],
        strict=True,
    ):
        monkeypatch.setattr("agentagon.operations.now", lambda at=created: at)
        audit_id = start(
            workspace,
            mode="traces",
            source="braintrust",
            project="demo",
            start_time="2026-08-10T00:00:00Z",
            end_time="2026-08-11T00:00:00Z",
            limit="all",
            scopes=[],
            host="test",
            model="fixture",
            request_id=request,
        )["audit_id"]
        import_traces(workspace, audit_id, fixtures / "braintrust.json")
        monkeypatch.setattr("agentagon.operations.now", lambda at=assigned: at)
        finish(workspace, audit_id)
        audits.append(audit_id)
    older, newer = audits
    assert newer < older
    assert [audit["audit_id"] for audit in workspace.audits()] == [newer, older]
    issue = list_issues(workspace)[0]
    assert issue["audit_ids"] == [older, newer]
    assert [occurrence["audit_id"] for occurrence in issue["occurrences"]] == [older, newer]
    assert issue["latest_audit_id"] == newer
    assert issue["historical_affected_traces"] == 1
