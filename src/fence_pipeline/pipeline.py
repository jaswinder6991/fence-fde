"""Stage orchestration with resume and timings."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select

from fence_pipeline.allocate import allocate_unapplied_payments
from fence_pipeline.schedule import build_missing_schedules
from fence_pipeline.db import ensure_schema, get_dsn, session_scope
from fence_pipeline.evaluate import evaluate_run
from fence_pipeline.ingest import ingest_collections, ingest_receivables
from fence_pipeline.live_book import project_receivables_current
from fence_pipeline.models import PipelineRun, ReceivableEvent, RowException


def _require_single_writer(session, *, resume_run_id: int | None) -> None:
    """Live book has one writer: refuse a new run while another is running."""
    other = session.scalars(
        select(PipelineRun).where(PipelineRun.status == "running")
    ).all()
    if resume_run_id is not None:
        other = [r for r in other if r.run_id != resume_run_id]
    if other:
        ids = ", ".join(str(r.run_id) for r in other)
        raise SystemExit(
            f"another pipeline run is still running (run_id={ids}); "
            "finish or fail it before starting a new one"
        )
from fence_pipeline.report import (
    write_exceptions_report,
    write_payment_schedule_report,
    write_pool_html_report,
    write_pool_report,
    write_receivables_position_report,
)

STAGES = [
    "ingest_receivables",
    "ingest_collections",
    "project_current",
    "build_schedules",
    "allocate_payments",
    "evaluate",
    "write_reports",
    "complete",
]


def _set_stage(
    session,
    run: PipelineRun,
    stage: str,
    timings: dict,
    status: str = "running",
    error: str | None = None,
) -> None:
    run.current_stage = stage
    run.status = status
    run.error = error
    run.timings = timings
    if status == "completed":
        run.completed_at = datetime.now(timezone.utc)
    session.commit()


def _stage_index(stage: str) -> int:
    try:
        return STAGES.index(stage)
    except ValueError:
        return 0


def run_pipeline(
    receivables_path: Path,
    collections_path: Path,
    output_dir: Path,
    *,
    resume_run_id: int | None = None,
    dsn: str | None = None,
) -> int:
    receivables_path = receivables_path.resolve()
    collections_path = collections_path.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    db_url = dsn or get_dsn()
    ensure_schema(db_url)

    with session_scope(db_url) as session:
        _require_single_writer(session, resume_run_id=resume_run_id)
        if resume_run_id is not None:
            run = session.get(PipelineRun, resume_run_id)
            if run is None:
                raise SystemExit(f"unknown run_id {resume_run_id}")
            run_id = resume_run_id
            timings = dict(run.timings or {})
            start_from = _stage_index(run.current_stage)
            if run.status == "completed":
                print(f"run {run_id} already completed", flush=True)
                return run_id
            print(
                f"resuming run {run_id} from stage {run.current_stage}",
                flush=True,
            )
        else:
            run = PipelineRun(
                receivables_path=str(receivables_path),
                collections_path=str(collections_path),
                output_dir=str(output_dir),
                status="running",
                current_stage="start",
                timings={},
            )
            session.add(run)
            session.commit()
            run_id = run.run_id
            timings = {}
            start_from = 0
            print(f"started run {run_id}", flush=True)

        def should_run(stage: str) -> bool:
            return _stage_index(stage) >= start_from

        try:
            if should_run("ingest_receivables"):
                t0 = time.perf_counter()
                _set_stage(session, run, "ingest_receivables", timings)
                session.execute(
                    delete(ReceivableEvent).where(ReceivableEvent.run_id == run_id)
                )
                session.execute(
                    delete(RowException).where(
                        RowException.run_id == run_id,
                        RowException.source == "receivables",
                    )
                )
                session.commit()
                stats = ingest_receivables(session, run_id, receivables_path)
                timings["ingest_receivables_s"] = round(time.perf_counter() - t0, 3)
                timings["ingest_receivables"] = stats
                print(
                    f"ingest_receivables: {stats} ({timings['ingest_receivables_s']}s)",
                    flush=True,
                )

            if should_run("ingest_collections"):
                t0 = time.perf_counter()
                _set_stage(session, run, "ingest_collections", timings)
                session.execute(
                    delete(RowException).where(
                        RowException.run_id == run_id,
                        RowException.source == "collections",
                    )
                )
                session.commit()
                stats = ingest_collections(session, run_id, collections_path)
                timings["ingest_collections_s"] = round(time.perf_counter() - t0, 3)
                timings["ingest_collections"] = stats
                print(
                    f"ingest_collections: {stats} ({timings['ingest_collections_s']}s)",
                    flush=True,
                )

            if should_run("project_current"):
                t0 = time.perf_counter()
                _set_stage(session, run, "project_current", timings)
                stats = project_receivables_current(session, run_id)
                timings["project_current_s"] = round(time.perf_counter() - t0, 3)
                timings["project_current"] = stats
                print(
                    f"project_current: {stats} ({timings['project_current_s']}s)",
                    flush=True,
                )

            if should_run("build_schedules"):
                t0 = time.perf_counter()
                _set_stage(session, run, "build_schedules", timings)
                session.execute(
                    delete(RowException).where(
                        RowException.run_id == run_id,
                        RowException.source.in_(("schedule", "correction")),
                    )
                )
                session.commit()
                stats = build_missing_schedules(session, run_id)
                timings["build_schedules_s"] = round(time.perf_counter() - t0, 3)
                timings["build_schedules"] = stats
                print(
                    f"build_schedules: {stats} ({timings['build_schedules_s']}s)",
                    flush=True,
                )

            if should_run("allocate_payments"):
                t0 = time.perf_counter()
                _set_stage(session, run, "allocate_payments", timings)
                session.execute(
                    delete(RowException).where(
                        RowException.run_id == run_id,
                        RowException.source == "allocate",
                    )
                )
                session.commit()
                stats = allocate_unapplied_payments(session, run_id)
                timings["allocate_payments_s"] = round(time.perf_counter() - t0, 3)
                timings["allocate_payments"] = stats
                print(
                    f"allocate_payments: {stats} ({timings['allocate_payments_s']}s)",
                    flush=True,
                )

            if should_run("evaluate"):
                t0 = time.perf_counter()
                _set_stage(session, run, "evaluate", timings)
                stats = evaluate_run(session, run_id)
                timings["evaluate_s"] = round(time.perf_counter() - t0, 3)
                timings["evaluate"] = stats
                print(f"evaluate: {stats} ({timings['evaluate_s']}s)", flush=True)

            if should_run("write_reports"):
                t0 = time.perf_counter()
                _set_stage(session, run, "write_reports", timings)
                schedule_path = (
                    output_dir / f"payment_schedule_report_run_{run_id}.csv"
                )
                position_path = (
                    output_dir / f"receivables_position_report_run_{run_id}.csv"
                )
                pool_path = output_dir / f"pool_report_run_{run_id}.csv"
                pool_html_path = output_dir / f"pool_report_run_{run_id}.html"
                exceptions_path = (
                    output_dir / f"exceptions_report_run_{run_id}.csv"
                )
                n_schedule = write_payment_schedule_report(
                    session, run_id, schedule_path
                )
                n_position = write_receivables_position_report(
                    session, run_id, position_path
                )
                n_exceptions = write_exceptions_report(
                    session,
                    run_id,
                    exceptions_path,
                    detail_sheets={
                        "receivables": position_path.name,
                        "collections": schedule_path.name,
                        "schedule": position_path.name,
                        "allocate": schedule_path.name,
                        "reconcile": position_path.name,
                    },
                )
                write_pool_report(session, run_id, pool_path)
                write_pool_html_report(
                    session,
                    run_id,
                    pool_html_path,
                    sheet_names={
                        "pool_csv": pool_path.name,
                        "exceptions_csv": exceptions_path.name,
                        "position_csv": position_path.name,
                        "schedule_csv": schedule_path.name,
                    },
                )
                timings["write_reports_s"] = round(time.perf_counter() - t0, 3)
                timings["write_reports"] = {
                    "payment_schedule_rows": n_schedule,
                    "receivables_position_rows": n_position,
                    "exception_rows": n_exceptions,
                    "payment_schedule_report": str(schedule_path),
                    "receivables_position_report": str(position_path),
                    "exceptions_report": str(exceptions_path),
                    "pool_report": str(pool_path),
                    "pool_report_html": str(pool_html_path),
                }
                print(
                    f"write_reports: {schedule_path.name}, "
                    f"{position_path.name}, {exceptions_path.name}, "
                    f"{pool_path.name}, {pool_html_path.name} "
                    f"({timings['write_reports_s']}s)",
                    flush=True,
                )

            total = sum(
                v
                for k, v in timings.items()
                if k.endswith("_s") and isinstance(v, (int, float))
            )
            timings["total_s"] = round(total, 3)
            _set_stage(session, run, "complete", timings, status="completed")
            print(f"completed run {run_id} in {timings['total_s']}s", flush=True)
            _print_timing_table(timings)
            return run_id

        except Exception as exc:
            session.rollback()
            run = session.get(PipelineRun, run_id)
            if run is not None:
                _set_stage(
                    session,
                    run,
                    run.current_stage,
                    timings,
                    status="failed",
                    error=str(exc),
                )
            raise


def _print_timing_table(timings: dict) -> None:
    print("\nStage timings")
    print("-" * 40)
    for stage in STAGES:
        key = f"{stage}_s"
        if key in timings:
            print(f"{stage:24s} {timings[key]:8.3f}s")
    if "total_s" in timings:
        print("-" * 40)
        print(f"{'total':24s} {timings['total_s']:8.3f}s")
