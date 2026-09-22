# Prepare the selected result for delivery

Input: user-selected verified candidate, exact branch/source identity and retained review/measurement evidence.

Prepare a readable diff and PR description explaining the problem, behavior change, component measurements, required checks and remaining limits. Revalidate the selected candidate's suite evidence and protect evaluator files. Exclude private datasets, traces, credentials and internal artifacts.

Create a draft PR only after the explicit publication action with a reviewed destination and base. Selection and local delivery do not imply publication. Merge and deployment remain separate. Use the existing [delivery procedure](../optimize/references/delivery.md) for engine commands and exact-parent rules.
