"""Celery application shared by the API enqueue path and review workers."""

from celery import Celery

from app.core.config import get_settings


settings = get_settings()
celery_app = Celery(
    "procurement_review",
    broker=settings.celery_broker_url or settings.redis_url,
    include=["app.workers.review_tasks"],
)
celery_app.conf.update(
    task_default_queue=settings.celery_queue,
    task_routes={"app.workers.review_tasks.run_review_task": {"queue": settings.celery_queue}},
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_time_limit=settings.celery_task_time_limit_seconds,
    task_soft_time_limit=settings.celery_task_soft_time_limit_seconds,
)
