"""Celery task for the long-running procurement review workflow.

The message is deliberately identifier-only. PostgreSQL remains the source of
truth for task state, claims, progress, findings, and the final report.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded

from app.core.config import get_settings
from app.services.procurement.procurement_workflow import run_review_workflow

from .celery_app import celery_app


logger = logging.getLogger(__name__)
TERMINAL_STATES = {"completed", "final_locked", "operator_review", "applicability_review", "cancelled"}

if TYPE_CHECKING:
    from app.services.procurement.review import ProcurementReviewService


def _now() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _now().isoformat()


def _claim_review_task(
    service: ProcurementReviewService,
    project_id: str,
    task_id: str,
    run_id: str,
    celery_task_id: str,
) -> bool:
    """Atomically claim one review attempt in PostgreSQL.

    A duplicate delivery with the same run ID is skipped after completion. A
    worker that died is reclaimable after the lease expires, while a live
    worker prevents a second delivery from running the workflow concurrently.
    """

    settings = get_settings()
    now = _now()
    claimed = False

    def persist(state: dict[str, Any]) -> None:
        nonlocal claimed
        task = next((item for item in state["tasks"] if item["id"] == task_id and item["project_id"] == project_id), None)
        if not task:
            return
        if task.get("worker_finished_run_id") == run_id or task.get("status") in TERMINAL_STATES:
            return

        active_run_id = task.get("worker_run_id")
        heartbeat_raw = task.get("worker_heartbeat_at")
        try:
            heartbeat_age = (now - datetime.fromisoformat(heartbeat_raw)).total_seconds()
        except (TypeError, ValueError):
            heartbeat_age = settings.review_worker_lease_seconds + 1
        active_task_id = task.get("worker_task_id")
        if active_run_id and active_task_id != celery_task_id and heartbeat_age < settings.review_worker_lease_seconds:
            return

        task["worker_run_id"] = run_id
        task["worker_task_id"] = celery_task_id
        task["worker_heartbeat_at"] = now.isoformat()
        task["updated_at"] = now.isoformat()
        claimed = True

    service.repository.transaction(persist)
    return claimed


def _heartbeat_review_task(
    service: ProcurementReviewService,
    project_id: str,
    task_id: str,
    run_id: str,
    celery_task_id: str,
) -> bool:
    touched = False

    def persist(state: dict[str, Any]) -> None:
        nonlocal touched
        task = next((item for item in state["tasks"] if item["id"] == task_id and item["project_id"] == project_id), None)
        if task and task.get("worker_run_id") == run_id and task.get("worker_task_id") == celery_task_id:
            task["worker_heartbeat_at"] = _now_iso()
            task["updated_at"] = task["worker_heartbeat_at"]
            touched = True

    service.repository.transaction(persist)
    return touched


def _finish_review_task(
    service: ProcurementReviewService,
    project_id: str,
    task_id: str,
    run_id: str,
    celery_task_id: str,
) -> None:
    def persist(state: dict[str, Any]) -> None:
        task = next((item for item in state["tasks"] if item["id"] == task_id and item["project_id"] == project_id), None)
        if task and task.get("worker_run_id") == run_id and task.get("worker_task_id") == celery_task_id:
            task["worker_finished_run_id"] = run_id
            task.pop("worker_run_id", None)
            task.pop("worker_task_id", None)
            task.pop("worker_heartbeat_at", None)

    service.repository.transaction(persist)


def _start_heartbeat(
    service: ProcurementReviewService,
    project_id: str,
    task_id: str,
    run_id: str,
    celery_task_id: str,
) -> tuple[threading.Event, threading.Thread]:
    stop = threading.Event()
    interval = get_settings().review_heartbeat_interval_seconds

    def beat() -> None:
        while not stop.wait(interval):
            try:
                if not _heartbeat_review_task(service, project_id, task_id, run_id, celery_task_id):
                    return
            except Exception:
                logger.exception("review heartbeat failed task_id=%s run_id=%s", task_id, run_id)

    thread = threading.Thread(target=beat, name=f"review-heartbeat-{task_id}", daemon=False)
    thread.start()
    return stop, thread


def _stop_heartbeat(stop: threading.Event, thread: threading.Thread) -> None:
    stop.set()
    thread.join(timeout=1)


def _load_review_inputs(
    service: ProcurementReviewService,
    project_id: str,
    task_id: str,
    run_id: str,
) -> tuple[str, str | None, dict[str, Any], dict[str, dict[str, Any]] | None]:
    task, _ = service._task(project_id, task_id)
    document = task.get("document") or {}
    document_path = document.get("path")
    if not document_path:
        raise ValueError(f"review task has no document path: {task_id}")
    project, _ = service._project_row(project_id)
    context = {"project": {"name": project.get("name"), "project_code": project.get("project_code")}, "title": task.get("title")}
    if not Path(document_path).is_file():
        relative_key = re.split(r"[\\/]+data[\\/]+uploads[\\/]", document_path, maxsplit=1)[-1]
        relative_key = relative_key.replace("\\", "/")
        document_path = str(service.storage.path(relative_key))
    if not Path(document_path).is_file():
        raise FileNotFoundError(f"review input file does not exist: {document_path}")
    return document_path, task.get("engine_run_id") or run_id, context, task.get("legal_applicability_confirmations")


class ReviewTask(Task):
    autoretry_for = (ConnectionError, TimeoutError)
    retry_backoff = True
    retry_backoff_max = get_settings().celery_task_retry_backoff_max_seconds
    retry_jitter = True
    max_retries = get_settings().celery_task_max_retries


@celery_app.task(
    bind=True,
    base=ReviewTask,
    name="app.workers.review_tasks.run_review_task",
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
)
def run_review_task(self: ReviewTask, project_id: str, task_id: str, run_id: str) -> dict[str, str]:
    """Run one review attempt; all arguments are small PostgreSQL identifiers."""

    for value, name in ((project_id, "project_id"), (task_id, "task_id"), (run_id, "run_id")):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")

    from app.services.procurement.review import ProcurementReviewService

    service = ProcurementReviewService()
    celery_task_id = self.request.id or f"local:{run_id}"
    if not _claim_review_task(service, project_id, task_id, run_id, celery_task_id):
        return {"status": "skipped", "task_id": task_id, "run_id": run_id}

    stop, heartbeat = _start_heartbeat(service, project_id, task_id, run_id, celery_task_id)
    finished = False
    try:
        document_path, engine_run_id, context, confirmations = _load_review_inputs(service, project_id, task_id, run_id)
        run_review_workflow(
            project_id,
            task_id,
            document_path,
            service._store_review_results,
            service._fail_review_task,
            service._update_review_progress,
            engine_run_id,
            context,
            service._pause_for_legal_applicability,
            confirmations,
        )
        finished = True
        return {"status": "finished", "task_id": task_id, "run_id": run_id}
    except SoftTimeLimitExceeded:
        service._fail_review_task(project_id, task_id, "审查任务超过软超时限制")
        raise
    except Exception as exc:
        service._fail_review_task(project_id, task_id, str(exc)[:500])
        logger.exception("review_task_failed project_id=%s task_id=%s run_id=%s", project_id, task_id, run_id)
        return {"status": "failed", "task_id": task_id, "run_id": run_id}
    finally:
        _stop_heartbeat(stop, heartbeat)
        if finished:
            _finish_review_task(service, project_id, task_id, run_id, celery_task_id)


def enqueue_review(project_id: str, task_id: str, run_id: str):
    """Enqueue an identifier-only review message and return Celery's handle."""

    for value, name in ((project_id, "project_id"), (task_id, "task_id"), (run_id, "run_id")):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
    return run_review_task.apply_async(
        args=(project_id, task_id, run_id),
        task_id=f"review:{run_id}",
        queue=get_settings().celery_queue,
    )
