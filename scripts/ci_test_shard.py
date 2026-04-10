"""Emit a deterministic pytest file shard for CI."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: ci_test_shard.py <shard_index> <shard_count>")

    shard_index = int(sys.argv[1])
    shard_count = int(sys.argv[2])
    if shard_count <= 0:
        raise SystemExit("shard_count must be > 0")
    if shard_index < 0 or shard_index >= shard_count:
        raise SystemExit("shard_index must be within [0, shard_count)")

    files = sorted(str(path) for path in Path("tests").rglob("test_*.py"))
    shard_files = files[shard_index::shard_count]
    if not shard_files:
        return 0

    sys.stdout.write("\n".join(shard_files))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
