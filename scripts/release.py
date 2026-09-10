"""Validate release inputs and preserve published artifacts across workflow retries."""

import argparse
import ast
import hashlib
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_WORKFLOWS = ("tests.yml", "security.yml", "codeql.yml")


def gh(*args):
    return subprocess.check_output(["gh", *args], text=True)


def api(path):
    return json.loads(gh("api", path))


def version_for_tag(tag, root=ROOT):
    if not re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", tag):
        raise ValueError("Release tags must be vX.Y.Z without a prefix or prerelease suffix")
    version = tag[1:]
    versions = [tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]]
    module = ast.parse((root / "src/agentagon/__init__.py").read_text())
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        ):
            versions.append(ast.literal_eval(node.value))
    for host in ("codex", "claude"):
        versions.append(json.loads((root / f".{host}-plugin/plugin.json").read_text())["version"])
    if len(versions) != 4 or any(value != version for value in versions):
        raise ValueError(f"Tag and package/CLI/plugin versions disagree: {tag}, {versions}")
    return version


def gate(tag, repository):
    version_for_tag(tag)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    tag_sha = subprocess.check_output(["git", "rev-parse", f"{tag}^{{commit}}"], text=True).strip()
    if sha != tag_sha:
        raise ValueError("Checkout does not match the release tag")
    branch = api(f"repos/{repository}/branches/main")
    if not branch["protected"]:
        raise ValueError("main must be protected before publication")
    comparison = api(f"repos/{repository}/compare/{sha}...{branch['commit']['sha']}")
    if comparison["status"] not in {"ahead", "identical"}:
        raise ValueError("Tagged revision must belong to main")
    for workflow in REQUIRED_WORKFLOWS:
        runs = api(
            f"repos/{repository}/actions/workflows/{workflow}/runs"
            f"?head_sha={sha}&branch=main&event=push&per_page=100"
        )["workflow_runs"]
        if not runs or max(runs, key=lambda run: run["id"])["conclusion"] != "success":
            raise ValueError(f"Successful main push checks missing for {sha}: {workflow}")
    print(f"Release gate passed for {tag} at {sha}")


def artifact_names(version):
    return {f"agentagon-{version}-py3-none-any.whl", f"agentagon-{version}.tar.gz"}


def checksums(directory, version):
    expected = artifact_names(version)
    lines = (directory / "SHA256SUMS").read_text().splitlines()
    values = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match or match[2] not in expected or match[2] in values:
            raise ValueError("Invalid or unexpected checksum entry")
        values[match[2]] = match[1]
    if set(values) != expected:
        raise ValueError("Checksum manifest must contain exactly the wheel and source archive")
    for name, value in values.items():
        path = directory / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != value:
            raise ValueError(f"Artifact checksum mismatch: {name}")
    return values


def manifest(directory, version):
    if {path.name for path in directory.iterdir()} != artifact_names(version):
        raise ValueError(
            "Build directory must contain only this version's wheel and source archive"
        )
    (directory / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}\n"
            for name in sorted(artifact_names(version))
        )
    )


def stage(tag, repository, directory):
    values = checksums(directory, version_for_tag(tag))
    releases = api(f"repos/{repository}/releases?per_page=100")
    existing = next((release for release in releases if release["tag_name"] == tag), None)
    if existing is None:
        gh(
            "release",
            "create",
            tag,
            "--repo",
            repository,
            "--verify-tag",
            "--draft",
            "--title",
            f"Agentagon {tag[1:]}",
            "--notes-file",
            str(directory / "RELEASE_NOTES.md"),
        )
    elif not existing["draft"]:
        # A completed release is immutable to retries too.
        print("GitHub release already published; checking assets without replacing them")
    assets = {item["name"]: item for item in (existing or {}).get("assets", [])}
    for name in ["SHA256SUMS", "RELEASE_NOTES.md", *sorted(values)]:
        if name in assets:
            downloaded = subprocess.check_output(
                ["gh", "api", "-H", "Accept: application/octet-stream", assets[name]["url"]]
            )
            if downloaded != (directory / name).read_bytes():
                raise ValueError(f"Existing GitHub asset differs: {name}; refusing to overwrite")
        else:
            gh("release", "upload", tag, str(directory / name), "--repo", repository)


def pypi_pending(directory, version, destination):
    values = checksums(directory, version)
    try:
        with urlopen(f"https://pypi.org/pypi/agentagon/{version}/json", timeout=30) as response:
            published = {
                item["filename"]: item["digests"]["sha256"] for item in json.load(response)["urls"]
            }
    except HTTPError as error:
        if error.code != 404:
            raise
        published = {}
    if set(published) - set(values):
        raise ValueError("PyPI release contains unexpected files")
    if destination.exists():
        raise ValueError("Pending-upload directory must be new")
    destination.mkdir()
    for name, value in values.items():
        if name in published:
            if published[name] != value:
                raise ValueError(f"Existing PyPI artifact differs: {name}; use a new version")
        else:
            shutil.copy2(directory / name, destination / name)
    print(f"{len(list(destination.iterdir()))} files remain to publish")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["gate", "version", "manifest", "verify", "stage", "pypi"]
    )
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repository", default="agentagon/agentagon")
    parser.add_argument("--directory", type=Path, default=Path("dist"))
    parser.add_argument("--pending", type=Path, default=Path("pending"))
    args = parser.parse_args()
    version = version_for_tag(args.tag)
    if args.command == "gate":
        gate(args.tag, args.repository)
    elif args.command == "manifest":
        manifest(args.directory, version)
    elif args.command == "verify":
        checksums(args.directory, version)
    elif args.command == "stage":
        stage(args.tag, args.repository, args.directory)
    elif args.command == "pypi":
        pypi_pending(args.directory, version, args.pending)
    else:
        print(version)


if __name__ == "__main__":
    main()
