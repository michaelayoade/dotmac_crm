from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_staging_sync_uses_shared_lock_and_single_admission_owner() -> None:
    script = (ROOT / "scripts/db_sync_to_staging.sh").read_text(encoding="utf-8")

    host_lock = "/var/lock/dotmac_staging_heavy.lock"
    admission_owner = "/home/dotmac/projects/dotmac_sub/scripts/staging_host_admission.py"

    assert host_lock in script
    assert admission_owner in script
    assert 'exec 8>"${STAGING_HOST_LOCK_FILE}"' in script
    assert 'STAGING_DB_CONTAINER="${TARGET_DB_CONTAINER}"' in script
    assert 'python3 "${STAGING_ADMISSION_COMMAND}"' in script
    assert script.index('exec 8>"${STAGING_HOST_LOCK_FILE}"') < script.index('exec 9>"${SYNC_LOCK_FILE}"')

    runbook = (ROOT / "docs/SEABONE_STAGING_SYNC.md").read_text(encoding="utf-8")
    assert host_lock in runbook
    assert "must not be moved to dotmac-observe" in runbook
