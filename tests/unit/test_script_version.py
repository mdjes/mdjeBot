"""The manager script shows the release version, so it must move with pyproject.toml."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_manager_script_version_is_the_release_version() -> None:
    release = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    script = (ROOT / "scripts" / "mdjebot.sh").read_text(encoding="utf-8")
    match = re.search(r'^readonly SCRIPT_VERSION="([^"]+)"', script, re.MULTILINE)
    assert match, "SCRIPT_VERSION not found in scripts/mdjebot.sh"
    assert match.group(1) == release
