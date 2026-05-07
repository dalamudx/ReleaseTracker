#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"
BACKEND_PYPROJECT = ROOT / "backend" / "pyproject.toml"
BACKEND_INIT = ROOT / "backend" / "src" / "releasetracker" / "__init__.py"
FRONTEND_PACKAGE = ROOT / "frontend" / "package.json"
FRONTEND_LOCK = ROOT / "frontend" / "package-lock.json"
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def read_version() -> str:
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not VERSION_PATTERN.fullmatch(version):
        raise SystemExit(f"Invalid VERSION value: {version!r}")
    return version


def update_pyproject(version: str) -> None:
    content = BACKEND_PYPROJECT.read_text(encoding="utf-8")
    updated, count = re.subn(r'^version = ".*"$', f'version = "{version}"', content, count=1, flags=re.MULTILINE)
    if count == 0:
        raise SystemExit(f"No version field found in {BACKEND_PYPROJECT}")
    BACKEND_PYPROJECT.write_text(updated, encoding="utf-8")


def update_backend_init(version: str) -> None:
    content = BACKEND_INIT.read_text(encoding="utf-8")
    updated, count = re.subn(r'^__version__ = ".*"$', f'__version__ = "{version}"', content, count=1, flags=re.MULTILINE)
    if count == 0:
        raise SystemExit(f"No __version__ field found in {BACKEND_INIT}")
    BACKEND_INIT.write_text(updated, encoding="utf-8")


def update_package_json(path: Path, version: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["version"] = version
    if path.name == "package-lock.json":
        packages = payload.get("packages")
        if isinstance(packages, dict) and isinstance(packages.get(""), dict):
            packages[""]["version"] = version
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    if len(sys.argv) > 2:
        raise SystemExit("Usage: sync_version.py [version]")
    if len(sys.argv) == 2:
        candidate = sys.argv[1].strip()
        if not VERSION_PATTERN.fullmatch(candidate):
            raise SystemExit(f"Invalid version value: {candidate!r}")
        VERSION_FILE.write_text(f"{candidate}\n", encoding="utf-8")

    version = read_version()
    update_pyproject(version)
    update_backend_init(version)
    update_package_json(FRONTEND_PACKAGE, version)
    update_package_json(FRONTEND_LOCK, version)
    print(f"Synchronized project version to {version}")


if __name__ == "__main__":
    main()
