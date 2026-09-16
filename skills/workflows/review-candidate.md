# Independently review a candidate

Input: exact source diff, frozen evaluator, permitted changes, measured component scores and required guardrails.

Use a separate reviewer session. Inspect evaluator tampering, scope violations, correctness risks and whether cited measurements belong to this exact source. Return the existing review contract and supporting evidence. The engine applies eligibility and ranking; a review cannot replace measurement.

Explain tradeoffs among independently verified finalists. Include the unchanged baseline for comparison, show unknowns, and return fewer candidates when fewer qualify. Prior focus checks and affected-agent checks remain mandatory for every candidate presented as verified. Preserve failed attempts as evidence.
