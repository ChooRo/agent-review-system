"""安全地从 JSON 开发存储中删除明确选定的任务。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
from app.repositories.postgres.review_repository import PostgresReviewRepository
from scripts.delete_projects import CleanupError, contains_id, validate_deleted

RUNNING_STATUSES = {"queued", "parsing", "reviewing"}


def make_plan(state: dict, task_ids: set[str]) -> dict:
    available = {row["id"] for row in state["tasks"]}
    missing = task_ids - available
    if missing:
        raise CleanupError(f"task_id does not exist: {', '.join(sorted(missing))}")
    finding_ids = {row["id"] for row in state["findings"] if row.get("task_id") in task_ids}
    related = task_ids | finding_ids
    cleaned = {
        name: [row for row in state[name] if not contains_id(row, related)]
        for name in ("tasks", "findings", "comments", "events")
    }
    cleaned.update({
        "projects": list(state["projects"]),
        "audit": [row for row in state["audit"] if not contains_id(row, related)],
        "idempotency": [row for row in state["idempotency"] if not contains_id(row, related)],
    })
    return {"task_ids": task_ids, "finding_ids": finding_ids, "state": deepcopy(state), "cleaned": cleaned}


def configured_data_dir() -> Path:
    os.chdir(BACKEND_ROOT)
    get_settings.cache_clear()
    value = Path(get_settings().data_dir)
    return value if value.is_absolute() else BACKEND_ROOT / value


def print_plan(plan: dict, dry_run: bool) -> None:
    print("DRY-RUN" if dry_run else "DELETE")
    print("task_ids:", ", ".join(sorted(plan["task_ids"])))
    print("tasks:", len(plan["task_ids"]))
    print("findings:", len(plan["finding_ids"]))
    for name in ("comments", "events", "audit", "idempotency"):
        print(f"{name}:", len(plan["state"][name]) - len(plan["cleaned"][name]))


def delete(repository: PostgresReviewRepository, data_dir: Path, plan: dict) -> Path:
    backup_dir = data_dir / "backups" / f"delete_tasks_{datetime.now(UTC):%Y%m%dT%H%M%SZ}_{uuid4().hex[:8]}"
    backup_dir.mkdir(parents=True)
    (backup_dir / "review_state.json").write_text(json.dumps(plan["state"], ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        repository.commit(plan["cleaned"])
        validate_deleted(repository.load(), type("Plan", (), {
            "project_ids": set(), "task_ids": plan["task_ids"], "finding_ids": plan["finding_ids"],
            "comment_ids": set(), "event_ids": set(),
        })())
    except Exception as exc:
        repository.commit(plan["state"])
        raise CleanupError(f"delete failed; state backed up at {backup_dir}: {exc}") from exc
    return backup_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely delete explicitly selected tasks.")
    parser.add_argument("--list", action="store_true", help="List tasks without changing data.")
    parser.add_argument("--task-id", action="append", default=[], help="Exact task ID; may be repeated.")
    parser.add_argument("--confirm", action="store_true", help="Actually delete after backup.")
    parser.add_argument("--force", action="store_true", help="Allow deletion of running tasks.")
    args = parser.parse_args(argv)
    data_dir = configured_data_dir()
    repository = PostgresReviewRepository(data_dir)
    if args.list:
        if args.task_id:
            parser.error("--list cannot be combined with --task-id")
        for row in repository.load()["tasks"]:
            print(f"{row['id']}\t{row.get('project_id', '')}\t{row.get('status', '')}")
        return 0
    if not args.task_id:
        parser.error("provide --list or at least one exact --task-id")
    try:
        plan = make_plan(repository.load(), set(args.task_id))
        active = [row["id"] for row in plan["state"]["tasks"] if row["id"] in plan["task_ids"] and row.get("status") in RUNNING_STATUSES]
        if active and not args.force:
            raise CleanupError(f"refusing deletion; running task IDs: {', '.join(active)} (use --force only for test cleanup)")
        print_plan(plan, not args.confirm)
        if not args.confirm:
            return 0
        backup_dir = delete(repository, data_dir, plan)
        print(f"Deleted. Recovery backup: {backup_dir}")
        return 0
    except CleanupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
