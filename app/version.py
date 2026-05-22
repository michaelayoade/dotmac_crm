from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
FALLBACK_VERSION = "0.0.0"


@lru_cache(maxsize=1)
def get_app_version() -> str:
    try:
        data: dict[str, Any] = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        version = data["tool"]["poetry"]["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        return FALLBACK_VERSION
    return str(version)
