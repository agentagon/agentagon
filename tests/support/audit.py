"""Shared audit and review test support."""

import json
from pathlib import Path

from agentagon.core.records import load_json
from agentagon.operations import prepare, start, submit


def respond(workspace, audit_id, stage, edit):
    prepared = prepare(workspace, audit_id, stage, batch_size=100)
    path = prepared["response_template"]
    response = load_json(Path(path))
    packet = load_json(Path(prepared["packet"]))
    edit(response, packet)
    Path(path).write_text(json.dumps(response), encoding="utf-8")
    return submit(workspace, audit_id, Path(path)), path


def review_unknown(response, packet):
    for item in response["items"]:
        for judgment in item["judgments"]:
            judgment.update(
                status="unknown", reason="The supplied evidence does not establish this conclusion."
            )


def diagnose_failure(response, packet):
    for item, unit in zip(response["items"], packet["units"], strict=True):
        tool_flag = next(flag for flag in unit["flags"] if flag["kind"] == "tool_failure")
        item.update(
            status="reviewed",
            summary="Weather retrieval failed and did not recover.",
            findings=[
                {
                    "key": "weather-timeout",
                    "basis": "runtime_only",
                    "kind": "tool_failure",
                    "title": "Weather requests time out",
                    "observation": "The weather tool returned a timeout and no result.",
                    "expected_behavior": "Return weather data or communicate inability.",
                    "authority": "User request in the trace",
                    "severity": "high",
                    "confidence": 0.9,
                    "evidence": tool_flag["evidence"],
                    "hypothesis": "Investigate upstream timeout handling before changing retries.",
                    "improvement": False,
                    "correlation_rationale": None,
                }
            ],
        )
        item["flag_dispositions"] = [
            {
                "flag_id": flag["id"],
                "status": "finding" if flag == tool_flag else "insufficient_evidence",
                "reason": "Observed tool timeout"
                if flag == tool_flag
                else "Insufficient evidence of a separate problem",
                "finding_keys": ["weather-timeout"] if flag == tool_flag else [],
            }
            for flag in unit["flags"]
        ]


def cluster(response, packet):
    existing = packet["existing_issues"]
    response["groups"] = [
        {
            "key": "weather-timeouts",
            "issue_id": existing[0]["issue_id"] if existing else None,
            "title": "Weather requests time out",
            "summary": "Timeouts prevent retrieval of current weather.",
            "rationale": "User goal blocked by the same evidenced tool failure.",
            "finding_ids": [unit["id"] for unit in packet["units"]],
        }
    ]


def finish(workspace, audit_id):
    respond(workspace, audit_id, "evidence", review_unknown)
    respond(workspace, audit_id, "diagnosis", diagnose_failure)
    return respond(workspace, audit_id, "clustering", cluster)


def finish_change_review(workspace):
    (workspace.root / "app.py").unlink()
    audit_id = start(
        workspace,
        mode="code",
        source=None,
        project=None,
        start_time=None,
        end_time=None,
        limit=None,
        scopes=["app.py"],
        host="test",
        model="fixture",
        code_scope="changes",
    )["audit_id"]
    respond(workspace, audit_id, "evidence", review_unknown)

    def diagnose(response, packet):
        for item, unit in zip(response["items"], packet["units"], strict=True):
            item.update(status="reviewed", summary="The obsolete weather stub was removed.")
            item["findings"] = [
                {
                    "key": key,
                    "basis": "implementation_only",
                    "kind": kind,
                    "title": title,
                    "observation": "The change removes the previous public weather stub.",
                    "expected_behavior": "Callers should use the replacement entry point.",
                    "authority": "Captured removal of the weather function.",
                    "severity": "low",
                    "confidence": 0.8,
                    "evidence": [unit["id"]],
                    "hypothesis": action,
                    "improvement": True,
                    "correlation_rationale": None,
                }
                for key, kind, title, action in [
                    (
                        "migration-guide",
                        "correctness",
                        "Document the replacement API",
                        "Add an example showing the replacement entry point for weather callers.",
                    ),
                    (
                        "removed-api-eval",
                        "evaluation_coverage",
                        "Exercise the replacement API",
                        "Add an eval case to evals/weather.json using city=Delhi; assert a weather "
                        "result via the replacement API, because this change removes the old stub.",
                    ),
                ]
            ]

    def group(response, packet):
        response["groups"] = [
            {
                "key": "weather-migration",
                "issue_id": None,
                "title": "Complete the weather API migration",
                "summary": "Document and evaluate the replacement for the removed weather stub.",
                "rationale": "Both recommendations follow from removing the previous entry point.",
                "finding_ids": [unit["id"] for unit in packet["units"]],
            }
        ]

    respond(workspace, audit_id, "diagnosis", diagnose)
    respond(workspace, audit_id, "clustering", group)
    return audit_id
