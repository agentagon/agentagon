# Analyze evidence for a goal

Input: confirmed code scope, selected immutable evidence and desired behavior.

Inspect bounded representative successes, failures and boundary cases. Cite source files or snapshot/trace IDs for every observation. Preserve root-trace grouping, selection bounds, missing children and source-revision uncertainty. A failure-filtered sample cannot establish a production-wide failure rate.

Output: relevant behaviors, supported failure patterns, representative examples, contradictory evidence, and instrumentation/label gaps. Keep hypotheses distinguishable from observations. Save this analysis in the measurement proposal's evidence/background/limitations; the user reviews it before acceptance. Refresh creates a new proposal and preserves the old accepted evidence.

During baseline preparation, apply the agreed metrics to this population separately from controlled benchmark cases. During optimization, return fresh candidate-specific execution evidence. Reserved final examples must never enter search feedback.
