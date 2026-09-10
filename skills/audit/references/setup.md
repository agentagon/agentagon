# CLI prerequisites

Requires Python 3.12+ and a coding-agent CLI with native plugin support. Full audits do not require Git; changes reviews, evaluation and fix workflows do. If Agentagon is missing, install from a trusted Agentagon source directory with `bash scripts/install.sh`; select hosts with `--host codex` or `--host claude-code`. A supplied wheel uses `--source /path/to/agentagon.whl`. Restart the host session to refresh plugin discovery.

If the host cannot execute the CLI, report the prerequisite; do not claim a completed audit.
