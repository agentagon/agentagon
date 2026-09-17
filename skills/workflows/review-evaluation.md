# Independently review an evaluation

Input: exact evaluator source, accepted measurement definition, validation and sensitivity evidence.

Use a separate reviewer session. Verify the accepted behavior-to-check mapping, label authority, missing-data policy, scoring direction/scales and source protections. Known incorrect behavior must fail for the intended reason; a crashing harness is not useful sensitivity evidence. Inspect whether trajectory checks observe actual events and whether metric discrimination matches known variants.

Return the existing evaluation-review contract with evidence and unresolved limitations. Do not author the implementation or invent execution receipts. The engine decides whether the evaluator can freeze.
