"""Install the bundled plugin through each host's native plugin manager."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from agentagon.core.records import AuditError, resource_path

HOSTS = {"codex": "codex", "claude-code": "claude"}
SKILLS = ("audit", "review", "setup", "dashboard", "fix", "ship", "eval")
MARKETPLACE = "agentagon-local"
PLUGIN_ID = f"ag@{MARKETPLACE}"
MARKER = ".agentagon-managed.json"


def install_plugins(hosts: list[str] | None = None, *, home: Path | None = None) -> dict:
    """Install for selected hosts, or every supported host executable on PATH.

    ``home`` isolates both storage and native host configuration for integration tests.
    Production calls preserve the host's configured home and all unrelated settings.
    """
    selected = list(dict.fromkeys(hosts if hosts is not None else _detected_hosts()))
    if not selected:
        raise AuditError("no supported host CLI found; install Codex or Claude Code, then retry")
    executables = {}
    for host in selected:
        if host not in HOSTS:
            raise AuditError(f"unsupported plugin host: {host}")
        executable = shutil.which(HOSTS[host])
        if not executable:
            raise AuditError(f"{HOSTS[host]} executable not found on PATH; install that host first")
        executables[host] = executable

    environment = os.environ.copy()
    if home is not None:
        home = home.expanduser().resolve()
        # Native configuration-directory overrides, never the real user's configuration.
        for name, folder in (("CODEX_HOME", ".codex"), ("CLAUDE_CONFIG_DIR", ".claude")):
            directory = home / folder
            directory.mkdir(parents=True, exist_ok=True)
            environment[name] = str(directory)
        data = home / ".local/share"
    else:
        data = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
        if not data.is_absolute():
            raise AuditError("XDG_DATA_HOME must be an absolute path")
    root = data / "agentagon/plugins"

    registered = {}
    for host, executable in executables.items():
        listing = _run_json(executable, ["plugin", "marketplace", "list", "--json"], environment)
        registered[host] = _check_marketplace(host, listing, root)

    version = _sync_bundle(root)
    results = []
    for host, executable in executables.items():
        if host == "codex":
            _run(executable, ["plugin", "marketplace", "add", str(root), "--json"], environment)
            _run(executable, ["plugin", "add", PLUGIN_ID, "--json"], environment)
        else:
            if registered[host]:
                _run(executable, ["plugin", "marketplace", "update", MARKETPLACE], environment)
            else:
                _run(executable, ["plugin", "marketplace", "add", str(root)], environment)
            before = _run_json(executable, ["plugin", "list", "--json"], environment)
            command = "update" if _plugin_entry(host, before) is not None else "install"
            _run(executable, ["plugin", command, PLUGIN_ID, "--scope", "user"], environment)
        listing = _run_json(executable, ["plugin", "list", "--json"], environment)
        entry = _plugin_entry(host, listing)
        if host == "claude-code" and entry is not None and entry.get("enabled") is not True:
            # Claude reports an error when enabling a plugin that is already enabled.
            _run(executable, ["plugin", "enable", PLUGIN_ID, "--scope", "user"], environment)
            listing = _run_json(executable, ["plugin", "list", "--json"], environment)
            entry = _plugin_entry(host, listing)
        if entry is None or entry.get("enabled") is not True or entry.get("version") != version:
            raise AuditError(
                f"{host} did not report the current {PLUGIN_ID} version enabled; "
                f"inspect `{HOSTS[host]} plugin list --json` and retry"
            )
        results.append({"host": host, "plugin": PLUGIN_ID, "version": version, "enabled": True})
    return {
        "state": "installed",
        "marketplace": str(root),
        "plugin_root": str(root / "plugins/ag"),
        "hosts": results,
        "skills": [f"ag:{name}" for name in SKILLS],
        "deferred": [],
        "next": "Start a new coding-agent session. Use ag:review for local changes or "
        "ag:audit for a full codebase audit, with or without Git or local changes. In Claude Code prefix with /; "
        "in Codex use the skill picker.",
    }


def _detected_hosts() -> list[str]:
    return [host for host, executable in HOSTS.items() if shutil.which(executable)]


def _run(executable: str, arguments: list[str], environment: dict[str, str]) -> str:
    try:
        completed = subprocess.run(
            [executable, *arguments],
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AuditError(
            f"{Path(executable).name} plugin command timed out; retry installation"
        ) from exc
    if completed.returncode:
        # Host diagnostics can contain unrelated configuration. Do not echo them into audit logs.
        raise AuditError(
            f"{Path(executable).name} {' '.join(arguments)} failed "
            f"(exit {completed.returncode}); run that host command directly for diagnostics"
        )
    return completed.stdout


def _run_json(executable: str, arguments: list[str], environment: dict[str, str]):
    try:
        return json.loads(_run(executable, arguments, environment))
    except json.JSONDecodeError as exc:
        raise AuditError(
            f"{Path(executable).name} returned invalid plugin JSON; update the host"
        ) from exc


def _check_marketplace(host: str, listing, root: Path) -> bool:
    entries = listing.get("marketplaces") if isinstance(listing, dict) else listing
    if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
        raise AuditError(f"{host} returned an unsupported marketplace listing; update the host")
    for entry in entries:
        if entry.get("name") != MARKETPLACE:
            continue
        if host == "codex":
            source = entry.get("marketplaceSource", {})
            location = source.get("source") if source.get("sourceType") == "local" else None
        else:
            source = entry.get("source")
            location = (
                source.get("path")
                if isinstance(source, dict) and source.get("source") in {"directory", "file"}
                else entry.get("path")
                if source in ("directory", "file")
                else None
            )
        if location is None or Path(location).resolve() != root.resolve():
            raise AuditError(
                f"{host} already has an unrelated {MARKETPLACE} marketplace; preserved it"
            )
        return True
    return False


def _plugin_entry(host: str, listing) -> dict | None:
    entries = listing.get("installed") if isinstance(listing, dict) else listing
    if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
        raise AuditError(f"{host} returned an unsupported plugin listing; update the host")
    identity = "pluginId" if host == "codex" else "id"
    return next(
        (
            item
            for item in entries
            if item.get(identity) == PLUGIN_ID
            and (host == "codex" or item.get("scope", "user") == "user")
        ),
        None,
    )


def _sync_bundle(root: Path) -> str:
    """Update only files recorded as ours; preserve unrelated host files and source additions."""
    payload = {}
    for name in SKILLS:
        source = resource_path(f"skills/{name}")
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            if path.is_file() and not path.is_symlink():
                payload[f"plugins/ag/skills/{name}/{relative}"] = path.read_bytes()
    source = resource_path("hooks")
    for path in sorted(source.glob("*.json")):
        if path.is_file() and not path.is_symlink():
            payload[f"plugins/ag/hooks/{path.name}"] = path.read_bytes()
    manifests = {}
    for folder in (".codex-plugin", ".claude-plugin"):
        manifest = json.loads(resource_path(f"{folder}/plugin.json").read_text(encoding="utf-8"))
        manifests[folder] = manifest
        payload[f"plugins/ag/{folder}/plugin.json"] = _json_bytes(manifest)
    fingerprint = hashlib.sha256()
    for name, content in sorted(payload.items()):
        fingerprint.update(name.encode() + b"\0" + content + b"\0")
    version = f"{manifests['.codex-plugin']['version'].split('+')[0]}+codex.{fingerprint.hexdigest()[:12]}"
    for folder, manifest in manifests.items():
        manifest["version"] = version
        payload[f"plugins/ag/{folder}/plugin.json"] = _json_bytes(manifest)
    payload[".agents/plugins/marketplace.json"] = _json_bytes(
        {
            "name": MARKETPLACE,
            "interface": {"displayName": "Agentagon"},
            "plugins": [
                {
                    "name": "ag",
                    "source": {"source": "local", "path": "./plugins/ag"},
                    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                    "category": "Productivity",
                }
            ],
        }
    )
    payload[".claude-plugin/marketplace.json"] = _json_bytes(
        {
            "name": MARKETPLACE,
            "owner": {"name": "Agentagon"},
            "plugins": [{"name": "ag", "source": "./plugins/ag"}],
        }
    )
    if root.is_symlink():
        raise AuditError("plugin destination is a symlink; preserved it")
    marker = root / MARKER
    previous = set()
    if marker.exists():
        if marker.is_symlink():
            raise AuditError("plugin ownership record is a symlink; preserved it")
        try:
            record = json.loads(marker.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise AuditError("invalid plugin ownership record; preserved existing files") from exc
        if (
            not isinstance(record, dict)
            or record.get("owner") != "agentagon"
            or not isinstance(record.get("files"), list)
            or not all(isinstance(name, str) for name in record["files"])
        ):
            raise AuditError("invalid plugin ownership record; preserved existing files")
        previous = set(record["files"])
    elif root.exists() and any(root.iterdir()):
        raise AuditError("plugin destination contains unmanaged files; preserved it")
    for name in previous | payload.keys():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise AuditError("invalid managed plugin path; preserved existing files")
        destination = root / relative
        if destination.is_symlink() or not destination.resolve().is_relative_to(root.resolve()):
            raise AuditError("managed plugin path escapes its directory; preserved it")
        if name in payload and destination.exists() and name not in previous:
            raise AuditError("plugin update would overwrite an unmanaged file; preserved it")
    root.mkdir(parents=True, exist_ok=True)
    # Record ownership before the first copy so an interrupted install can be retried.
    _replace_bytes(
        marker, _json_bytes({"owner": "agentagon", "files": sorted(previous | payload.keys())})
    )
    for name, content in payload.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_bytes() != content:
            _replace_bytes(path, content)
    for name in previous - payload.keys():
        (root / name).unlink(missing_ok=True)
    _replace_bytes(marker, _json_bytes({"owner": "agentagon", "files": sorted(payload)}))
    return version


def _replace_bytes(path: Path, content: bytes) -> None:
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=".agentagon-", delete=False
    ) as temporary:
        pending = Path(temporary.name)
        try:
            temporary.write(content)
            temporary.flush()
            os.replace(pending, path)
        finally:
            pending.unlink(missing_ok=True)


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()
