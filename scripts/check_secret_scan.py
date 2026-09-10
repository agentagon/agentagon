"""Exercise the public-token exception without storing private credential fixtures."""

import argparse
import json
import subprocess
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gitleaks", help="Path to the verified Gitleaks executable")
    args = parser.parse_args()
    config = ROOT / ".gitleaks.toml"
    allowed = tomllib.loads(config.read_text())["rules"][0]["allowlists"][0]["regexes"][0]
    public = allowed.removeprefix("^").removesuffix("$")
    with tempfile.TemporaryDirectory(prefix="agentagon-secret-scan-check-") as temporary:
        root = Path(temporary)
        source = root / "src/agentagon/usage.py"
        source.parent.mkdir(parents=True)
        source.write_text(
            f'POSTHOG_PROJECT_TOKEN = "{public}"\n'
            + 'PRIVATE_API_KEY = "ghp_'
            + "A1b2C3d4E5f6" * 3
            + '"\n'
            + 'PRIVATE_API_TOKEN = "phx_'
            + "xT8aN4pD7sQ2mZ9kL5cR6vW3"
            + '"\n'
        )
        (root / "elsewhere.py").write_text(f'POSTHOG_PROJECT_TOKEN = "{public}"\n')
        report = root / "findings.json"
        result = subprocess.run(
            [
                args.gitleaks,
                "dir",
                ".",
                "--config",
                str(config),
                "--redact",
                "--no-banner",
                "--report-format",
                "json",
                "--report-path",
                str(report),
            ],
            cwd=root,
            capture_output=True,
            text=True,
        )
        findings = json.loads(report.read_text())
        found = {(item["File"], item["StartLine"]) for item in findings}
        expected = {
            ("src/agentagon/usage.py", 2),
            ("src/agentagon/usage.py", 3),
            ("elsewhere.py", 1),
        }
        if result.returncode != 1 or found != expected:
            raise ValueError(f"Public-token exception changed scanner coverage: {found}")
    print("Only the exact public token at its declared source path is excluded.")


if __name__ == "__main__":
    main()
