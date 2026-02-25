"""
Validate MCP DB integration prerequisites for the `omni-db` server.

Checks:
1. `.mcp.json` has an `omni-db` entry.
2. `DOTMAC_OMNI_DB_DSN` is set (env or .env).
3. DSN can connect and run safe read-only SELECT queries.
4. Optional: `npx @bytebase/dbhub` is runnable.

Usage:
    python scripts/check_mcp_db.py
    python scripts/check_mcp_db.py --skip-npx
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

try:
    import psycopg2 as pg
except ImportError:
    pg = None

ROOT = Path(__file__).resolve().parents[1]
MCP_CONFIG = ROOT / ".mcp.json"
ENV_FILE = ROOT / ".env"


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    sys.exit(1)


def _redact_dsn(dsn: str) -> str:
    try:
        parts = urlsplit(dsn)
        if not parts.username and not parts.password:
            return dsn
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        user = parts.username or ""
        netloc = f"{user}:***@{host}" if user else host
        return urlunsplit(
            (parts.scheme, netloc, parts.path, parts.query, parts.fragment)
        )
    except Exception:
        return "<redacted>"


def _load_dotenv() -> None:
    """Minimal .env loader (no external deps)."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _check_mcp_config() -> None:
    print("Checking .mcp.json ...")
    if not MCP_CONFIG.exists():
        _fail(".mcp.json is missing at project root")

    try:
        data = json.loads(MCP_CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f".mcp.json is invalid JSON: {exc}")

    server = data.get("mcpServers", {}).get("omni-db")
    if not isinstance(server, dict):
        _fail("`.mcp.json` is missing `mcpServers.omni-db`")

    command = server.get("command")
    args = server.get("args")
    if not command or not isinstance(args, list):
        _fail("`mcpServers.omni-db` must include `command` and array `args`")

    _ok("Found MCP server config for `omni-db`")

    arg_text = " ".join(str(a) for a in args)
    if "DOTMAC_OMNI_DB_DSN" in arg_text:
        _ok("MCP server reads DSN from DOTMAC_OMNI_DB_DSN")
    else:
        _warn("MCP args do not reference DOTMAC_OMNI_DB_DSN; verify secret handling")


def _check_db_connectivity(dsn: str) -> None:
    print("Checking database connectivity ...")
    if pg is None:
        _fail("psycopg2 is not installed. Run: pip install psycopg2-binary")

    redacted = _redact_dsn(dsn)
    _ok(f"Using DSN: {redacted}")

    try:
        conn = pg.connect(dsn, connect_timeout=5)
        cur = conn.cursor()

        cur.execute(
            "SELECT current_user, current_database(), current_setting('server_version')"
        )
        user, db, version = cur.fetchone()
        _ok(f"Connected to `{db}` as `{user}` (Postgres {version})")

        # Check PostGIS is available
        cur.execute("SELECT PostGIS_Version()")
        (postgis_ver,) = cur.fetchone()
        _ok(f"PostGIS available: {postgis_ver}")

        # Check a core table exists
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'people'
            )
        """)
        (has_persons,) = cur.fetchone()
        if not has_persons:
            _warn("`people` table not found (is this the right database?)")
        else:
            cur.execute("SELECT COUNT(*) FROM people")
            (count,) = cur.fetchone()
            _ok(f"`people` table is queryable (rows: {count})")

        # Check write privileges
        cur.execute("""
            SELECT
                has_table_privilege(current_user, 'people', 'INSERT'),
                has_table_privilege(current_user, 'people', 'UPDATE'),
                has_table_privilege(current_user, 'people', 'DELETE')
        """)
        can_insert, can_update, can_delete = cur.fetchone()
        if any((can_insert, can_update, can_delete)):
            _warn(
                "DB user has write privileges on `people`; "
                "a read-only user is recommended for MCP"
            )
        else:
            _ok("DB user does not have write privileges (read-only)")

        cur.close()
        conn.close()
    except Exception as exc:
        _fail(f"DB connectivity check failed: {exc}")


def _check_npx(skip_npx: bool) -> None:
    print("Checking npx / dbhub ...")
    if skip_npx:
        _warn("Skipping npx/dbhub check (--skip-npx)")
        return

    cmd = ["npx", "-y", "@bytebase/dbhub", "--help"]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        _fail("`npx` is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        _fail("`npx @bytebase/dbhub --help` timed out")

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:200]
        _fail(f"`@bytebase/dbhub` is not runnable via npx: {detail}")

    _ok("`npx @bytebase/dbhub` is runnable")


def main() -> None:
    print("=" * 60)
    print("  DotMac Omni CRM - MCP DB Integration Health Check")
    print("=" * 60)
    print()

    parser = argparse.ArgumentParser(description="Check MCP DB integration health")
    parser.add_argument(
        "--skip-npx",
        action="store_true",
        help="Skip npx/dbhub availability check",
    )
    args = parser.parse_args()

    _load_dotenv()
    _check_mcp_config()

    dsn = os.getenv("DOTMAC_OMNI_DB_DSN")
    if not dsn:
        _fail(
            "DOTMAC_OMNI_DB_DSN is not set.\n"
            "  Export it or add to .env:\n"
            "  DOTMAC_OMNI_DB_DSN=postgresql://claude_readonly:<password>@localhost:5432/dotmac_crm"
        )

    _check_db_connectivity(dsn)
    _check_npx(args.skip_npx)

    print()
    print("  All checks passed!")
    print()


if __name__ == "__main__":
    main()
