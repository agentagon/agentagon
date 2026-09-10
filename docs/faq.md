# Frequently asked questions

## Is Agentagon a model or an agent framework?

No. It is a CLI and coding-host plugin for reviewing and improving an existing agent application. Your Codex or Claude Code session provides reasoning and code changes. Agentagon captures evidence, executes declared checks, and retains comparisons.

It does not replace your application’s agent framework, production runtime, or trace collector.

## Do I need an Agentagon account or API key?

Core audit, review, evaluation, fix, and dashboard workflows need no Agentagon account or API key. Optional Intelligence requires separately issued access. Your coding host, benchmark model calls, and connected providers have their own access requirements.

## What will it cost to run?

Your host’s model usage, model calls made by your benchmark, remote compute, and optional services can incur costs under those providers’ terms. Candidate/trial/time limits bound work; they are not a monetary spending cap. Choose a small profile and understand benchmark calls before scaling up.

These docs do not quote a subscription or service price. Ask about separately issued Intelligence access through the contact in its guide.

## Can I use it on a project without Git?

Yes for a full audit, including a current-code audit. Changes review needs Git. Evaluation and fix workflows require a clean committed Git checkout because they compare isolated source snapshots.

## Will it modify my application during an audit?

The audit creates private `.agentagon/` state and, in Git repositories, a local exclude entry. It does not implement application changes or run a new evaluation suite. A fix workflow creates candidate edits in isolated worktrees; selection does not automatically apply them to the origin checkout.

## Can it audit prompts as well as code?

It can inspect permitted textual prompt files and prompt construction in the selected application source. Conclusions depend on the evidence and coverage. To establish a prompt improvement, define representative cases and measure it through an evaluation and fix run.

## Do I need production traces?

No. Start with code-only audit or review. Traces can supply runtime evidence when you have compatible exports and permission to inspect them. Selected traces are a sample; they do not automatically establish production-wide rates.

## Is it fully local or offline?

The bundled synthetic example needs no network after installation. Evidence is stored locally, but agent-led reasoning uses your coding host’s model service. Benchmarks and optional providers may use the network. Product telemetry is enabled by default and can be disabled. See [Privacy and data](privacy.md).

## Does it keep running when I close the coding agent?

There is no independent reasoning supervisor. Work requiring the host waits for it. Saved reservations and evidence support resumption. Native continuation hooks depend on the host’s capabilities and trust settings; unsupported callbacks require manual resume.

## Does a verified candidate mean the application is safe to deploy?

It means the candidate passed its frozen checks and hard constraints and received independent review tied to that source and evidence. That claim applies to the tested cases. Your normal review, release, production validation, and monitoring still determine deployment readiness.

## Why does the dashboard retain failed candidates?

They explain what was tried, what failed, and what was dominated by another result. Removing them would hide experiment history and make recovery or tradeoff analysis harder. Retained attempts are not all verified alternatives.

## Can I change the benchmark halfway through?

A comparison uses a frozen evaluation. Create a new evaluation draft or run when changing protected benchmark files, inputs, or objective definitions. You can change supported future search policy without rewriting the evaluator or past results.

## Can I use an existing test suite?

Yes. Evaluation preparation inspects existing tests and permitted data. The benchmark still needs declared checks, expected behavior, useful metrics, sensitivity evidence, and independent review. Existing test success alone does not define whether a proposed optimization improved the goal.

## Can it choose, merge, and deploy the best fix automatically?

You choose among verified alternatives. Agentagon can prepare the selected change and publish a draft PR when you ask. Selection, publication, merge, deployment, and issue resolution are separate actions. The shipping workflow does not merge or deploy.

## Where should I start?

[Install Agentagon](getting-started/install.md), then follow [Your first audit](getting-started/first-audit.md). If you want to inspect the local evidence format first, use the [synthetic example](getting-started/local-example.md).
