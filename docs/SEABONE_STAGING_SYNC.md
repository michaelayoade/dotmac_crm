# Seabone staging database sync

The nightly CRM production-to-staging refresh is a heavy, trusted-data
operation. It is serialized with every CRM, ERP, and Sub staging deployment,
restore, and migration through `/var/lock/dotmac_staging_heavy.lock`.

`scripts/db_sync_to_staging.sh` acquires that host-wide lock before its existing
repo-specific sync lock and before creating a dump. It then delegates the host
resource decision to the typed owner at
`/home/dotmac/projects/dotmac_sub/scripts/staging_host_admission.py`, passing
`dotmac_omni_db` as the database whose health must be proven. Missing lock,
missing admission owner, an unhealthy database, high load/swap/I/O pressure,
blocked processes, or another heavy job all fail closed without creating a
dump.

The systemd service account must be able to open the pre-provisioned shared
lock file. The tracked Sub admission owner must be present at the exact path;
do not copy it into this repository and create a second policy implementation.

Production data remains inside the approved staging trust boundary. The dump
and restore job must not be moved to dotmac-observe; a future move requires a
separately approved trusted migration host and an updated systemd owner.
