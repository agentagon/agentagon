"""Publication must fail closed when versions, source checks or artifact bytes differ."""

import importlib.util
import io
import json
import tomllib
from pathlib import Path
from urllib.error import HTTPError

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release", ROOT / "scripts/release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def artifacts(tmp_path):
    for name in release.artifact_names("0.1.0"):
        (tmp_path / name).write_bytes(name.encode())
    release.manifest(tmp_path, "0.1.0")
    return release.checksums(tmp_path, "0.1.0")


def test_version_checks_all_manifests_without_executing_package(tmp_path):
    current = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert release.version_for_tag(f"v{current}") == current
    for name in (
        "pyproject.toml",
        "src/agentagon/__init__.py",
        ".codex-plugin/plugin.json",
        ".claude-plugin/plugin.json",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())
    (tmp_path / ".claude-plugin/plugin.json").write_text('{"version": "different"}')
    with pytest.raises(ValueError, match="disagree"):
        release.version_for_tag(f"v{current}", tmp_path)
    for tag in ("main", "--help", "v01.1.0", "v0.1.0;whoami", "v0.1.0rc1"):
        with pytest.raises(ValueError, match="tags must"):
            release.version_for_tag(tag)


def test_checksum_rejects_changed_bytes_and_path_traversal(tmp_path):
    artifacts(tmp_path)
    (tmp_path / "agentagon-0.1.0.tar.gz").write_bytes(b"replacement")
    with pytest.raises(ValueError, match="checksum mismatch"):
        release.checksums(tmp_path, "0.1.0")
    (tmp_path / "SHA256SUMS").write_text("a" * 64 + "  ../outside.whl\n")
    with pytest.raises(ValueError, match="unexpected checksum"):
        release.checksums(tmp_path, "0.1.0")


def test_pypi_resume_copies_only_missing_identical_files(tmp_path, monkeypatch):
    values = artifacts(tmp_path)
    name = "agentagon-0.1.0.tar.gz"
    response = {"urls": [{"filename": name, "digests": {"sha256": values[name]}}]}
    monkeypatch.setattr(
        release, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(response).encode())
    )
    pending = tmp_path / "pending"
    release.pypi_pending(tmp_path, "0.1.0", pending)
    assert {path.name for path in pending.iterdir()} == {"agentagon-0.1.0-py3-none-any.whl"}
    response["urls"][0]["digests"]["sha256"] = "a" * 64
    with pytest.raises(ValueError, match="Existing PyPI artifact differs"):
        release.pypi_pending(tmp_path, "0.1.0", tmp_path / "bad")


def test_pypi_failure_is_not_treated_as_a_new_release(tmp_path, monkeypatch):
    artifacts(tmp_path)

    def unavailable(*args, **kwargs):
        raise HTTPError("https://pypi.org", 503, "Unavailable", {}, None)

    monkeypatch.setattr(release, "urlopen", unavailable)
    with pytest.raises(HTTPError):
        release.pypi_pending(tmp_path, "0.1.0", tmp_path / "pending")
    assert not (tmp_path / "pending").exists()


@pytest.mark.parametrize("failure", ["unprotected", "diverged", "missing", "failed", "pending"])
def test_gate_rejects_unapproved_source(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(release, "version_for_tag", lambda _: "0.1.0")
    monkeypatch.setattr(release.subprocess, "check_output", lambda *a, **k: "a" * 40 + "\n")

    def response(path):
        if path.endswith("branches/main"):
            return {"protected": failure != "unprotected", "commit": {"sha": "b" * 40}}
        if "/compare/" in path:
            return {"status": "diverged" if failure == "diverged" else "ahead"}
        conclusion = {"failed": "failure", "pending": None}.get(failure, "success")
        return {
            "workflow_runs": [] if failure == "missing" else [{"id": 1, "conclusion": conclusion}]
        }

    monkeypatch.setattr(release, "api", response)
    with pytest.raises(ValueError):
        release.gate("v0.1.0", "agentagon/agentagon")


def test_github_resume_never_overwrites_an_existing_asset(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "version_for_tag", lambda _: "0.1.0")
    artifacts(tmp_path)
    (tmp_path / "RELEASE_NOTES.md").write_text("Release notes")
    monkeypatch.setattr(
        release,
        "api",
        lambda _: [
            {
                "tag_name": "v0.1.0",
                "draft": True,
                "assets": [{"name": "SHA256SUMS", "url": "https://api.github.com/asset"}],
            }
        ],
    )
    monkeypatch.setattr(release.subprocess, "check_output", lambda *a, **k: b"different manifest")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        release.stage("v0.1.0", "agentagon/agentagon", tmp_path)
