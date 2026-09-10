"""Shared changes test support."""

import subprocess


def git(root, *args):
    return subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
