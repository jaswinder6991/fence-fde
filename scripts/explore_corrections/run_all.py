#!/usr/bin/env python3
"""Run all corrections CSV explorations and print evidence to stdout.

Usage (from repo root):
  python scripts/explore_corrections/run_all.py

Read-only: loads data/corrections/*.csv and builds expected schedules via
fence_pipeline.schedule.build_schedule. Does not touch the database or
change pipeline behavior.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from collections_diff import run as run_collections  # noqa: E402
from common import section  # noqa: E402
from outliers import run as run_outliers  # noqa: E402
from receivable_diff import run as run_receivables  # noqa: E402
from schedules_and_cash import run as run_schedules  # noqa: E402


def main() -> None:
    section("Corrections data exploration (read-only)")
    print("Data dir: data/corrections/")
    print("No DB writes. No pipeline schedule rebuild.")

    run_receivables()
    run_collections()
    run_schedules()
    findings = run_outliers()

    section("Done")
    print(f"Outlier findings to discuss before implementation: {len(findings)}")


if __name__ == "__main__":
    main()
