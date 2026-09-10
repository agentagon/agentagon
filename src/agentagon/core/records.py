"""Shared JSON records and versioned resource loading."""

import hashlib
import json
import math
import re
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

CONTRACT_VERSION = "1"
RUBRIC_VERSION = "1"
PROVIDERS = ("braintrust", "langfuse", "langsmith", "phoenix", "otlp")


class AuditError(ValueError):
    """An actionable invalid input or incompatible/stale record."""


def encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def identifier(kind: str, *values: Any) -> str:
    return f"{kind}_{digest(values)[:24]}"


def now() -> str:
    return datetime.now(UTC).isoformat()


def timestamp_ns(value: Any, *, nanos: bool = False) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise AuditError("boolean is not a timestamp")
    if isinstance(value, (int, float)) or (nanos and isinstance(value, str)):
        try:
            number = Decimal(str(value))
            if not number.is_finite():
                raise AuditError("timestamp must be finite")
            return int(number if nanos else number * 1_000_000_000)
        except InvalidOperation as exc:
            raise AuditError("invalid numeric timestamp") from exc
    if not isinstance(value, str):
        raise AuditError("timestamp must be ISO 8601 or epoch seconds")
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuditError("invalid ISO 8601 timestamp") from exc
    if date.tzinfo is None:
        raise AuditError("timestamps require an explicit timezone")
    delta = date.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    fraction = re.search(r"[T ]\d{2}:\d{2}:\d{2}[.,](\d+)", value)
    remainder = 0
    if fraction:
        digits = fraction[1]
        if any(digit != "0" for digit in digits[9:]):
            raise AuditError("timestamps support up to nanosecond precision")
        remainder = int(digits[:9].ljust(9, "0")) % 1000
    return (
        (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000 + remainder
    )


def number(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AuditError("measurement must be numeric")
    if not math.isfinite(value) or value < 0:
        raise AuditError("measurement must be finite and nonnegative")
    return value


def resource_path(relative: str) -> Path:
    candidates = (Path(__file__).resolve().parents[3], Path(sys.prefix) / "share/agentagon")
    for candidate in candidates:
        if (candidate / relative).exists():
            return candidate / relative
    raise AuditError(f"package resource missing: {relative}; reinstall agentagon")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=_invalid_constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"invalid UTF-8 JSON: {path.name}") from exc


def _invalid_constant(value: str) -> None:
    raise AuditError(f"non-finite JSON number: {value}")


@lru_cache(maxsize=16)
def _validator(name: str, definition: str | None) -> Draft202012Validator:
    schema = load_json(resource_path(f"contracts/v1/{name}.json"))
    if definition:
        schema = {"$defs": schema["$defs"], "$ref": f"#/$defs/{definition}"}
    return Draft202012Validator(schema)


def validate_record(name: str, record: Any, *, definition: str | None = None) -> None:
    errors = sorted(_validator(name, definition).iter_errors(record), key=lambda e: str(e.path))
    if errors:
        error = errors[0]
        # Do not echo arbitrary values (which may contain credentials) into diagnostics.
        location = ".".join(str(part) for part in error.path) or "document"
        raise AuditError(f"invalid {name} at {location}: failed {error.validator} validation")


def catalog() -> dict[str, Any]:
    return load_json(resource_path("signals/audit-v1.json"))
