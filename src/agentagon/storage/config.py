"""User defaults and checkout overrides in one private, user-local file."""

import copy
import fcntl
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from agentagon.core.records import PROVIDERS, AuditError, encoded

ACCESS_MESSAGE = (
    "An optional Agentagon API key unlocks audit, evaluation and fix suggestions from our curated knowledge "
    "library. Use ag:setup to configure https://brain.agentagon.ai or your supplied service origin "
    "and the name of an environment variable containing your API key. "
    "Email hello@agentagon.ai to request access. "
    "Local audits, evaluation preparation and measured fixes remain available without API access."
)
DEFAULTS = {
    "intelligence": {
        "endpoint": None,
        "api_key_env": "AGENTAGON_API_KEY",
        "access_presented": False,
    },
    "traces": {
        "state": "unset",
        "source": None,
        "project": None,
        "endpoint": None,
        "api_key_env": None,
        "public_key_env": None,
    },
    "profiles": {},
    "telemetry": {"enabled": True},
}
KEYS = {f"{section}.{key}" for section, values in DEFAULTS.items() for key in values}


def validate_endpoint(value: str, *, origin: bool = False) -> str:
    value = value.strip().rstrip("/")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise AuditError("endpoint URL must not contain whitespace or control characters")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise AuditError("invalid endpoint URL") from exc
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AuditError("endpoint URL must not contain credentials, a query, or a fragment")
    if origin and parsed.path:
        raise AuditError("intelligence endpoint must be an origin without a path")
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if not parsed.hostname or (
        parsed.scheme != "https" and not (local and parsed.scheme == "http")
    ):
        raise AuditError("endpoint requires HTTPS; HTTP is allowed only for loopback")
    return value


def _value(key: str, value: Any) -> Any:
    if key not in KEYS:
        raise AuditError(f"unsupported setting: {key}")
    if key in {"intelligence.access_presented", "telemetry.enabled"}:
        if not isinstance(value, bool):
            raise AuditError(f"{key} must be a boolean")
        return value
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise AuditError(f"{key} must be a nonempty string of at most 2048 characters")
    value = value.strip()
    if key.endswith((".api_key_env", ".public_key_env")) and not re.fullmatch(
        r"[A-Z_][A-Z0-9_]*", value
    ):
        raise AuditError(f"{key} must be an environment variable name, never a secret")
    if key == "traces.state" and value not in {"unset", "enabled", "disabled"}:
        raise AuditError("traces.state must be unset, enabled, or disabled")
    if key == "traces.source" and value not in PROVIDERS:
        raise AuditError(f"traces.source must be one of: {', '.join(PROVIDERS)}")
    if key.endswith(".endpoint"):
        value = validate_endpoint(value, origin=key.startswith("intelligence."))
    return value


def _scope_key(scope: str, key: str) -> None:
    if key == "telemetry.enabled" and scope != "user":
        raise AuditError("telemetry.enabled belongs to user settings")
    if key == "intelligence.access_presented" and scope != "user":
        raise AuditError("intelligence.access_presented belongs to user settings")
    if key == "traces.state" and scope != "project":
        raise AuditError("traces.state belongs to project settings")


def _settings(value: Any, scope: str) -> dict:
    if not isinstance(value, dict):
        raise AuditError("settings must be an object")
    result = {}
    for section, values in value.items():
        if section not in DEFAULTS or not isinstance(values, dict):
            raise AuditError("unsupported settings section")
        if section == "profiles":
            result[section] = {name: _profile(name, profile) for name, profile in values.items()}
            continue
        result[section] = {}
        for key, setting in values.items():
            dotted = f"{section}.{key}"
            _scope_key(scope, dotted)
            result[section][key] = _value(dotted, setting)
    return result


def _profile(name: str, profile: Any) -> dict:
    from agentagon.experiments.spec import validate_profile

    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
        raise AuditError(
            "profile name must start with a lowercase letter and use letters, digits, _ or -"
        )
    return validate_profile(copy.deepcopy(profile))


def credential(settings: dict, section: str, key: str = "api_key_env") -> str:
    """Resolve a credential only at its point of use; never include it in output."""
    name = settings[section][key]
    return os.environ.get(name, "").strip() if name else ""


class Config:
    def __init__(self) -> None:
        override = os.environ.get("AGENTAGON_CONFIG")
        config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        self.path = Path(override or str(Path(config_home) / "agentagon" / "config.json"))
        self.path = self.path.expanduser().absolute()

    def read(self) -> dict:
        if self.path.is_symlink():
            raise AuditError("Agentagon config must not be a symlink")
        if not self.path.exists():
            return {"version": 1, "user": {}, "projects": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise AuditError("unable to read Agentagon config") from exc
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "user", "projects"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or not isinstance(data["projects"], dict)
        ):
            raise AuditError("unsupported Agentagon config format")
        data["user"] = _settings(data["user"], "user")
        for root, settings in data["projects"].items():
            if not isinstance(root, str) or root != str(Path(root).expanduser().resolve()):
                raise AuditError("project settings must use resolved absolute checkout paths")
            data["projects"][root] = _settings(settings, "project")
        return data

    def effective(self, root: Path | None = None, overrides: dict | None = None) -> dict:
        data = self.read()
        result = copy.deepcopy(DEFAULTS)
        layers = [data["user"]]
        if root is not None:
            layers.append(data["projects"].get(str(Path(root).expanduser().resolve()), {}))
        for layer in layers:
            for section, values in layer.items():
                result[section].update(values)
        for dotted, value in (overrides or {}).items():
            validated = _value(dotted, value)
            section, key = dotted.split(".")
            result[section][key] = validated
        if result["traces"]["source"] == "langfuse" and result["traces"]["public_key_env"] is None:
            result["traces"]["public_key_env"] = "LANGFUSE_PUBLIC_KEY"
        return result

    def summary(self, root: Path | None = None) -> dict:
        from agentagon.usage import _summary

        settings = self.effective(root)
        intelligence = settings["intelligence"]
        traces = settings["traces"]
        intel_ready = bool(intelligence["endpoint"] and credential(settings, "intelligence"))
        return {
            "config_path": str(self.path),
            "workspace": str(Path(root).expanduser().resolve()) if root is not None else None,
            "settings": settings,
            "telemetry": _summary(self, settings),
            "intelligence": {
                "configured": intel_ready,
                "access_message": ACCESS_MESSAGE,
                "key_configured": bool(credential(settings, "intelligence")),
                "endpoint_configured": bool(intelligence["endpoint"]),
                "onboarding_pending": not intel_ready and not intelligence["access_presented"],
            },
            "traces": {
                "onboarding_pending": root is not None and traces["state"] == "unset",
                "enabled": traces["state"] == "enabled",
                "key_configured": bool(credential(settings, "traces")),
                "public_key_configured": bool(credential(settings, "traces", "public_key_env")),
                "provider_configured": bool(traces["source"] and traces["project"]),
            },
        }

    def profile(self, root: Path | None, name: str) -> dict:
        profiles = self.effective(root)["profiles"]
        if name not in profiles:
            raise AuditError(f"profile not found: {name}; configure it with agentagon setup")
        return copy.deepcopy(profiles[name])

    def update_profile(
        self, scope: str, name: str, profile: dict, root: Path | None = None
    ) -> dict:
        validated = _profile(name, profile)
        return self._update(scope, root, profile=(name, validated))

    def update(
        self,
        scope: str,
        root: Path | None = None,
        values: dict | None = None,
        unset: tuple[str, ...] = (),
    ) -> dict:
        if scope not in {"user", "project"}:
            raise AuditError("settings scope must be user or project")
        if scope == "project" and root is None:
            raise AuditError("project settings require a checkout root")
        values = values or {}
        if set(values).intersection(unset):
            raise AuditError("a setting cannot be set and unset together")
        for key in (*values, *unset):
            if key not in KEYS:
                raise AuditError(f"unsupported setting: {key}")
            _scope_key(scope, key)
        validated = {key: _value(key, value) for key, value in values.items()}
        return self._update(scope, root, validated, unset)

    def _update(
        self,
        scope: str,
        root: Path | None,
        values: dict | None = None,
        unset: tuple[str, ...] = (),
        profile: tuple[str, dict] | None = None,
    ) -> dict:
        if scope not in {"user", "project"}:
            raise AuditError("settings scope must be user or project")
        if scope == "project" and root is None:
            raise AuditError("project settings require a checkout root")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = self.read()
            if scope == "user":
                target = data["user"]
            else:
                target = data["projects"].setdefault(str(Path(root).expanduser().resolve()), {})
            if profile is not None:
                name, settings = profile
                target.setdefault("profiles", {})[name] = settings
            for dotted, value in (values or {}).items():
                section, key = dotted.split(".")
                target.setdefault(section, {})[key] = value
            for dotted in unset:
                section, key = dotted.split(".")
                target.get(section, {}).pop(key, None)
                if section in target and not target[section]:
                    del target[section]
            descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=self.path.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(encoded(data) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        if (values or {}).get("telemetry.enabled") is False:
            from agentagon.usage import _purge

            _purge(self)
        return self.summary(root)
