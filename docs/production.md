# Recursive self-improvement

Agentagon connects two loops: improve application agents using evidence, then remember outcomes to guide subsequent attempts. Improvement memory already participates in task preparation; production observations add feedback after deployment. Automatic rewriting of Agentagon's own workflows or evaluation gates is outside this release.

## Start with an assessment

Open any local folder, or clone a repository only when you need a new checkout. A GitHub remote is optional. Configure the managed Codex or Claude backend, optionally connect Braintrust, LangSmith or Langfuse, or import a supported trace. Save setup and select **Analyze project**. The default selection covers seven days and at most 100 completed traces within provider bounds.

Assessment runs as a task. It scans application code, discovers trace identities, proposes bindings, groups supported issues and recommends next actions. Code-only and trace-only results remain useful. Missing authentication preserves partial results. Suggestions require confirmation; a trace name alone does not authorize source changes. A confirmed trace-only agent needs code ownership before Fix.

## Keep an improvement history

Every verified Fix candidate and Optimize finalist gets an improvement record pointing to existing engine evidence. Failed and dominated attempts remain in Activity and improvement memory. Broader goals remain optional for trace-driven Fix.

Evaluation, candidate selection, deployment, and production outcome are separate facts. Select a verified candidate before recording its release, environment, actual revision and deployment time. The deployment entry is a user declaration. Trace revision metadata supports release matching; it is not proof that an untested revision is equivalent to the tested candidate. A merge is not deployment evidence. An explicit trace release and exact tested revision can also retain a trace-reported deployment link. Its first observation does not establish deployment time; record that time explicitly before a before/after comparison.

## Enable production monitoring

Open an agent's **Production** tab after confirming its provider selector. Choose the environment, collection limits, and measurements. Connecting a provider alone does not authorize monitoring. Pause a monitor to stop automatic collection. **Analyze now** starts an explicit observation task through the same runtime.

Defaults are hourly checks, 100 traces per acquisition, seven days maximum catch-up, and a 1 GiB project evidence budget. Scheduled brain diagnosis occurs at most daily for new evidence with a five-minute task budget. Explicit tasks retain their submitted limits. Reaching the storage limit pauses acquisition; existing evidence is not automatically deleted.

The detached service continues when the dashboard or MCP disconnects. It cannot collect while the machine sleeps or the service is stopped. Last-check timestamps, stale coverage and catch-up gaps remain visible. Restart restores schedules but does not resume interrupted tasks. Resume or discard a pending interrupted observation before another check can run. Provider failures back off; missing credentials and budget limits require attention.

## Interpret results

Accept a versioned measurement definition to classify outcomes. It specifies a population, metric, aggregation, direction, material-change threshold, current/reference windows, minimum samples, and required coverage. Changes start a new comparison series. Without accepted criteria, observations remain descriptive.

Supported metrics are root-trace latency, reported root cost, explicit root failure rate, explicitly reviewed issue recurrence, and a supplied numeric quality score identified by an accepted metadata key. Unreviewed recurrence is unknown; changed trace evidence invalidates an earlier review. Agentagon does not infer task success from missing errors, add parent and child cost totals together, or fabricate missing labels. Quality scores must be prepared/calibrated by the application; arbitrary text is never a numeric quality score.

Comparisons match declared environment and exact trace-reported revisions. Mixed or missing versions are **Not comparable**. Missing baseline, incomplete window coverage, partial acquisition, unknown scores, limited traffic or wide uncertainty produce **Insufficient evidence**. Numerical means and p95 estimates use deterministic bootstrap intervals; binary means use Wilson intervals. These descriptive intervals do not establish causation or account for correlated production traffic. Improved, regressed and no-material-change labels require separation or equivalence under the accepted effect threshold.

A production assessment does not silently close an issue or replace offline verification. No failures observed in a sample is not proof of universal recovery. Monitoring continues after a change so subsequent regressions can be investigated.

## Learn from outcomes

Tasks freeze recalled improvement-entry IDs and versions. The managed brain can report which supplied lessons it used or rejected and why. Outcomes retain evidence references and uncertainty. Material production changes add versioned feedback; unchanged checks do not write repeated lessons or alerts. Memory failures are retryable and never invalidate verified execution evidence. Target-agent memory remains separately bound and frozen during evaluation.

Dashboard and MCP expose the same onboarding, recommendations, improvements, deployments, monitors, observations and tasks. See the [MCP contract](mcp.md). Fresh state is required; incompatible state is rejected without rewriting private data.
