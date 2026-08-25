from __future__ import annotations

import importlib
import sys
import types
from types import SimpleNamespace

import pytest

from app.core.config import Settings


def load_worker_module(monkeypatch):
    """Keep these infrastructure tests independent from the DB driver/runtime."""
    service_module = types.ModuleType("app.services.procurement.review")
    service_module.ProcurementReviewService = object
    monkeypatch.setitem(sys.modules, "app.services.procurement.review", service_module)
    workflow_module = types.ModuleType("app.services.procurement.procurement_workflow")
    workflow_module.run_review_workflow = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "app.services.procurement.procurement_workflow", workflow_module)
    sys.modules.pop("app.workers.review_tasks", None)
    return importlib.import_module("app.workers.review_tasks")


def test_celery_settings_are_reliable(monkeypatch):
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    settings = Settings()
    assert settings.redis_url.startswith("redis://")
    assert settings.celery_task_soft_time_limit_seconds < settings.celery_task_time_limit_seconds
    assert settings.review_heartbeat_interval_seconds < settings.review_worker_lease_seconds


def test_enqueue_sends_only_identifiers(monkeypatch):
    review_tasks = load_worker_module(monkeypatch)

    captured = {}

    def apply_async(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=kwargs["task_id"])

    monkeypatch.setattr(review_tasks.run_review_task, "apply_async", apply_async)
    result = review_tasks.enqueue_review("project-1", "task-1", "run-1")

    assert result.id == "review:run-1"
    assert captured["args"] == ("project-1", "task-1", "run-1")
    assert set(captured) == {"args", "task_id", "queue"}


def test_enqueue_rejects_payloads_that_are_not_identifiers(monkeypatch):
    enqueue_review = load_worker_module(monkeypatch).enqueue_review

    with pytest.raises(ValueError):
        enqueue_review("project-1", "task-1", "")


def test_worker_does_not_use_a_result_backend():
    from app.workers.celery_app import celery_app

    assert celery_app.conf.task_ignore_result is True
    assert celery_app.conf.result_backend in (None, "disabled")
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_heartbeat_thread_is_non_daemon_and_is_cleaned_up(monkeypatch):
    workers = load_worker_module(monkeypatch)
    monkeypatch.setattr(workers, "get_settings", lambda: SimpleNamespace(review_heartbeat_interval_seconds=3600))

    stop, thread = workers._start_heartbeat(SimpleNamespace(), "project-1", "task-1", "run-1", "celery-1")
    assert thread.daemon is False

    workers._stop_heartbeat(stop, thread)
    assert not thread.is_alive()


def test_database_claim_makes_duplicate_delivery_idempotent(monkeypatch):
    workers = load_worker_module(monkeypatch)

    state = {"tasks": [{"id": "task-1", "project_id": "project-1", "status": "reviewing"}]}

    class Repository:
        def transaction(self, mutate):
            mutate(state)

    service = SimpleNamespace(repository=Repository())
    assert workers._claim_review_task(service, "project-1", "task-1", "run-1", "celery-1") is True
    assert workers._claim_review_task(service, "project-1", "task-1", "run-1", "celery-2") is False
    workers._finish_review_task(service, "project-1", "task-1", "run-1", "celery-1")
    assert workers._claim_review_task(service, "project-1", "task-1", "run-1", "celery-3") is False
