# Agentagon Intelligence

<span id="optional-intelligence"></span>

Agentagon Intelligence adds curated suggestions to audits, evaluation preparation and measured fixes. It is optional: every workflow remains usable when access is missing or the service is unavailable.

Your coding agent handles the lookups and prepares privacy-safe summaries. Suggestions help guide its work; local evidence and verification still determine the result.

## Start with your coding agent

After obtaining access, use **ag:setup** in Codex or `/ag:setup` in Claude Code. Supply the issued service origin and the name of the environment variable containing your key; keep the key itself outside chat.

Once configured, **ag:audit**, **ag:eval**, and **ag:fix** consult Intelligence as part of their workflows. You do not need to prepare request files or run lookup commands yourself. Describe what you want investigated or improved in your normal request.

## Configure access

Request access through [hello@agentagon.ai](mailto:hello@agentagon.ai), then ask **ag:setup** to configure it using the issued service origin and your key’s environment-variable name. Keep the key itself outside chat and `.agentagon/`.

??? details "Direct CLI configuration"

    Request access through [hello@agentagon.ai](mailto:hello@agentagon.ai). The documented hosted origin is `https://brain.agentagon.ai`; configure the exact issued origin explicitly. Keep the issued key in an environment variable or credential store. Save its environment-variable name with `agentagon setup --scope user --set intelligence.api_key_env AGENTAGON_API_KEY` and the service origin with `--set intelligence.endpoint https://brain.agentagon.ai`. Project-scoped values can override user defaults in the same config file. There is no implicit service address. Do not paste secrets into coding-agent conversations or store them in `.agentagon/`.

    `agentagon status` reports whether the key and endpoint are configured without revealing their values. Missing configuration leaves audits, evaluation preparation and fixes available locally. `agentagon setup --scope user --set intelligence.access_presented true` records that optional access was discussed.

## Use during a workflow

The coding agent sends an abstract summary of the application and the question being investigated. It excludes raw code, traces, credentials, customer details, and protected evaluation material. It may consult Intelligence again when later evidence makes a follow-up useful.

Guidance is advisory. It cannot change your permissions, benchmark, constraints, or execution limits. If the service is unavailable, the workflow continues locally.

??? details "Request preparation and direct lookup commands"

    The coding agent prepares separate short UTF-8 files in ignored `.agentagon/` storage. For audit, put project purpose, workflow, broad constraints and high-level available evidence in `context`, and a specific user ask or abstract investigation objective in `focus`. For evaluation, use `context` for applicability and constraints and `goal` for the evaluation question. For a fix, use required `focus` for the known issue and optional `context` for applicability. The CLI reads only files explicitly supplied to lookup. Never upload a saved raw audit, evaluation or fix goal: prepare a privacy-safe summary first.

    Exclude raw code/traces, personal information, customer identifiers, private URLs, local paths, credentials and protected evaluation material from both files. For evaluation preparation, protected material includes plans, fixtures, cases, ground truth, private inputs and exact acceptance details. For a fix, exclude the frozen evaluator and candidate-specific private material. Generalize proprietary details. Pattern redaction is additional protection and does not guarantee complete removal of sensitive content.

    ```sh
    agentagon --workspace CHECKOUT audit lookup AUDIT_ID --context-file CONTEXT_FILE --focus-file FOCUS_FILE --phase initial --limit 5
    agentagon --workspace CHECKOUT eval lookup EVALUATION_ID --context-file CONTEXT_FILE --goal-file GOAL_FILE --phase initial --limit 5
    agentagon --workspace CHECKOUT fix lookup RUN_ID --context-file CONTEXT_FILE --focus-file FOCUS_FILE --phase initial --limit 5
    ```

    The ID selects the local owner and the client selects the matching service path: `/v1/audit`, `/v1/eval` or `/v1/fix`. The endpoints share a response contract; audit accepts `context` and/or `focus`, evaluation accepts `context` and/or `goal`, and fix requires `focus` with optional `context`. The fix coordinator owns lookups for a run; do not invoke one for each candidate or reviewer. Each workflow makes at most two automatic logical lookups for its owner: an initial request and, when useful, one distinct `--phase follow_up` request after later evidence or measurements. Identical completed requests for the same owner, endpoint and phase reuse receipts; changing a supplied field creates a distinct request. `--refresh` explicitly repeats a completed request. Requests are not automatically retried. See the [shared skill reference](../skills/audit/references/intelligence.md) for preparation and workflow rules.

## Client contract

These implementation details are handled by the coding agent and CLI.

??? details "API fields, limits, and saved receipts"

    The request uses a bearer key. Audit and fix use `focus`; evaluation uses `goal`:

    ```json
    {
      "context": "Document Q&A using retrieval, an LLM and caching. Preserve answer quality and access isolation.",
      "focus": "Reduce response latency.",
      "limit": 5
    }
    ```

    ```json
    {
      "context": "Document Q&A using retrieval, an LLM and caching. Preserve answer quality and access isolation.",
      "goal": "Measure whether the agent handles unsupported questions safely.",
      "limit": 5
    }
    ```

    Audit requires `context` or `focus`. Fix requires `focus` and accepts optional `context`. Evaluation requires `context` or `goal` and rejects `focus`. Any supplied field must be nonblank UTF-8 text; the relevant fields together must be at most 4,000 characters before and after redaction. Omit unused fields. The previous `query` request field and `--query-file` option are not accepted. A positive integer `limit` defaults to five and values above five are capped at five by the client. The wire contract permits zero to five distinct suggestions, each with exactly `id`, `title` and `suggestion`; the current fix and evaluation routes return at most one strongest qualifying recipe or procedure. Audit guidance may return up to five. The response also contains an opaque `knowledge_version`. An empty successful response means no guidance qualified.

    Configure an origin without credentials, a path, query, or fragment. HTTPS is required except for loopback development. The client appends the workflow path, enforces a 30-second total request deadline, declines redirects, bounds response size, and validates responses. Missing keys or endpoints, rejected keys, rate limits, timeouts, service failures and invalid responses do not prevent the owning workflow. Invalid endpoint syntax produces a configuration error. A lookup reports a busy evaluation or fix owner promptly instead of waiting for its active operation. Requests are not automatically retried.

    Receipts retain redacted request fields, responses, suggestion IDs, versions, timestamps and status in local `.agentagon/` artifacts. Each receipt is attached to its audit, evaluation preparation or fix run; completed requests are cached per owner, endpoint and phase. Reports identify consulted versions and suggestions where that workflow exposes them. Guidance requires local evidence before it can support a finding, cannot define evaluation ground truth by itself, and cannot override permissions, fixed rubrics, frozen evaluators, hard constraints or execution budgets.

    Use `agentagon eval status EVALUATION_ID` or `agentagon status --run RUN_ID` to inspect the owner-linked receipt index (`intelligence` on evaluation state and `intelligence_receipts` on fix status). Audit reports retain their guidance summary; fix reports and the dashboard keep guidance payloads private. Do not hand-edit receipts or canonical workflow state.

    [Product telemetry](telemetry.md) is enabled by default and can be disabled. It records lookup outcomes and returned knowledge IDs without context, focus, goal or local references for all three workflows. Cached rereads do not repeat returned-entry events. The `knowledge_investigated` and `knowledge_cited` stages remain audit-only because they refer to local audit evidence and saved findings.

## Data processing

Agentagon Intelligence uses the redacted request to provide guidance. We do not retain your request data on our servers.
