"""Run the real procurement review workflow in a backend thread."""

from pathlib import Path
from typing import Callable

from app.core.config import get_settings
from app.review_engine.services.workflow import STEPS, WorkflowEngine
from app.review_engine.settings import load_settings


def run_review_workflow(
    project_id: str, task_id: str, doc_path: str,
    store_results: Callable[[str, str, dict], None], fail_task: Callable[[str, str, str], None],
    progress_task: Callable[[str, str, str, str | None, int, int], None], engine_run_id: str | None = None,
    task_context: dict | None = None,
) -> None:
    try:
        config_path = Path(__file__).resolve().parent.parent.parent / "review_config.json"
        config = load_settings(config_path if config_path.is_file() else None)
        config["mineru"]["api_url"] = get_settings().mineru_api_url
        llm = config.get("llm", {})
        if not (llm.get("api_url") and llm.get("api_key") and llm.get("model")):
            raise RuntimeError("LLM configuration requires api_url, api_key, and model")
        engine_dir = Path(__file__).resolve().parent.parent / "review_engine"
        runs_root = Path(config["runtime"]["runs_root"])

        def report_progress(store, state, completed_step) -> None:
            progress_task(project_id, task_id, state["run_id"], completed_step, len(state.get("completed_steps", [])), len(STEPS))

        engine = WorkflowEngine(runs_root, engine_dir / "skills.json", config, progress_callback=report_progress)
        run_dir = runs_root / engine_run_id if engine_run_id else None
        store = engine.resume(run_dir) if run_dir and (run_dir / "state.json").is_file() else engine.start("procurement", {"procurement": doc_path}, task_context=task_context)
        state = store.load_state()
        if state["status"] == "completed":
            report = store.read_artifact(STEPS.index("final_report") + 1, "final_report")
            store_results(project_id, task_id, {**report, "engine_run_id": state["run_id"], "quality": {"status": "passed"}})
        else:
            error_info = state.get("error", {})
            fail_task(project_id, task_id, f"{error_info.get('step', '')}: {error_info.get('message', 'unknown error')}"[:500])
    except Exception as exc:
        fail_task(project_id, task_id, str(exc)[:500])
