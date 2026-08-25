from types import SimpleNamespace

from app.core.config import get_settings
from app.services.procurement import procurement_workflow
from app.services.procurement import review as review_service
from app.services.procurement.review import ProcurementReviewService


def empty_state(task: dict) -> dict:
    return {
        "projects": [{"id": "project-1", "name": "project", "project_code": "P-1", "handling_department": "procurement",
                      "project_owner": "经办", "status": "draft", "created_by": 1,
                      "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
                      "version": 1}],
        "tasks": [task],
        "findings": [],
        "comments": [],
        "events": [],
        "audit": [],
        "idempotency": [],
    }


def test_start_clears_error_and_stage_progress_keeps_run_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    service = ProcurementReviewService()
    task = {
        "id": "task-1",
        "project_id": "project-1",
        "title": "review",
        "status": "failed",
        "operator_id": 1,
        "members": [],
        "document": {"id": "doc-1", "file_name": "document.pdf", "content_type": "application/pdf", "size": 10,
                     "sha256": "doc1", "path": str(tmp_path / "document.pdf"), "version": 1, "uploaded_by": 1,
                     "uploaded_at": "2026-01-01T00:00:00+00:00"},
        "engine_run_id": "existing-run",
        "error": "old error",
        "progress": 35,
        "progress_step": "build_logical_units",
        "batch_completed": 2,
        "batch_total": 10,
        "created_at": "now",
        "updated_at": "now",
        "version": 1,
    }
    service.repository.commit(empty_state(task))

    captured = {}

    monkeypatch.setattr(review_service, "enqueue_review", lambda *args: captured.setdefault("args", args))
    service.start("project-1", "task-1", {"id": 1, "role_codes": ["operator"], "department": "business"}, None)
    started = service.repository.load()["tasks"][0]
    assert "error" not in started
    assert "progress_step" not in started
    assert "batch_completed" not in started
    assert "batch_total" not in started
    assert captured["args"][:2] == ("project-1", "task-1")
    assert captured["args"][2].startswith("run_")

    service._update_review_progress("project-1", "task-1", "existing-run", "parse_documents", 1, 13)
    service._fail_review_task("project-1", "task-1", "later error")
    state = service.repository.load()
    progressed = state["tasks"][0]
    assert progressed["engine_run_id"] == "existing-run"
    assert progressed["progress"] == 11
    assert progressed["progress_step"] == "parse_documents"
    assert progressed["error"] == "later error"
    assert state["events"][-1]["actor_id"] == 0
    assert "parse_documents" in state["events"][-1]["reason"]
    get_settings.cache_clear()

def test_batch_progress_advances_inside_extract_stage(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    service = ProcurementReviewService()
    task = {
        "id": "task-1", "project_id": "project-1", "title": "review", "status": "reviewing",
        "operator_id": 1, "members": [], "document": None, "progress": 39,
        "created_at": "now", "updated_at": "now", "version": 1,
    }
    service.repository.commit(empty_state(task))

    service._update_review_progress("project-1", "task-1", "run-1", "extract_candidates", 5, 13, 1, 10)
    first = service.repository.load()["tasks"][0]
    service._update_review_progress("project-1", "task-1", "run-1", "extract_candidates", 5, 13, 2, 10)
    second_state = service.repository.load()
    second = second_state["tasks"][0]

    assert second["progress"] > first["progress"]
    assert second["batch_completed"] == 2
    assert second["batch_total"] == 10
    assert second_state["events"] == []
    get_settings.cache_clear()


def test_stalled_review_is_failed_when_read(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REVIEW_TASK_TIMEOUT_SECONDS", "60")
    get_settings.cache_clear()
    service = ProcurementReviewService()
    task = {"id": "task-1", "project_id": "project-1", "title": "review", "status": "reviewing", "operator_id": 1, "members": [{"user_id": 1, "task_role": "operator", "department": "采购业务部", "module_scope": ["procurement"]}], "document": None, "engine_run_id": "run-1", "progress": 5, "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00", "version": 1}
    service.repository.commit(empty_state(task))

    result = service.get_task("project-1", "task-1", {"id": 1, "role_codes": ["operator"], "department": "business"})

    assert result["status"] == "failed"
    assert "心跳超时" in result["error"]
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
    monkeypatch.setattr(procurement_workflow, "get_settings", lambda: SimpleNamespace(mineru_api_url="http://mineru"))
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
    assert progress == [("project-1", "task-1", run_id, None, 1, len(procurement_workflow.STEPS) - 2)]
    assert failures[0][-1] == "quality_check: boom"


def test_workflow_fails_clearly_without_llm_configuration(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(procurement_workflow, "load_settings", lambda _path: {"mineru": {}, "llm": {}, "runtime": {"runs_root": str(tmp_path)}})
    monkeypatch.setattr(procurement_workflow, "get_settings", lambda: SimpleNamespace(mineru_api_url="http://mineru"))
    failures = []
    procurement_workflow.run_review_workflow("project-1", "task-1", "document.pdf", lambda *_args: None, lambda *args: failures.append(args), lambda *_args: None)
    assert "LLM configuration requires api_url, api_key, and model" in failures[0][-1]
