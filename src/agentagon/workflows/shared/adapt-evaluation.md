# Reuse or prepare a native evaluation

Input: accepted measurement design, evaluator inventory, selected dataset and execution budget.

Inspect the existing entrypoint, assertions, scorer and dependencies before recommending reuse. Keep the user's native framework and command where suitable. Discovery identifies source evidence, not execution readiness. Show proposed command, dataset version, scorer and output mapping before execution. Native SDK commands may upload results; honor the accepted execution settings and publication scope.

Use the accepted native plan and generated dataset loader when provided. Braintrust and DeepEval exports are starting inputs; missing expectations, unsupported conversation/tool state and attachments need explicit handling. Never copy observed output into expected output. Keep private inputs outside source delivery.

Adapt numeric results to `AGENTAGON_RESULT_PATH` as `{"metrics": {...}}`; passing a command alone proves only its declared checks. Preserve metric units, directions, weights and gates from the accepted design. Implement an instrumented evaluator when trajectory measurements lack imported evidence.

Complete [the shared authoring procedure](../eval/references/authoring.md) and [independent evaluation review](review-evaluation.md). Changes to accepted scoring require a new proposal; changes to executable evaluation definitions require a new evaluator version.
