"""Diagnostics identify the loaded service without exposing process configuration."""

import sys

import pytest

from agentagon.core.records import AuditError
from agentagon.storage.state import atomic_write
from agentagon.workflows import service as service_module
from agentagon.workflows import service_host
from agentagon.workflows.service import Application


def test_diagnostics_report_loaded_assets_and_state_contracts(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTAGON_BUILD_ID", "local-review")
    monkeypatch.setenv("AGENTAGON_API_KEY", "must-not-leak")
    application = Application(tmp_path / "state", execute=lambda *_: {})
    try:
        first = application.diagnostics()
        second = application.diagnostics()
    finally:
        application.close()

    assert first == second
    assert first["application"] == "agentagon"
    assert first["package_version"]
    assert first["build_id"] == "local-review"
    assert len(first["frontend_asset_version"]) == 16
    assert first["state_contracts"] == {"evidence": "1", "workspace": 2, "metadata": 4}
    assert "must-not-leak" not in str(first)


def test_client_rejects_a_detached_service_from_another_build(monkeypatch):
    monkeypatch.setattr(
        service_host,
        "service_identity",
        lambda: {
            "package_version": "2.0.0",
            "build_id": "current-build",
            "frontend_asset_version": "current-assets",
        },
    )

    with pytest.raises(AuditError, match="different package version, build id, frontend asset"):
        service_host._verify_service(
            {
                "application": "agentagon",
                "package_version": "1.0.0",
                "build_id": "old-build",
                "frontend_asset_version": "old-assets",
            }
        )


def test_service_start_for_mcp_does_not_register_the_current_directory(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("AGENTAGON_APP_STATE", str(state))
    expected = {
        "package_version": "2.0.0",
        "build_id": "current-build",
        "frontend_asset_version": "current-assets",
    }
    monkeypatch.setattr(service_host, "service_identity", lambda: expected)
    monkeypatch.setattr(
        service_host.ServiceClient,
        "request",
        lambda *_args, **_kwargs: {"application": "agentagon", **expected},
    )
    commands = []

    def start(command, **_kwargs):
        commands.append(command)
        atomic_write(state / "instance.json", {"port": 59116, "token": "test-token"})

    monkeypatch.setattr("subprocess.Popen", start)

    service_host.ensure_service()

    assert commands == [
        [
            sys.executable,
            "-m",
            "agentagon",
            "serve",
            "--without-project",
        ]
    ]
    assert "--workspace" not in commands[0]


def test_supplied_executor_is_the_configured_brain_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(
        service_module,
        "detect_backends",
        lambda: [
            {
                "agent": "codex",
                "available": False,
                "authenticated": None,
                "unavailable_reason": "Codex is not installed.",
            },
            {"agent": "claude", "available": False, "authenticated": False},
        ],
    )
    application = Application(tmp_path / "state", execute=lambda *_: {})
    try:
        assistants = {item["id"]: item for item in application.assistants()["assistants"]}
    finally:
        application.close()

    assert assistants["codex"]["available"] is True
    assert assistants["codex"]["authenticated"] is True
    assert assistants["codex"]["message"] is None
    assert assistants["claude"]["available"] is False
