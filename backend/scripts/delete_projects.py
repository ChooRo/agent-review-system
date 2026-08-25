"""安全地从 JSON 开发存储中删除明确选定的测试项目。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
from app.repositories.postgres.review_repository import PostgresReviewRepository


RUNNING_STATUSES = {"queued", "parsing", "reviewing"}


class CleanupError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeletionPlan:
    project_ids: set[str]
    task_ids: set[str]
    finding_ids: set[str]
    comment_ids: set[str]
    event_ids: set[str]
    upload_dirs: tuple[Path, ...]
    state: dict
    cleaned: dict

    @property
    def counts(self) -> dict[str, int]:
        return {
            "projects": len(self.project_ids), "tasks": len(self.task_ids), "findings": len(self.finding_ids),
            "comments": len(self.comment_ids), "events": len(self.event_ids),
            "audit": len(self.state["audit"]) - len(self.cleaned["audit"]),
            "idempotency": len(self.state["idempotency"]) - len(self.cleaned["idempotency"]),
            "upload_directories": len(self.upload_dirs),
        }


def contains_id(value: object, identifiers: set[str]) -> bool:
    if isinstance(value, str):
        return value in identifiers
    if isinstance(value, dict):
        return any(contains_id(item, identifiers) for item in value.values())
    if isinstance(value, list):
        return any(contains_id(item, identifiers) for item in value)
    return False


def make_plan(state: dict, project_ids: set[str], uploads_dir: Path) -> DeletionPlan:
    available = {project["id"] for project in state["projects"]}
    missing = project_ids - available
    if missing:
        raise CleanupError(f"project_id does not exist: {', '.join(sorted(missing))}")
    tasks = [row for row in state["tasks"] if row.get("project_id") in project_ids]
    task_ids = {row["id"] for row in tasks}
    findings = [row for row in state["findings"] if row.get("task_id") in task_ids]
    finding_ids = {row["id"] for row in findings}
    related_ids = project_ids | task_ids | finding_ids
    comments = [row for row in state["comments"] if contains_id(row, related_ids)]
    comment_ids = {row["id"] for row in comments if row.get("id")}
    events = [row for row in state["events"] if row.get("task_id") in task_ids]
    event_ids = {row["id"] for row in events}
    all_ids = related_ids | comment_ids | event_ids
    cleaned = {
        "projects": [row for row in state["projects"] if row.get("id") not in project_ids],
        "tasks": [row for row in state["tasks"] if row.get("id") not in task_ids],
        "findings": [row for row in state["findings"] if row.get("id") not in finding_ids],
        "comments": [row for row in state["comments"] if not contains_id(row, related_ids)],
        "events": [row for row in state["events"] if row.get("id") not in event_ids],
        "audit": [row for row in state["audit"] if not (contains_id(row.get("target_id"), all_ids) or contains_id(row.get("rule_id"), all_ids) or contains_id(row.get("details"), all_ids))],
        "idempotency": [row for row in state["idempotency"] if not contains_id(row.get("response"), all_ids)],
    }
    upload_dirs = tuple(path for path in (uploads_dir / project_id for project_id in project_ids) if path.exists())
    return DeletionPlan(project_ids, task_ids, finding_ids, comment_ids, event_ids, upload_dirs, deepcopy(state), cleaned)


def validate_deleted(state: dict, plan: DeletionPlan) -> None:
    all_ids = plan.project_ids | plan.task_ids | plan.finding_ids | plan.comment_ids | plan.event_ids
    if any(contains_id(row, all_ids) for rows in state.values() for row in rows):
        raise CleanupError("cleanup validation found a remaining target reference")
    task_ids = {row["id"] for row in state["tasks"]}
    if any(row.get("task_id") not in task_ids for name in ("findings", "events") for row in state[name]):
        raise CleanupError("cleanup validation found an orphan task reference")


def print_plan(plan: DeletionPlan, dry_run: bool) -> None:
    print("DRY-RUN" if dry_run else "DELETE")
    print("project_ids:", ", ".join(sorted(plan.project_ids)))
    for name, count in plan.counts.items():
        print(f"{name}: {count}")


def running_tasks(plan: DeletionPlan) -> list[str]:
    return [row["id"] for row in plan.state["tasks"] if row.get("id") in plan.task_ids and row.get("status") in RUNNING_STATUSES]


def backup_and_delete(repository: PostgresReviewRepository, data_dir: Path, plan: DeletionPlan) -> Path:
    backup_dir = data_dir / "backups" / f"delete_projects_{datetime.now(UTC):%Y%m%dT%H%M%SZ}_{uuid4().hex[:8]}"
    moved: list[tuple[Path, Path]] = []
    try:
        backup_dir.mkdir(parents=True)
        (backup_dir / "review_state.json").write_text(json.dumps(plan.state, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            for source in plan.upload_dirs:
                destination = backup_dir / "uploads" / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                moved.append((source, destination))
            repository.commit(plan.cleaned)
            validate_deleted(repository.load(), plan)
        except Exception as exc:
            try:
                repository.commit(plan.state)
            finally:
                for source, destination in reversed(moved):
                    if destination.exists():
                        shutil.move(str(destination), str(source))
            raise CleanupError(f"delete failed; restored state/uploads from {backup_dir}: {exc}") from exc
    except OSError as exc:
        raise CleanupError(f"backup failed; state was not modified: {exc}") from exc
    return backup_dir


def configured_paths() -> tuple[Path, Path]:
    os.chdir(BACKEND_ROOT)
    get_settings.cache_clear()
    settings = get_settings()
    data_dir = Path(settings.data_dir)
    uploads_dir = Path(settings.uploads_dir)
    return (data_dir if data_dir.is_absolute() else BACKEND_ROOT / data_dir, uploads_dir if uploads_dir.is_absolute() else BACKEND_ROOT / uploads_dir)


def list_projects(repository: PostgresReviewRepository) -> None:
    state = repository.load()
    task_counts = {project["id"]: 0 for project in state["projects"]}
    for task in state["tasks"]:
        if task.get("project_id") in task_counts:
            task_counts[task["project_id"]] += 1
    for project in state["projects"]:
        print(f"{project['id']}\t{project.get('project_code', '')}\t{project.get('name', '')}\t{project.get('status', '')}\t{task_counts[project['id']]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely delete explicitly selected test projects.")
    parser.add_argument("--list", action="store_true", help="List projects without changing data.")
    parser.add_argument("--project-id", action="append", default=[], help="Exact project ID; may be repeated.")
    parser.add_argument("--confirm", action="store_true", help="Actually delete after backup.")
    parser.add_argument("--force", action="store_true", help="Allow deletion of queued/parsing/reviewing test tasks.")
    args = parser.parse_args(argv)
    if args.list:
        if args.project_id:
            parser.error("--list cannot be combined with --project-id")
        data_dir, _ = configured_paths(); list_projects(PostgresReviewRepository(data_dir)); return 0
    if not args.project_id:
        parser.error("provide --list or at least one exact --project-id")
    data_dir, uploads_dir = configured_paths()
    repository = PostgresReviewRepository(data_dir)
    try:
        plan = make_plan(repository.load(), set(args.project_id), uploads_dir)
        active = running_tasks(plan)
        if active and not args.force:
            raise CleanupError(f"refusing whole batch; running task IDs: {', '.join(active)} (use --force only for test cleanup)")
        if args.force and active:
            print("WARNING: --force deletes running test tasks; do not use for production data.", file=sys.stderr)
        print_plan(plan, dry_run=not args.confirm)
        if not args.confirm:
            return 0
        validate_deleted(plan.cleaned, plan)
        backup_dir = backup_and_delete(repository, data_dir, plan)
        print(f"Deleted. Recovery backup: {backup_dir}")
        return 0
    except CleanupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
