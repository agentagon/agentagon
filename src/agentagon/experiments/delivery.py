"""Prepare and publish the exact selected, verified experiment as a draft PR."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from agentagon.core.records import AuditError, digest, identifier, now
from agentagon.experiments import checkouts, engine, evaluation, store
from agentagon.storage.workspace import Workspace


def _command(
    root: Path, argv: list[str], *, missing: bool = False, binary: bool = False
) -> str | bytes:
    try:
        result = subprocess.run(
            argv,
            cwd=root,
            capture_output=True,
            text=not binary,
            check=False,
            timeout=60,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"},
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        # Timeout exceptions retain argv, which may include an authenticated remote URL.
        raise AuditError(
            "delivery command unavailable or interrupted; retry to reconcile"
        ) from None
    if result.returncode and not (missing and result.returncode == 2):
        # Git and gh diagnostics can contain authenticated URLs or private server responses.
        raise AuditError("delivery command failed; check Git/GitHub access and retry to reconcile")
    return result.stdout if binary else result.stdout.rstrip("\n")


def _git(root: Path, *args: str, missing: bool = False, binary: bool = False) -> str | bytes:
    return _command(
        root,
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        missing=missing,
        binary=binary,
    )


def _selected(workspace: Workspace, data: dict, candidate_id: str | None) -> dict:
    selected = data.get("selected")
    if not selected or candidate_id and candidate_id != selected["candidate_id"]:
        raise AuditError("ship requires the candidate already chosen with fix select")
    candidate = data["candidates"].get(selected["candidate_id"])
    if not candidate:
        raise AuditError("selected candidate is missing from the run")
    engine._verified_evidence(workspace, data, candidate)
    if candidate["candidate_id"] not in evaluation.frontier(data):
        raise AuditError("selected candidate is no longer on the verified Pareto frontier")
    branch = f"codex/ag-fix-{data['run_id']}-{candidate['candidate_id']}"
    if selected != {
        "candidate_id": candidate["candidate_id"],
        "branch": branch,
        "source_revision": candidate["source_revision"],
    }:
        raise AuditError("selection no longer identifies the exact verified branch")
    actual = _git(workspace.root, "rev-parse", "--verify", f"refs/heads/{branch}")
    if actual != candidate["source_revision"]:
        raise AuditError(
            "selected branch changed; restore or verify the new source before shipping"
        )
    return candidate


def _destination(workspace: Workspace, remote: str, base: str | None) -> tuple[dict, str]:
    if not isinstance(remote, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", remote):
        raise AuditError("ship remote must be one configured Git remote name")
    fetch = _git(workspace.root, "remote", "get-url", "--all", remote).splitlines()
    push = _git(workspace.root, "remote", "get-url", "--push", "--all", remote).splitlines()
    if len(fetch) != 1 or len(push) != 1 or fetch != push or not push[0]:
        raise AuditError("ship requires one unambiguous remote with matching fetch and push URLs")
    if base is None:
        try:
            target = _git(workspace.root, "symbolic-ref", f"refs/remotes/{remote}/HEAD")
        except AuditError as exc:
            raise AuditError(
                "remote default branch is unavailable; supply an explicit base branch"
            ) from exc
        prefix = f"refs/remotes/{remote}/"
        if not target.startswith(prefix):
            raise AuditError("remote default branch is unavailable; supply an explicit base branch")
        base = target[len(prefix) :]
    if not isinstance(base, str) or base.startswith("-") or "\x00" in base:
        raise AuditError("ship base must be a Git branch name")
    _git(workspace.root, "check-ref-format", f"refs/heads/{base}")
    revision = _git(workspace.root, "rev-parse", "--verify", f"refs/remotes/{remote}/{base}")
    return {
        "remote": remote,
        "remote_digest": digest(push[0]),
        "base": base,
        "base_revision": revision,
    }, push[0]


def _repository(url: str) -> str:
    """Resolve the configured destination without accepting local paths as GitHub repos."""
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise AuditError("publication remote is not a valid GitHub URL") from exc
    if parsed.scheme in {"https", "ssh"} and parsed.hostname:
        if parsed.query or parsed.fragment:
            raise AuditError("publication remote must not contain a query or fragment")
        host, path = parsed.hostname, parsed.path.lstrip("/")
    else:
        match = re.fullmatch(r"git@([A-Za-z0-9.-]+):(.+)", url)
        if not match:
            raise AuditError("publication requires a GitHub HTTPS or SSH remote")
        host, path = match.groups()
    if path.endswith(".git"):
        path = path[:-4]
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path
    ):
        raise AuditError("publication remote does not identify one GitHub repository")
    return f"{host}/{path}"


def _remote_revision(workspace: Workspace, url: str, branch: str) -> str | None:
    ref = f"refs/heads/{branch}"
    output = _git(workspace.root, "ls-remote", "--exit-code", "--refs", url, ref, missing=True)
    if not output:
        return None
    rows = [line.split("\t") for line in output.splitlines()]
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != ref:
        raise AuditError("remote branch identity is ambiguous")
    return rows[0][0]


def _remote_base(workspace: Workspace, url: str, record: dict) -> None:
    if _remote_revision(workspace, url, record["base"]) != record["base_revision"]:
        raise AuditError("remote base changed; start a new fix run and verify against the new base")


def _summary(workspace: Workspace, data: dict, candidate: dict) -> dict:
    baseline = data["candidates"][data["baseline_id"]]
    metrics, variation = evaluation.aggregate(
        data["spec"],
        [
            engine._validate_result(
                data, candidate, trial, workspace.read_artifact(trial["artifact"])
            )[0]
            for trial in engine._completed(candidate)
        ],
    )
    # Only structural IDs, numeric aggregates and booleans leave the private archive.
    # Goal, hypothesis, reviewer prose, commands, environment and raw evidence stay local.
    return {
        "version": 1,
        "run_id": data["run_id"],
        "candidate_id": candidate["candidate_id"],
        "source_revision": candidate["source_revision"],
        "source_digest": candidate["source_digest"],
        "evaluation_digest": data["evaluation_digest"],
        "repetitions": data["spec"]["repetitions"],
        "metrics": {
            name: {
                "direction": definition["direction"],
                "baseline": baseline["metrics"][name],
                "candidate": metrics[name],
                "min": variation[name]["min"],
                "max": variation[name]["max"],
            }
            for name, definition in data["spec"]["metrics"].items()
        },
        "constraints": evaluation.constraints(
            data["spec"], candidate["metrics"], baseline["metrics"]
        ),
        "checks": [
            {
                "id": check["id"],
                "issue_ids": [
                    value
                    for value in check.get("issue_ids", [])
                    if re.fullmatch(r"issue_[0-9a-f]{24}", value)
                ],
            }
            for check in data["spec"]["checks"]
        ],
        "issue_ids": [
            value for value in data["issue_ids"] if re.fullmatch(r"issue_[0-9a-f]{24}", value)
        ],
        "independent_review": "pass",
        **(
            {
                "cleanup": {
                    "original_candidate_id": candidate["cleanup_of"],
                    "state": candidate["cleanup_outcome"]["state"],
                    "comparisons": candidate["cleanup_outcome"]["comparisons"],
                }
            }
            if candidate.get("cleanup_outcome")
            else {}
        ),
        "limitations": [
            "Measurements apply to the frozen evaluation and recorded execution environment.",
            "Observed ranges describe repeated samples; they are not confidence intervals.",
            "A draft PR does not apply the change, prove deployment behavior or resolve issues.",
        ],
    }


def _body(summary: dict, record: dict) -> str:
    lines = [
        "# Verified Agentagon candidate",
        "",
        "This draft proposes the selected candidate measured against the original baseline.",
        "The candidate passed its frozen checks, hard constraints and independent review.",
        "",
        "| Metric | Direction | Original baseline | Candidate | Observed candidate range |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for name, metric in summary["metrics"].items():
        lines.append(
            f"| {name} | {metric['direction']} | {metric['baseline']:g} | "
            f"{metric['candidate']:g} | {metric['min']:g}–{metric['max']:g} |"
        )
    lines += ["", f"Repetitions per candidate: {summary['repetitions']}.", "", "## Validation", ""]
    if summary.get("cleanup"):
        lines.append(
            "- Shipping cleanup was measured and independently reviewed again. "
            f"Original candidate: `{summary['cleanup']['original_candidate_id']}`."
        )
        for comparison in summary["cleanup"]["comparisons"]:
            lines.append(
                f"- Cleanup `{comparison['kind']}.{comparison['objective']}` "
                f"({comparison['direction']}): {comparison['original']:g} → "
                f"{comparison['cleanup']:g}; "
                + ("no worse." if comparison["no_worse"] else "user-selected tradeoff.")
            )
    for constraint in summary["constraints"]:
        operator = ">=" if constraint["op"] == "gte" else "<="
        lines.append(
            f"- Constraint `{constraint['metric']}`: {constraint['actual']:g} {operator} "
            f"{constraint['threshold']:g} (pass; {constraint['reference']})."
        )
    for check in summary["checks"]:
        issues = ", ".join(f"`{value}`" for value in check["issue_ids"])
        lines.append(
            f"- Check `{check['id']}` passed." + (f" Issue evidence: {issues}." if issues else "")
        )
    if summary["issue_ids"]:
        lines.append("- Associated issue records: " + ", ".join(summary["issue_ids"]) + ".")
    lines += ["- Independent review: pass.", "", "## Evidence identity", ""]
    for label in (
        "run_id",
        "candidate_id",
        "source_revision",
        "source_digest",
        "evaluation_digest",
    ):
        lines.append(f"- {label}: `{summary[label]}`")
    lines += [f"- verified base: `{record['base_revision']}`", "", "## Limits", ""]
    lines += [f"- {limit}" for limit in summary["limitations"]]
    lines += ["", f"<!-- agentagon-delivery:{record['delivery_id']} -->", ""]
    return "\n".join(lines)


def _prepare(
    workspace: Workspace, data: dict, candidate: dict, destination: dict, *, reconcile: bool
) -> dict:
    if checkouts.tree(workspace.root, data["origin_revision"]) == candidate["source_digest"]:
        raise AuditError("selected candidate has no source changes to deliver")
    _git(
        workspace.root,
        "merge-base",
        "--is-ancestor",
        data["origin_revision"],
        candidate["source_revision"],
    )
    for name in checkouts.paths(workspace.root, candidate["source_revision"]):
        path = Path(name)
        if (
            ".agentagon" in path.parts
            or path.name == ".env"
            or path.name.startswith(".env.")
            and path.name not in {".env.example", ".env.sample"}
            or path.suffix in {".pem", ".key", ".p12"}
        ):
            raise AuditError("delivery snapshot contains a private state or credential path")
    binding = {
        **destination,
        "base_revision": data["origin_revision"],
        "run_id": data["run_id"],
        "candidate_id": candidate["candidate_id"],
        "branch": data["selected"]["branch"],
        "source_revision": candidate["source_revision"],
        "source_digest": candidate["source_digest"],
        "evaluation_digest": data["evaluation_digest"],
        "inputs_digest": data["inputs_digest"],
        "profile_digest": data["profile_digest"],
    }
    delivery_id = identifier("delivery", binding)
    deliveries = data.setdefault("deliveries", {})
    record = deliveries.get(delivery_id)
    if record and (
        record.get("version") != 1 or any(record.get(k) != v for k, v in binding.items())
    ):
        raise AuditError("delivery record changed or has an unsupported version")
    if (
        destination["base_revision"] != data["origin_revision"]
        and not reconcile
        and not (record and record["state"] == "published")
    ):
        raise AuditError(
            "delivery base differs from the original evaluation base; verify a new run"
        )
    if record is None:
        record = {
            "version": 1,
            "delivery_id": delivery_id,
            **binding,
            "state": "prepared",
            "created_at": now(),
        }
        deliveries[delivery_id] = record
    directory = store.run_dir(workspace, data["run_id"]) / "deliveries" / delivery_id
    summary = _summary(workspace, data, candidate)
    diffstat = _git(
        workspace.root,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--stat",
        data["origin_revision"],
        candidate["source_revision"],
        "--",
    )
    patch = _git(
        workspace.root,
        "diff",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
        data["origin_revision"],
        candidate["source_revision"],
        "--",
        binary=True,
    )
    workspace.write(directory / "summary.json", summary)
    workspace.write_bytes(directory / "diff.patch", patch)
    workspace.write_bytes(directory / "diffstat.txt", (diffstat + "\n").encode())
    workspace.write_bytes(directory / "pull-request.md", _body(summary, record).encode())
    record["artifacts"] = {
        "summary": str((directory / "summary.json").relative_to(workspace.root)),
        "diff": str((directory / "diff.patch").relative_to(workspace.root)),
        "diffstat": str((directory / "diffstat.txt").relative_to(workspace.root)),
        "pr_body": str((directory / "pull-request.md").relative_to(workspace.root)),
    }
    _save(workspace, data, record)
    return record


def _save(workspace: Workspace, data: dict, record: dict) -> None:
    record["updated_at"] = now()
    store.save_run(workspace, data)
    workspace.write(
        store.run_dir(workspace, data["run_id"])
        / "deliveries"
        / record["delivery_id"]
        / "delivery.json",
        record,
    )


def _existing_pr(workspace: Workspace, repository: str, record: dict) -> dict | None:
    output = _command(
        workspace.root,
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repository,
            "--head",
            record["branch"],
            "--base",
            record["base"],
            "--state",
            "all",
            "--limit",
            "100",
            "--json",
            "url,number,state,isDraft,headRefOid,headRefName,baseRefName,isCrossRepository,body",
        ],
    )
    try:
        entries = json.loads(output)
    except (ValueError, TypeError) as exc:
        raise AuditError("GitHub returned an unreadable PR response; retry to reconcile") from exc
    if (
        not isinstance(entries, list)
        or len(entries) >= 100
        or any(not isinstance(entry, dict) for entry in entries)
    ):
        raise AuditError("GitHub PR history is ambiguous; inspect existing deliveries")
    entries = [entry for entry in entries if entry.get("isCrossRepository") is not True]
    if not entries:
        return None
    if len(entries) != 1:
        raise AuditError(
            "multiple PRs already use the delivery branch; inspect existing deliveries"
        )
    pr = entries[0]
    marker = f"<!-- agentagon-delivery:{record['delivery_id']} -->"
    if (
        pr.get("headRefOid") != record["source_revision"]
        or pr.get("headRefName") != record["branch"]
        or pr.get("baseRefName") != record["base"]
        or pr.get("isCrossRepository") is not False
        or not isinstance(pr.get("body"), str)
        or marker not in pr["body"]
        or type(pr.get("number")) is not int
        or pr["number"] < 1
        or pr.get("state") not in {"OPEN", "CLOSED", "MERGED"}
        or type(pr.get("isDraft")) is not bool
        or pr.get("url") != f"https://{repository}/pull/{pr['number']}"
    ):
        raise AuditError("existing PR does not match this verified delivery; it was not changed")
    return {
        "url": pr["url"],
        "number": pr["number"],
        "state": pr["state"],
        "is_draft": pr["isDraft"],
    }


def ship(
    workspace: Workspace,
    run_id: str,
    candidate_id: str | None = None,
    *,
    remote: str = "origin",
    base: str | None = None,
    publish: bool = False,
) -> dict:
    """Prepare locally; only an explicit publish request may push and create a draft PR.

    Selection must already exist. Its branch is the exact branch created by engine.select;
    this operation never checks it out, updates it, stages origin files or rebases it.
    The run lock serializes retry reconciliation and prevents admission/selection changes
    between the final evidence check and publication.
    """
    if type(publish) is not bool:
        raise AuditError("publication requires an explicit boolean publish option")
    workspace.require_initialized()
    with store.locked(workspace, run_id):
        data = store.load_run(workspace, run_id)
        if any(
            c.get("cleanup_of")
            and c.get("cleanup_of") == (data.get("selected") or {}).get("candidate_id")
            and not c.get("cleanup_outcome")
            for c in data["candidates"].values()
        ):
            raise AuditError(
                "finish cleanup verification and comparison before preparing or publishing delivery"
            )
        candidate = _selected(workspace, data, candidate_id)
        destination, url = _destination(workspace, remote, base)
        record = _prepare(workspace, data, candidate, destination, reconcile=publish)
        if publish:
            if shutil.which("gh") is None:
                raise AuditError(
                    "install and authenticate GitHub CLI before publishing; preparation is saved"
                )
            repository = _repository(url)
            existing = _existing_pr(workspace, repository, record)
            remote_revision = _remote_revision(workspace, url, record["branch"])
            if remote_revision and remote_revision != record["source_revision"]:
                raise AuditError(
                    "remote delivery branch has different source; it was not overwritten"
                )
            if existing is None:
                if record["state"] == "published":
                    raise AuditError(
                        "published PR is no longer discoverable; inspect it before retrying"
                    )
                _selected(workspace, data, candidate["candidate_id"])
                _remote_base(workspace, url, record)
                record["state"] = "publishing"
                _save(workspace, data, record)
                if remote_revision is None:
                    # An empty expected ref is atomic create-only protection. It cannot
                    # update a branch that appears after our remote read, even by fast-forward.
                    _git(
                        workspace.root,
                        "push",
                        "--porcelain",
                        f"--force-with-lease=refs/heads/{record['branch']}:",
                        "--",
                        url,
                        f"{record['source_revision']}:refs/heads/{record['branch']}",
                    )
                if _remote_revision(workspace, url, record["branch"]) != record["source_revision"]:
                    raise AuditError("pushed branch identity changed; publication stopped")
                record["state"] = "pushed"
                _save(workspace, data, record)
                _selected(workspace, data, candidate["candidate_id"])
                _remote_base(workspace, url, record)
                _command(
                    workspace.root,
                    [
                        "gh",
                        "pr",
                        "create",
                        "--repo",
                        repository,
                        "--draft",
                        "--base",
                        record["base"],
                        "--head",
                        record["branch"],
                        "--title",
                        f"fix: apply verified Agentagon candidate {candidate['candidate_id'][-8:]}",
                        "--body-file",
                        str(workspace.root / record["artifacts"]["pr_body"]),
                    ],
                )
                existing = _existing_pr(workspace, repository, record)
                if existing is None:
                    raise AuditError(
                        "PR creation was not confirmed; retry to reconcile without duplicating"
                    )
            record.update(state="published", pr=existing)
            _save(workspace, data, record)
        return {
            **record,
            "artifacts": {
                key: str(workspace.root / value) for key, value in record["artifacts"].items()
            },
        }
