"""Limits for diagnostic task evidence. Measurements remain a separate contract."""

from agentagon.core.records import AuditError

DEFAULT_LIMITS = {
    "max_events": 2000,
    "max_event_bytes": 262144,
    "max_artifacts": 20,
    "max_artifact_bytes": 262144,
    "max_total_artifact_bytes": 1048576,
}
MAXIMUM_LIMITS = {
    "max_events": 10000,
    "max_event_bytes": 524288,
    "max_artifacts": 100,
    "max_artifact_bytes": 1048576,
    "max_total_artifact_bytes": 2097152,
}


def validate_limits(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != set(DEFAULT_LIMITS):
        raise AuditError("evidence limits must declare every event and artifact bound")
    for key, number in value.items():
        if type(number) is not int or not 1 <= number <= MAXIMUM_LIMITS[key]:
            raise AuditError(f"evidence limit {key} must be between 1 and {MAXIMUM_LIMITS[key]}")
    if value["max_artifact_bytes"] > value["max_total_artifact_bytes"]:
        raise AuditError("per-artifact limit exceeds total artifact limit")
    return dict(value)
