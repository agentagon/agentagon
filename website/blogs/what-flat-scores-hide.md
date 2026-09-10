---
title: What flat scores hide
description: A stable evaluation score can conceal a regression. Inspect the cases behind the number before deciding a change is ready.
author: Gursharan Singh
published_at: 2026-09-07
updated_at: 2026-09-10
original_url: /field-notes/what-flat-scores-hide/
---

The release that deserves a second look is not always the one with a lower score. Sometimes an improvement on a common path cancels out a regression on a smaller, more consequential one.

An aggregate score is useful evidence about the cases it summarizes. It cannot explain every decision inside those cases, or tell you what happened on paths the evaluation never exercised.

## The number can stay still while behavior moves

Imagine changing an agent’s prompt to resolve customer requests with fewer tool calls. The agent becomes faster on straightforward requests. It also starts answering some ambiguous requests before checking the information it needs.

The overall result may look similar. The operational change is substantial: the agent now makes a different decision about when evidence is sufficient.

The useful review question is: **which cases improved, which regressed, and what did the agent actually do differently?**

## A refund example

Consider an illustrative support agent handling a refund outside the normal policy window. The customer is frustrated and threatens a chargeback. The agent can retrieve the policy, ask for missing information, or escalate.

A candidate prompt encourages quicker resolution. When the policy lookup is inconclusive, the agent begins treating customer urgency as enough reason to promise a refund. A baseline that previously escalated would have made a different decision.

The failure is visible in the sequence: the lookup did not establish eligibility, but the final answer promised an outcome anyway. A score that mostly reflects routine, eligible refunds may barely register that shift.

This is an example of a failure mode to test, not a measured result from an Agentagon customer.

## Turn the concern into a comparison

Start by inspecting the changed code and any relevant execution traces. In Agentagon, an [audit](/docs/audit/) can record findings and evidence; a [review](/docs/review/) focuses on captured changes.

Then prepare an [evaluation](/docs/eval/) that exercises the concern. For the refund example, include missing policy information, a failed lookup, an ineligible request, and a request with strong pressure cues. Keep ordinary eligible requests in the set too: fixing the edge case should preserve the common path.

Freeze the benchmark before comparing candidates. Measure baseline and candidate against the same cases, checks, and objectives. Inspect failed and errored executions alongside successful ones, and record gaps instead of treating them as passes.

## Read the evidence before choosing

The [dashboard](/docs/dashboard/) brings saved findings and comparisons together. Use it to inspect the changed source, measurements, and review evidence. A faster candidate that degrades an important behavior may be a tradeoff you reject.

Agentagon preserves verified alternatives so you can [choose a result](/docs/delivery/). Selection prepares a reviewable branch; publishing, merging, and deploying remain separate decisions.

The score tells you where to begin. The cases, traces, and coverage tell you what the result supports.
