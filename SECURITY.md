# Security policy

## Report a vulnerability privately

Email [security@agentagon.ai](mailto:security@agentagon.ai) with suspected vulnerabilities in this repository, its packaged CLI, plugins or dashboard. Do not disclose exploit details through a public issue or pull request.

Include enough information to reproduce and assess the problem:

- Agentagon version (`agentagon --version`) and source commit, if known.
- Affected component, operating system, coding host and runner configuration.
- A minimal reproduction using synthetic inputs, the observed impact and any required permissions.
- Relevant redacted output and whether the problem reproduces with a local runner.

Do not send credentials, private application source, raw customer traces or a complete `.agentagon/` directory. Preserve relevant evidence locally and describe what it contains. Test only systems and data you are authorized to use.

## Version and response expectations

This policy does not promise a response deadline or a maintenance window for any release. Include the exact version or commit affected; do not assume that a fix applies to every previous version.

Use ordinary bug reports for problems that do not expose data, bypass permissions or create a security risk. Community conduct reports use the process in [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Relevant security boundaries

Agentagon stores audit and experiment evidence locally. That storage can contain sensitive application data. Credential redaction recognizes common fields and patterns; it is not a general detector for secrets or personal data.

Local candidate worktrees isolate files, not processes or network access. The coding host and configured execution services determine where code runs and where permitted model inputs are processed.

The dashboard binds to loopback, checks the exact host, and enables controls only when requested. See the [architecture guide](docs/contributing/architecture.md), [runner boundaries](docs/fix.md#configure-execution-once), [data handling](docs/audit.md#coverage-and-data-handling) and [optional Intelligence](docs/intelligence.md) for implementation details and limits.
