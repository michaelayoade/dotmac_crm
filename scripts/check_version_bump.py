#!/usr/bin/env python3
"""Require an app version bump when production-impacting files change."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = "pyproject.toml"
VERSION_RE = re.compile(r'^version\s*=\s*"(?P<version>\d+\.\d+\.\d+)"\s*$')
PRODUCTION_PATHS = (
    "alembic/",
    "app/",
    "Dockerfile",
    "docker-compose.yml",
    "poetry.lock",
    "pyproject.toml",
    "static/",
    "templates/",
)
VERSION_ONLY_PATHS = {PYPROJECT}


def run_git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True)


def changed_files(base_ref: str, head_ref: str) -> list[str]:
    output = run_git(["diff", "--name-only", base_ref, head_ref])
    return [line.strip() for line in output.splitlines() if line.strip()]


def read_file_at_ref(ref: str, path: str) -> str:
    return run_git(["show", f"{ref}:{path}"])


def version_from_text(text: str) -> str:
    in_poetry = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "[tool.poetry]":
            in_poetry = True
            continue
        if in_poetry and stripped.startswith("["):
            break
        if in_poetry:
            match = VERSION_RE.match(stripped)
            if match:
                return match.group("version")
    raise SystemExit("Could not find a SemVer version in [tool.poetry].")


def parse_version(version: str) -> tuple[int, int, int]:
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def is_production_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix) for prefix in PRODUCTION_PATHS)


def needs_version_bump(paths: list[str]) -> bool:
    production_paths = [path for path in paths if is_production_path(path)]
    return bool(set(production_paths) - VERSION_ONLY_PATHS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check that production-impacting changes bump the app version.")
    parser.add_argument("base_ref", help="Base Git ref or SHA.")
    parser.add_argument("head_ref", help="Head Git ref or SHA.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    paths = changed_files(args.base_ref, args.head_ref)

    if not needs_version_bump(paths):
        print("No production-impacting changes detected; version bump not required.")  # noqa: T201
        return 0

    base_version = version_from_text(read_file_at_ref(args.base_ref, PYPROJECT))
    head_version = version_from_text(read_file_at_ref(args.head_ref, PYPROJECT))

    if parse_version(head_version) <= parse_version(base_version):
        changed = "\n".join(f"  - {path}" for path in paths if is_production_path(path))
        print(  # noqa: T201
            "Production-impacting changes require a pyproject.toml version bump.\n"
            f"Base version: {base_version}\n"
            f"Head version: {head_version}\n"
            "Changed production paths:\n"
            f"{changed}\n\n"
            "Run one of:\n"
            "  scripts/bump_version.py patch\n"
            "  scripts/bump_version.py minor\n"
            "  scripts/bump_version.py major"
        )
        return 1

    print(f"Version bump detected: {base_version} -> {head_version}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
