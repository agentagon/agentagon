"""Compare explicit source revision metadata without treating it as execution proof."""

import re

REVISION_KEYS = {
    "git.commit.sha",
    "git.commit.id",
    "git.commit.hash",
    "git.commit_sha",
    "git_commit",
    "git_sha",
    "commit_sha",
    "git_commit_sha",
    "vcs.ref.head.revision",
}
COMMIT = re.compile(r"[0-9a-fA-F]{7,64}\Z")


def _revisions(value: dict, prefix: str = "") -> set[str]:
    result = set()
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else key
        if name in REVISION_KEYS and isinstance(item, str) and COMMIT.fullmatch(item):
            result.add(item.lower())
        elif isinstance(item, dict):
            result.update(_revisions(item, name))
    return result


def trace_alignment(
    trace: dict, revision: str | None, code_scope: str = "full", local_changes: bool | None = False
) -> dict:
    """A matching hash supports the assumption; it does not verify the deployed source."""
    reported = set()
    for span in trace["spans"]:
        for field in ("attributes", "metadata", "resource"):
            container = span.get(field)
            if isinstance(container, dict):
                reported.update(_revisions(container))
    mismatch = revision is not None and any(
        not revision.lower().startswith(value) for value in reported
    )
    warning = None
    if local_changes:
        warning = (
            "This codebase has uncommitted changes. Supplied traces may reflect different code, "
            "even when their commit matches HEAD. Analysis may be incomplete or incorrect."
        )
    elif revision is None:
        warning = (
            "This codebase has no Git revision to compare with supplied traces. "
            "Traces may reflect different code; analysis may be incomplete or incorrect."
        )
    return {
        "status": "mismatch"
        if mismatch
        else "unverified"
        if code_scope == "changes" or warning
        else "assumed",
        "revision": revision,
        "reported_revisions": sorted(reported),
        "warning": warning,
    }
