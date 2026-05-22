#!/usr/bin/env python3
"""Bump the application version in pyproject.toml.

Examples:
  scripts/bump_version.py patch
  scripts/bump_version.py minor --tag
  scripts/bump_version.py 1.2.0 --tag --tag-prefix v
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
VERSION_RE = re.compile(r'^version\s*=\s*"(?P<version>\d+\.\d+\.\d+)"\s*$')


def read_current_version() -> tuple[str, int]:
    in_poetry = False
    for line_no, line in enumerate(PYPROJECT.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if stripped == "[tool.poetry]":
            in_poetry = True
            continue
        if in_poetry and stripped.startswith("["):
            break
        if in_poetry:
            match = VERSION_RE.match(stripped)
            if match:
                return match.group("version"), line_no
    raise SystemExit("Could not find a SemVer version in [tool.poetry].")


def parse_version(version: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit(f"Invalid version '{version}'. Use MAJOR.MINOR.PATCH, for example 0.1.1.")
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def next_version(current: str, bump: str) -> str:
    major, minor, patch = parse_version(current)
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    parse_version(bump)
    return bump


def update_pyproject(old_version: str, new_version: str) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    in_poetry = False
    replaced = False

    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[tool.poetry]":
            in_poetry = True
            continue
        if in_poetry and stripped.startswith("["):
            break
        if in_poetry and VERSION_RE.match(stripped):
            lines[index] = line.replace(f'"{old_version}"', f'"{new_version}"', 1)
            replaced = True
            break

    if not replaced:
        raise SystemExit("Could not update [tool.poetry] version.")

    PYPROJECT.write_text("".join(lines), encoding="utf-8")


def run_git(args: list[str]) -> None:
    subprocess.run(["git", *args], cwd=ROOT, check=True)


def ensure_tag_missing(tag_name: str) -> None:
    result = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag_name}"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        raise SystemExit(f"Tag '{tag_name}' already exists.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bump pyproject.toml version and optionally create a Git tag.")
    parser.add_argument("bump", help="One of: major, minor, patch, or an explicit MAJOR.MINOR.PATCH version.")
    parser.add_argument("--tag", action="store_true", help="Create an annotated Git tag after updating pyproject.toml.")
    parser.add_argument("--tag-prefix", default="v", help="Tag prefix to use with --tag. Default: v")
    parser.add_argument("--message", help="Annotated tag message. Default: Release <tag>")
    parser.add_argument("--dry-run", action="store_true", help="Print the version change without writing files or tags.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    current, line_no = read_current_version()
    new_version = next_version(current, args.bump)
    tag_name = f"{args.tag_prefix}{new_version}"

    if current == new_version:
        raise SystemExit(f"Version is already {new_version}.")

    print(f"{current} -> {new_version} ({PYPROJECT.relative_to(ROOT)}:{line_no})")  # noqa: T201

    if args.tag:
        ensure_tag_missing(tag_name)
        print(f"tag: {tag_name}")  # noqa: T201

    if args.dry_run:
        return 0

    update_pyproject(current, new_version)

    if args.tag:
        run_git(["tag", "-a", tag_name, "-m", args.message or f"Release {tag_name}"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
