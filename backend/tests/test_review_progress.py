from types import SimpleNamespace

from app.core.config import get_settings
from app.services import procurement_workflow
from app.services.procurement_review import ProcurementReviewService


def empty_state(task: dict) -> dict:
    return {
        "projects": [],
        "tasks": [task],
        "findings": [],
        "comments": [],
        "events": [],
        "audit": [],
        "idempotency": [],
    }


def test_start_clears_error_and_stage_progress_keeps_run_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REVIEW_EXECUTION_MODE", "live")
    get_settings.cache_clear()
    service = ProcurementReviewService()
    task = {
        "id": "task-1",
        "project_id": "project-1",
        "title": "review",
        "status": "failed",
        "operator_id": 1,
        "members": [],
        "document": {"path": str(tmp_path / "document.pdf")},
        "engine_run_id": "existing-run",
        "error": "old error",
        "progress": 35,
        "created_at": "now",
        "updated_at": "now",
        "version": 1,
    }
    service.repository.commit(empty_state(task))

    captured = {}

    class Thread:
        def __init__(self, target, args, daemon):
            captured["args"] = args

        def start(self):
            pass

    monkeypatch.setattr("app.services.procurement_review.threading.Thread", Thread)
    service.start("project-1", "task-1", {"id": 1, "role_codes": ["operator"], "department": "business"}, None)
    started = service.repository.load()["tasks"][0]
    assert started["execution_mode"] == "live"
    assert "error" not in started
    assert captured["args"][-1] == "existing-run"

    service._update_review_progress("project-1", "task-1", "existing-run", "parse_documents", 1, 11)
    service._fail_review_task("project-1", "task-1", "later error")
    state = service.repository.load()
    progressed = state["tasks"][0]
    assert progressed["engine_run_id"] == "existing-run"
    assert progressed["progress"] == 13
    assert progressed["error"] == "later error"
    assert state["events"][-1]["actor_id"] == 0
    assert "parse_documents" in state["events"][-1]["reason"]
    get_settings.cache_clear()


def test_failed_live_workflow_resumes_existing_run(tmp_path, monkeypatch) -> None:
    run_id = "existing-run"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    (run_dir / "state.json").write_text("{}", encoding="utf-8")
    state = {
        "run_id": run_id,
        "status": "failed",
        "completed_steps": ["parse_documents"],
        "error": {"step": "quality_check", "message": "boom"},
    }

    class Store:
        def load_state(self):
            return state

    class Engine:
        def __init__(self, runs_root, skills_path, config, progress_callback):
            self.progress_callback = progress_callback

        def start(self, *args, **kwargs):
            raise AssertionError("existing run must not be replaced")

        def resume(self, path):
            assert path == run_dir
            self.progress_callback(Store(), state, None)
            return Store()

    monkeypatch.setattr(procurement_workflow, "WorkflowEngine", Engine)
    monkeypatch.setattr(procurement_workflow, "load_settings", lambda path: {"mineru": {}, "llm": {"api_url": "url", "api_key": "key", "model": "model"}, "runtime": {"runs_root": str(tmp_path)}})
    monkeypatch.setattr(procurement_workflow, "get_settings", lambda: SimpleNamespace(mineru_api_url="http://mineru", review_execution_mode="live"))
    progress, failures = [], []
    procurement_workflow.run_review_workflow(
        "project-1",
        "task-1",
        "document.pdf",
        lambda *args: None,
        lambda *args: failures.append(args),
        lambda *args: progress.append(args),
        run_id,
    )
    assert progress == [("project-1", "task-1", run_id, None, 1, 11)]
    assert failures[0][-1] == "quality_check: boom"
