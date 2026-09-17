"""Prepare local delivery or a draft PR from the exact reviewed source and evidence."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from agentagon.core.records import AuditError, digest, identifier, now
from agentagon.experiments import checkouts, engine, evaluation, patches, preparation, store
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
    if data.get("suite"):
        from agentagon.experiments import suites

        suites.verify_selection(workspace, data["run_id"], candidate["candidate_id"])
    if data.get("optimizer_configured") and not candidate.get("verification_of"):
        raise AuditError("optimizer delivery requires reserved final verification")
    if data["spec"].get("scoring"):
        from agentagon.experiments.scoring import qualifying

        if candidate["candidate_id"] not in {entry["candidate_id"] for entry in qualifying(data)}:
            raise AuditError(
                "selected candidate no longer establishes a verified scored improvement"
            )
    elif candidate["candidate_id"] not in evaluation.frontier(data):
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
    from agentagon.experiments import scoring
    from agentagon.reporting import _score_projection, _scoring_definition_projection

    baseline = data["candidates"][data["baseline_id"]]
    # Selection has revalidated these observations, including bound host grades.
    metrics, variation = evaluation.aggregate(
        data["spec"],
        [trial["metrics"] for trial in engine._completed(candidate)],
    )
    # Only structural IDs, numeric aggregates and booleans leave the private archive.
    # Goal, hypothesis, reviewer prose, commands, environment and raw evidence stay local.
    return {
        "version": 1,
        "status": "verified",
        "source_kind": "measured_candidate",
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
                "agreed_scoring": _scoring_definition_projection(data["spec"]["scoring"]),
                "baseline_score": _score_projection(scoring.candidate_score(data, baseline)),
                "candidate_score": _score_projection(scoring.candidate_score(data, candidate)),
            }
            if data["spec"].get("scoring")
            else {}
        ),
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
    if summary.get("candidate_score"):
        lines += [
            f"- Agreed score: {summary['baseline_score']['score']} → {summary['candidate_score']['score']} (higher is better).",
            "- Required behavior gates and hard limits passed independently of the score.",
        ]
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


def _safe_snapshot(workspace: Workspace, revision: str) -> None:
    for name in checkouts.paths(workspace.root, revision):
        path = Path(name)
        if (
            any(part in path.parts for part in (".agentagon", "agentagon-private"))
            or path.name == ".env"
            or path.name.startswith(".env.")
            and path.name not in {".env.example", ".env.sample"}
            or path.suffix in {".pem", ".key", ".p12"}
        ):
            raise AuditError("delivery snapshot contains a private state or credential path")


def _reviewed_delivery(data: dict, delivery_id: str | None, binding: dict) -> None:
    """Bind browser publication to the package reviewed before opening its dialog."""
    if delivery_id is None:
        return
    record = data.get("deliveries", {}).get(delivery_id) if isinstance(delivery_id, str) else None
    if (
        not record
        or record.get("version") != 1
        or any(record.get(key) != value for key, value in binding.items())
    ):
        raise AuditError("prepared delivery changed; prepare and review the current package again")


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
    _safe_snapshot(workspace, candidate["source_revision"])
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


def _eval_parent(workspace: Workspace, data: dict, candidate: dict, evaluation_id: str) -> dict:
    """Prove that the measured application is a child of the exact reviewed eval commit.

    A PR base name alone cannot establish this relationship: candidates made from the
    pre-eval commit must be measured again on the evaluation branch before delivery.
    """
    prepared = preparation.load(workspace, evaluation_id)
    source = _evaluation_source(workspace, prepared)
    if data["origin_revision"] != source["source_revision"]:
        raise AuditError(
            "stacked delivery requires the exact eval parent as the measured application base"
        )
    _git(
        workspace.root,
        "merge-base",
        "--is-ancestor",
        source["source_revision"],
        candidate["source_revision"],
    )
    # The frozen evaluator must remain unchanged in the application child.
    frozen_paths = {
        entry["path"] for entry in prepared["package"]["files"] if entry["kind"] != "inputs"
    }
    changed = set(
        checkouts.changes(workspace.root, source["source_revision"], candidate["source_revision"])
    )
    if frozen_paths & changed:
        raise AuditError("stacked application delivery changes the frozen eval parent")
    return {"evaluation_id": evaluation_id, **source}


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
        raise AuditError("existing PR does not match this delivery; it was not changed")
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
    eval_parent_id: str | None = None,
    prepared_delivery_id: str | None = None,
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
        _reviewed_delivery(
            data,
            prepared_delivery_id,
            {
                "run_id": run_id,
                "candidate_id": candidate["candidate_id"],
                "branch": data["selected"]["branch"],
                "source_revision": candidate["source_revision"],
                "source_digest": candidate["source_digest"],
                "base_revision": data["origin_revision"],
                "evaluation_digest": data["evaluation_digest"],
                "inputs_digest": data["inputs_digest"],
                "profile_digest": data["profile_digest"],
            },
        )
        parent = (
            _eval_parent(workspace, data, candidate, eval_parent_id) if eval_parent_id else None
        )
        if parent:
            if base is not None and base != parent["branch"]:
                raise AuditError("stacked delivery base must be the reviewed evaluation branch")
            base = parent["branch"]
        destination, url = (
            _destination(workspace, remote, base)
            if publish
            else ({"base_revision": data["origin_revision"]}, None)
        )
        record = _prepare(workspace, data, candidate, destination, reconcile=publish)
        if parent:
            record["eval_parent"] = parent
            record["merge_order"] = "Merge the evaluation PR first, then the application PR."
            body_path = workspace.root / record["artifacts"]["pr_body"]
            workspace.write_bytes(
                body_path,
                (
                    body_path.read_text()
                    + "\n## Stacked delivery\n\n"
                    + f"Evaluation parent: `{parent['source_revision']}` on `{parent['branch']}`.\n\n"
                    + record["merge_order"]
                    + "\n"
                ).encode(),
            )
            _save(workspace, data, record)

        def revalidate():
            selected = _selected(workspace, data, candidate["candidate_id"])
            if eval_parent_id:
                _eval_parent(workspace, data, selected, eval_parent_id)

        if publish:
            _publish(
                workspace,
                record,
                url,
                save=lambda value: _save(workspace, data, value),
                revalidate=revalidate,
                title=f"fix: apply verified Agentagon candidate {candidate['candidate_id'][-8:]}",
            )
        return {
            **record,
            "artifacts": {
                key: str(workspace.root / value) for key, value in record["artifacts"].items()
            },
        }


def _publish(workspace: Workspace, record: dict, url: str, *, save, revalidate, title: str) -> None:
    if shutil.which("gh") is None:
        raise AuditError(
            "install and authenticate GitHub CLI before publishing; preparation is saved"
        )
    repository = _repository(url)
    existing = _existing_pr(workspace, repository, record)
    remote_revision = _remote_revision(workspace, url, record["branch"])
    if remote_revision and remote_revision != record["source_revision"]:
        raise AuditError("remote delivery branch has different source; it was not overwritten")
    if existing is None:
        if record["state"] == "published":
            raise AuditError("published PR is no longer discoverable; inspect it before retrying")
        revalidate()
        _remote_base(workspace, url, record)
        record["state"] = "publishing"
        save(record)
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
        save(record)
        revalidate()
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
                title,
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
    save(record)


def _evaluation_source(workspace: Workspace, data: dict) -> dict:
    if data["state"] != "frozen":
        raise AuditError("evaluation delivery requires a reviewed frozen benchmark")
    package, review = data["package"], data["review"]
    record = next(c for c in data["checks"] if c["validation_id"] == package["validation_id"])
    template = record["review_template"]
    if (
        record["state"] != "passed"
        or review["verdict"] != "pass"
        or review["reviewer"] == data["author"]
        or review["assessments"] != dict.fromkeys(preparation.ASSESSMENTS, True)
        or any(
            review[k] != template[k]
            for k in ("evaluation_id", "validation_id", "validation_digest", "evidence")
        )
        or digest({k: v for k, v in record.items() if k != "review_template"})
        != review["validation_digest"]
        or package["files"] != record["files"]
        or package["plan"] != record["plan"]
    ):
        raise AuditError("frozen evaluation review or validation evidence changed")
    observed = preparation._observed_trials(workspace, data, record)
    comparisons = preparation._metric_comparisons(record["plan"], observed)
    if comparisons != record.get("metric_comparisons", []) or not all(
        c["passed"] for c in comparisons
    ):
        raise AuditError("evaluation metric validation changed")
    branch = package["review_branch"]
    revision = _git(workspace.root, "rev-parse", "--verify", f"refs/heads/{branch}")
    if package.get("review_revision") and package["review_revision"] != revision:
        raise AuditError("evaluation review branch changed")
    files = {entry["path"]: entry for entry in package["files"] if entry["kind"] != "inputs"}
    changed = checkouts.changes(workspace.root, data["origin_revision"], revision)
    if set(changed) - files.keys():
        raise AuditError("evaluation branch contains changes outside its reviewed package")
    names = set(checkouts.paths(workspace.root, revision))
    for name, entry in files.items():
        if entry.get("deleted"):
            if name in names:
                raise AuditError("evaluation branch does not match its reviewed deletion")
            continue
        retained = checkouts.checked_file(workspace.root, entry["artifact"]).read_bytes()
        delivered = _git(workspace.root, "show", f"{revision}:{name}", binary=True)
        mode = _git(workspace.root, "ls-tree", revision, "--", name).split(" ", 1)[0]
        if (
            hashlib.sha256(retained).hexdigest() != entry["digest"]
            or delivered != retained
            or mode != ("100755" if entry["mode"] & 0o111 else "100644")
        ):
            raise AuditError("evaluation branch or frozen input changed")
    # Validate private inputs too, even though their contents never enter the delivery.
    for entry in package["files"]:
        if entry["kind"] == "inputs" and not entry.get("deleted"):
            content = checkouts.checked_file(workspace.root, entry["artifact"]).read_bytes()
            if hashlib.sha256(content).hexdigest() != entry["digest"]:
                raise AuditError("frozen evaluation input changed")
    return {
        "branch": branch,
        "source_revision": revision,
        "source_digest": checkouts.tree(workspace.root, revision),
    }


def deliver(
    workspace: Workspace,
    *,
    run_id: str | None = None,
    evaluation_id: str | None = None,
    patch_id: str | None = None,
    remote: str = "origin",
    base: str | None = None,
    publish: bool = False,
    eval_parent_id: str | None = None,
    prepared_delivery_id: str | None = None,
) -> dict:
    """Package one selected measured candidate, frozen eval, or reviewed patch locally."""
    if sum(value is not None for value in (run_id, evaluation_id, patch_id)) != 1:
        raise AuditError("delivery needs exactly one run, evaluation, or patch")
    if type(publish) is not bool:
        raise AuditError("publication requires an explicit boolean publish option")
    if run_id:
        return ship(
            workspace,
            run_id,
            remote=remote,
            base=base,
            publish=publish,
            eval_parent_id=eval_parent_id,
            prepared_delivery_id=prepared_delivery_id,
        )
    if eval_parent_id:
        raise AuditError("an eval parent applies only to a verified application run")
    workspace.require_initialized()
    backend = patches if patch_id else preparation
    source_id = patch_id or evaluation_id
    with backend.locked(workspace, source_id) as root:
        data = backend.load(workspace, source_id)

        def revalidate():
            if patch_id:
                patches.verified(workspace, data)
                return {k: data[k] for k in ("branch", "source_revision", "source_digest")}
            return _evaluation_source(workspace, data)

        source = revalidate()
        _reviewed_delivery(
            data,
            prepared_delivery_id,
            {
                "source_kind": "patch" if patch_id else "evaluation",
                "source_id": source_id,
                "base_revision": data["origin_revision"],
                **source,
                "evidence_digest": digest(data["review"]) if patch_id else data["package_digest"],
            },
        )
        _safe_snapshot(workspace, source["source_revision"])
        if source["source_digest"] == checkouts.tree(workspace.root, data["origin_revision"]):
            raise AuditError("reviewed source has no changes to deliver")
        _git(
            workspace.root,
            "merge-base",
            "--is-ancestor",
            data["origin_revision"],
            source["source_revision"],
        )
        destination, url = _destination(workspace, remote, base) if publish else ({}, None)
        binding = {
            **destination,
            "base_revision": data["origin_revision"],
            "source_kind": "patch" if patch_id else "evaluation",
            "source_id": source_id,
            **source,
            "evidence_digest": digest(data["review"]) if patch_id else data["package_digest"],
        }
        delivery_id = identifier("delivery", binding)
        deliveries = data.setdefault("deliveries", {})
        record = deliveries.get(delivery_id)
        if record and (
            record.get("version") != 1 or any(record.get(k) != v for k, v in binding.items())
        ):
            raise AuditError("delivery record changed or has an unsupported version")
        if record is None:
            record = {
                "version": 1,
                "delivery_id": delivery_id,
                **binding,
                "state": "prepared",
                "created_at": now(),
            }
            deliveries[delivery_id] = record
        directory = root / "deliveries" / delivery_id

        def save(value):
            value["updated_at"] = now()
            backend._save(workspace, data)
            workspace.write(directory / "delivery.json", value)

        state = "reviewed_unmeasured" if patch_id else "reviewed_evaluation"
        limitations = [
            "No application improvement or issue resolution is established by this delivery.",
            "Baseline comparison is unavailable; recorded validation and independent review apply to this exact patch."
            if patch_id
            else "Evaluation validation applies to the fixed application revision and declared cases; dataset scores do not establish application improvement.",
            "Publishing a draft PR does not merge or deploy the change.",
        ]
        summary = {
            "version": 1,
            "status": state,
            "source_kind": binding["source_kind"],
            "source_id": source_id,
            **source,
            "base_revision": data["origin_revision"],
            "evidence_digest": binding["evidence_digest"],
            "independent_review": "pass",
            "comparison": None,
            "limitations": limitations,
        }
        if patch_id:
            summary["checks"] = [{"id": c["id"], "passed": True} for c in data["plan"]["checks"]]
            summary["executable_checks_run"] = bool(data["plan"]["checks"])
            if not data["plan"]["checks"]:
                limitations.append(
                    "No executable checks run; this patch has independent review only."
                )
        else:
            summary["validation_id"] = data["package"]["validation_id"]
            summary["coverage"] = data["package"]["plan"]["coverage"]["status"]
            comparison = data["package"].get("version_comparison")
            if comparison:
                delivered = set(
                    checkouts.changes(
                        workspace.root, data["origin_revision"], source["source_revision"]
                    )
                )
                summary["comparison"] = {
                    "kind": "evaluation_version",
                    **{
                        key: comparison[key]
                        for key in (
                            "previous_evaluation_id",
                            "previous_package_digest",
                            "previous_validation_id",
                            "validation_id",
                            "application_revision",
                            "same_application_revision",
                        )
                    },
                    "changed_delivered_paths": [
                        name for name in comparison["changed_paths"] if name in delivered
                    ],
                    "coverage_before": comparison["coverage_before"]["status"],
                    "coverage_after": comparison["coverage_after"]["status"],
                    "provenance_changed": comparison["provenance_before"]
                    != comparison["provenance_after"],
                    "claim": "Evaluator version change; scores across versions do not establish application improvement.",
                }
        body = "\n".join(
            [
                "# Reviewed Agentagon patch" if patch_id else "# Reviewed Agentagon evaluation",
                "",
                f"Evidence status: `{state}`.",
                "",
                "## Validation",
                "",
                "- Independent review passed.",
                *[f"- Check `{c['id']}` passed." for c in summary.get("checks", [])],
                "",
                "## Evidence identity",
                "",
                *[
                    f"- {k}: `{summary[k]}`"
                    for k in (
                        "source_id",
                        "source_revision",
                        "source_digest",
                        "base_revision",
                        "evidence_digest",
                    )
                ],
                "",
                "## Limits",
                "",
                *[f"- {limit}" for limit in limitations],
                *(
                    [
                        "",
                        "## Evaluation version comparison",
                        "",
                        f"- Previous evaluation: `{summary['comparison']['previous_evaluation_id']}`.",
                        f"- Same application revision: `{summary['comparison']['same_application_revision']}`.",
                        f"- Coverage: `{summary['comparison']['coverage_before']}` → `{summary['comparison']['coverage_after']}`.",
                        "- Evaluator changes do not establish application improvement across dataset versions.",
                    ]
                    if summary["comparison"]
                    else []
                ),
                "",
                f"<!-- agentagon-delivery:{delivery_id} -->",
                "",
            ]
        )
        workspace.write(directory / "summary.json", summary)
        workspace.write_bytes(directory / "pull-request.md", body.encode())
        workspace.write_bytes(
            directory / "diff.patch",
            _git(
                workspace.root,
                "diff",
                "--binary",
                "--no-ext-diff",
                "--no-textconv",
                data["origin_revision"],
                source["source_revision"],
                "--",
                binary=True,
            ),
        )
        workspace.write_bytes(
            directory / "diffstat.txt",
            (
                _git(
                    workspace.root,
                    "diff",
                    "--stat",
                    "--no-ext-diff",
                    "--no-textconv",
                    data["origin_revision"],
                    source["source_revision"],
                    "--",
                )
                + "\n"
            ).encode(),
        )
        record["artifacts"] = {
            key: str((directory / filename).relative_to(workspace.root))
            for key, filename in {
                "summary": "summary.json",
                "pr_body": "pull-request.md",
                "diff": "diff.patch",
                "diffstat": "diffstat.txt",
            }.items()
        }
        save(record)
        if publish:
            _publish(
                workspace,
                record,
                url,
                save=save,
                revalidate=revalidate,
                title=f"fix: apply reviewed Agentagon {binding['source_kind']} {source_id[-8:]}",
            )
        return {
            **record,
            "artifacts": {
                key: str(workspace.root / value) for key, value in record["artifacts"].items()
            },
        }
