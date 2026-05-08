from __future__ import annotations

import argparse
import json
import sys

from app.db import SessionLocal
from app.services.ai.provider_health import render_health_report_text, run_provider_healthcheck


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Operational AI provider health smoke test")
    parser.add_argument("--mode", choices=("primary", "secondary", "fallback"), default="primary")
    parser.add_argument(
        "--simulate-primary-failure",
        choices=("none", "timeout", "auth"),
        default="none",
        help="Only applies to --mode fallback. Simulates a primary failure to exercise secondary failover.",
    )
    parser.add_argument(
        "--ignore-circuit",
        action="store_true",
        help="Probe the provider even if the gateway circuit is currently open.",
    )
    parser.add_argument("--json", action="store_true", help="Print structured JSON instead of text")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db = SessionLocal()
    try:
        report = run_provider_healthcheck(
            db,
            mode=args.mode,
            respect_circuit=not args.ignore_circuit,
            simulate_primary_failure=args.simulate_primary_failure,
        )
    finally:
        db.close()

    if args.json:
        sys.stdout.write(f"{json.dumps(report.to_dict(), indent=2, sort_keys=True)}\n")
    else:
        sys.stdout.write(f"{render_health_report_text(report)}\n")
    return 0 if report.overall_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
