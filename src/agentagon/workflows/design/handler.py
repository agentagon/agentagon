"""Propose a measurement design for explicit acceptance."""


def accept(workspace, job, raw_result):
    from agentagon.workflows.evaluate.designs import validate

    if raw_result.get("needs_input") or not raw_result.get("measurement_design"):
        question = str(
            raw_result.get("needs_input") or "Continue to propose measurements for this goal."
        )
        return "needs_input", {"needs_input": question}, question
    proposal = validate(raw_result["measurement_design"])
    return (
        "completed",
        {
            "summary": str(raw_result.get("summary", "Measurement proposal ready for review.")),
            "measurement_design": proposal,
        },
        None,
    )
