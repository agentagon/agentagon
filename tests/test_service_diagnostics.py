"""Diagnostics identify the loaded service without exposing process configuration."""

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
