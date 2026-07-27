"""Validate or apply the reviewed Dotmac Sub native-chat history export."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
from dataclasses import asdict
from pathlib import Path

from app.db import SessionLocal
from app.services.crm.inbox.selfcare_history_import import (
    HistoryExport,
    HistoryImportError,
    import_history,
)


def _load(path: Path) -> HistoryExport:
    if path.is_symlink() or not path.is_file():
        raise HistoryImportError("Import input must be a regular file.")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise HistoryImportError("Import input must have mode 0600 or stricter.")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise HistoryImportError("Export payload must be a JSON object.")
    conversations = raw.get("conversations")
    canonical = json.dumps(conversations, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    if digest != raw.get("content_sha256"):
        raise HistoryImportError("Export content digest does not match.")
    return HistoryExport.model_validate(raw)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    history = _load(args.input)
    with SessionLocal() as db:
        try:
            result = import_history(db, history, apply=args.apply)
        except Exception:
            db.rollback()
            raise
    print(json.dumps(asdict(result), sort_keys=True))


if __name__ == "__main__":
    main()
