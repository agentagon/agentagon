import pytest

from agentagon.telemetry.alignment import trace_alignment

REVISION = "1234567890abcdef1234567890abcdef12345678"


@pytest.mark.parametrize(
    "metadata",
    [
        {"git.commit.sha": REVISION},
        {"git": {"commit": {"sha": REVISION[:12]}}},
        {"commit_sha": REVISION.upper()},
        {"service.version": "2.0.0", "version": "abcdef1234"},
        {"git.commit.sha": "main"},
        {},
    ],
)
def test_matching_or_missing_revision_metadata_is_not_verification(metadata):
    result = trace_alignment({"spans": [{"metadata": metadata}]}, REVISION)
    assert result["status"] == "assumed"
    assert result["revision"] == REVISION


def test_conflicting_explicit_revisions_are_retained_across_spans():
    other = "a" * 40
    result = trace_alignment(
        {
            "spans": [
                {"metadata": {"git_commit": REVISION}},
                {"resource": {"git.commit.sha": other}},
            ]
        },
        REVISION,
    )
    assert result["status"] == "mismatch"
    assert set(result["reported_revisions"]) == {REVISION, other}


def test_unborn_review_cannot_infer_a_version_match():
    result = trace_alignment({"spans": [{"attributes": {"git_sha": REVISION}}]}, None, "changes")
    assert result["status"] == "unverified"
    assert result["revision"] is None


def test_baseline_hash_does_not_prove_traces_executed_local_changes():
    result = trace_alignment(
        {"spans": [{"metadata": {"git.commit.sha": REVISION}}]}, REVISION, "changes"
    )
    assert result["status"] == "unverified"


@pytest.mark.parametrize("reported", [REVISION, "a" * 40])
def test_full_audit_with_local_changes_warns_even_when_metadata_matches(reported):
    result = trace_alignment(
        {"spans": [{"metadata": {"git.commit.sha": reported}}]},
        REVISION,
        local_changes=True,
    )
    assert result["status"] == ("unverified" if reported == REVISION else "mismatch")
    assert "uncommitted changes" in result["warning"]
    assert "incomplete or incorrect" in result["warning"]


def test_full_audit_without_git_cannot_infer_a_version_match():
    result = trace_alignment({"spans": [{"metadata": {"git.commit.sha": REVISION}}]}, None)
    assert result["status"] == "unverified"
    assert "no Git revision" in result["warning"]
