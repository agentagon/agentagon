# Prepare a release

Release approved `vX.Y.Z` tags from protected `main` using the [release workflow](../../.github/workflows/release.yml). It builds one wheel and source archive, stages their hashes in a draft GitHub release, publishes those files to PyPI through Trusted Publishing, and then publishes the GitHub release.

## One-time maintainer setup

Configure PyPI's pending Trusted Publisher for project `agentagon`, owner/repository `agentagon/agentagon`, workflow `release.yml`, environment `pypi`. Verify the repository identity after a rename or replacement. A pending publisher does not reserve the PyPI name; verify ownership after the first successful upload.

Create the GitHub `pypi` environment with a required maintainer reviewer and tag deployment policy `v*`. A sole maintainer must be allowed to approve their own deployment. Keep repository Actions tokens read-only by default. Only staging/GitHub publication jobs receive `contents: write`; only the PyPI job receives `id-token: write`. No persistent PyPI API token is needed.

Protect `main` against force pushes and deletion and require the always-running test matrix, browser, source-package, secret, dependency and CodeQL checks. Require approval of workflows from outside contributors. Enable GitHub secret scanning, push protection, dependency graph, Dependabot alerts/security updates, and code scanning. Weekly security scans resolve the currently allowed dependency versions; Dependabot proposes Python and Action updates.

## 1. Select and review the source

Choose the exact commit for release. Review changes since the previous release and prepare notes covering behavior, compatibility and known limitations. Keep the release checkout free of unrelated work and generated files. See [CONTRIBUTING.md](../../CONTRIBUTING.md) for checks and commit guidance.

Preserve [LICENSE](../../LICENSE). For new third-party material, retain its applicable license and attribution. The [Code of Conduct](../../CODE_OF_CONDUCT.md) carries its own Contributor Covenant attribution; do not replace it with the software license.

Before the first public release, confirm that the reporting address in [SECURITY.md](../../SECURITY.md) and the Code of Conduct receives mail. Publishing an address does not configure its mailbox or forwarding.

Configure telemetry following [maintainer setup](../telemetry.md#maintainer-setup). Verify the privacy settings and synthetic events from the installed wheel before release. Do not mistake a mocked test or HTTP acceptance for verified ingestion; record the settings and live check in release evidence. Personal and secret API keys must never be bundled.

## 2. Keep version declarations aligned

Update the release version in these four places together:

| File | Field |
|---|---|
| [pyproject.toml](../../pyproject.toml) | `project.version` |
| [src/agentagon/__init__.py](../../src/agentagon/__init__.py) | `__version__`, shown by `agentagon --version` |
| [.codex-plugin/plugin.json](../../.codex-plugin/plugin.json) | `version` |
| [.claude-plugin/plugin.json](../../.claude-plugin/plugin.json) | `version` |

The installer derives a native plugin cache version from the Codex manifest's base version and the bundled content hash. It writes a `+codex.HASH` suffix into the installed manifests. Do not paste that generated suffix back into the source release version. See [_sync_bundle()](../../src/agentagon/installation.py) and its [cache-version test](../../tests/test_installation.py).

Check consistency from the repository root, substituting the intended release tag:

```sh
python scripts/release.py version --tag v0.1.0
```

The workflow rejects mismatched declarations, tags outside `main`, unprotected `main`, and missing or failed push checks on the exact tagged SHA. Let the `main` checks finish before creating the tag.

## 3. Review compatibility and package contents

Package versions are separate from the contract and rubric versions in [core/records.py](../../src/agentagon/core/records.py), [contracts/v1/](../../contracts/v1/) and [signals/audit-v1.json](../../signals/audit-v1.json). Do not change these identifiers as a routine release bump.

For record changes, inspect readers, writers, schemas and retained-data tests together. Document migration requirements or explicit incompatibilities; do not claim automatic migration without an implementation. Preserve the Python 3.10 boundary for the remote worker and copyable event helper.

[pyproject.toml](../../pyproject.toml) declares wheel resources. [MANIFEST.in](../../MANIFEST.in) declares additional source-archive contents. New skills, contracts, helpers, hooks and dashboard assets must be available after installation, not merely from a source checkout.

Root community files and `.github/` templates serve repository visitors. Do not assume every repository file is included in the wheel or source archive; inspect the actual distributions if their inclusion is required.

## 4. Build and validate the artifacts

Run the [full checks](../../CONTRIBUTING.md#run-checks-before-submitting), including `python -m build`, and the [browser checks](README.md#browser-checks). Use a fresh checkout or a build directory without artifacts from previous versions so wheel globs select the intended release.

Follow the [packaging checks](README.md#packaging-checks) to install the wheel in a fresh environment outside the checkout. Run both `scripts/check_installed.py` and the local audit example there. Record the exact source commit, artifact filenames, environment and results.

These checks cover installed resources and local workflows. Record native-host and live SSH/E2B skips separately; only run those integrations with authorization. If plugin resources or registration change, include the relevant authorized native-host verification before claiming that installation path works.

## 5. Write notes and publish deliberately

Use a release note with these sections; omit empty sections rather than inventing work:

```markdown
## Changes

- User-visible changes, with links to their pull requests.

## Compatibility and upgrade

- Python/host requirements and any configuration, contract or state changes.
- Installation or upgrade instructions for the actual distributed artifact.

## Validation

- Source commit and artifact filenames.
- Commands, environments and results; list skipped or unverified integrations.

## Known limitations

- Remaining issues and operational limits relevant to this release.
```

Write `docs/releases/X.Y.Z.md` before tagging the approved revision. Create and push `vX.Y.Z` only when publication is authorized. Approve the waiting `pypi` environment deployment after inspecting the draft release and `SHA256SUMS`.

After publication, download both destinations' artifacts and compare hashes. Install from PyPI in a fresh environment outside the checkout; check `agentagon --version`, `agentagon resources`, and the synthetic example. Record the public tag/commit, actual checks and live telemetry evidence in the release notes.

## Recover a partial publication

Use **Re-run failed jobs** in the original Actions run first. Its immutable `release-packages` artifact is retained for 90 days. The draft GitHub release keeps the same wheel, source archive, notes and `SHA256SUMS` beyond that retention window. Never rebuild or replace files for a version already uploaded to PyPI.

If all draft assets were staged successfully, dispatch `Release` with the same existing tag to resume. It downloads the staged files, verifies their hashes, and uploads only files missing on PyPI. A hash mismatch fails closed. Existing GitHub assets are byte-compared and never overwritten.

If staging was interrupted before every asset reached GitHub, rerun the failed staging job from the original run so it uses the original build artifact. A new dispatch intentionally fails when the draft is incomplete; do not delete the draft to force a rebuild. If the original files cannot be recovered, investigate and publish a new version instead of reusing the old version.
