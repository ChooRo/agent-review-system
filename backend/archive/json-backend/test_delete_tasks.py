from __future__ import annotations

import json
from pathlib import Path

from app.repositories.review_repository import ReviewRepository
from scripts import delete_tasks


def test_task_delete_dry_run_and_cascade(tmp_path: Path, monkeypatch, capsys) -> None:
    data_dir = tmp_path / "data"
    ReviewRepository(data_dir).commit({
        "projects": [{"id": "p1"}, {"id": "p2"}],
        "tasks": [{"id": "t1", "project_id": "p1", "status": "draft"}, {"id": "t2", "project_id": "p2"}],
        "findings": [{"id": "f1", "task_id": "t1"}],
        "comments": [{"id": "c1", "finding_id": "f1"}],
        "events": [{"id": "e1", "task_id": "t1"}],
        "audit": [{"id": "a1", "target_id": "t1"}],
        "idempotency": [{"key": "i1", "response": {"task_id": "t1"}}],
    })
    monkeypatch.setattr(delete_tasks, "configured_data_dir", lambda: data_dir)
    assert delete_tasks.main(["--task-id", "t1"]) == 0
    assert delete_tasks.main(["--task-id", "t1", "--confirm"]) == 0
    state = json.loads((data_dir / "review_data.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in state["projects"]] == ["p1", "p2"]
    assert [row["id"] for row in state["tasks"]] == ["t2"]
    assert all(not any(value == "t1" or value == "f1" for value in row.values() if isinstance(value, str)) for name in ("findings", "comments", "events", "audit") for row in state[name])
