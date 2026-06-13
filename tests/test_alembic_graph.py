"""Guard the Alembic migration graph.

A duplicate revision id (two migration files declaring the same ``revision``)
silently corrupts the graph into a cycle, so ``alembic upgrade heads`` fails —
which only surfaces at deploy/restart time, not in the normal test run. These
tests make CI fail fast instead.
"""

import re
from collections import Counter
from pathlib import Path

_VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_REVISION_RE = re.compile(r"^revision = [\"']([^\"']+)[\"']", re.MULTILINE)
_DOWN_RE = re.compile(r"^down_revision = [\"']([^\"']+)[\"']", re.MULTILINE)


def _revisions() -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for path in _VERSIONS_DIR.glob("*.py"):
        text = path.read_text()
        match = _REVISION_RE.search(text)
        if match:
            out.append((match.group(1), path))
    return out


def test_no_duplicate_revision_ids():
    counts = Counter(rev for rev, _ in _revisions())
    dupes = {rev: count for rev, count in counts.items() if count > 1}
    assert not dupes, f"Duplicate Alembic revision ids: {dupes}"


def test_migration_graph_has_single_acyclic_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(_VERSIONS_DIR.parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(_VERSIONS_DIR.parent))
    script = ScriptDirectory.from_config(cfg)
    # walk_revisions raises CycleDetected if the graph is cyclic.
    list(script.walk_revisions())
    heads = script.get_heads()
    assert len(heads) == 1, f"Expected exactly one head, found {len(heads)}: {heads}"
