"""CLI entrypoint for the Starfleet facility pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from fence_pipeline.pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fence-pipeline",
        description="Ingest Starfleet exports and produce facility reports.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the pipeline on export CSVs")
    run.add_argument("--receivables", required=True, type=Path)
    run.add_argument("--collections", required=True, type=Path)
    run.add_argument("--output-dir", required=True, type=Path)
    run.add_argument(
        "--resume",
        type=int,
        default=None,
        metavar="RUN_ID",
        help="Resume a failed run from its current stage",
    )
    run.add_argument(
        "--database-url",
        default=None,
        help="Postgres DSN (default: env FENCE_DATABASE_URL or local Compose)",
    )
    return parser


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise SystemExit(f"{label} file not found: {path}")


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    _require_file(args.receivables, "receivables")
    _require_file(args.collections, "collections")
    run_id = run_pipeline(
        args.receivables,
        args.collections,
        args.output_dir,
        resume_run_id=args.resume,
        dsn=args.database_url,
    )
    print(f"run_id={run_id}")


if __name__ == "__main__":
    main()
