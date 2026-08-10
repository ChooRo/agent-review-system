"""审查工作流后台执行：启动 WorkflowEngine、存储结果、更新任务状态。"""

from pathlib import Path
from typing import Callable

from app.review_engine.services.workflow import STEPS, WorkflowEngine
from app.review_engine.settings import load_settings
from app.review_engine.mock_runner import run_procurement_mock
from app.core.config import get_settings


def run_review_workflow(
    project_id: str,
    task_id: str,
    doc_path: str,
    store_results: Callable[[str, str, dict], None],
    fail_task: Callable[[str, str, str], None],
    progress_task: Callable[[str, str, str, str | None, int, int], None],
    engine_run_id: str | None = None,
) -> None:
    """在后台线程中执行审查流水线，完成后通过回调回写 findings 和任务状态。"""
    try:
        config_path = Path(__file__).resolve().parent.parent.parent / "review_config.json"
        config = load_settings(config_path if config_path.is_file() else None)
        app_settings = get_settings()
        config["mineru"]["api_url"] = app_settings.mineru_api_url
        engine_dir = Path(__file__).resolve().parent.parent / "review_engine"
        skills_path = engine_dir / "skills.json"
        runs_root = Path(config["runtime"]["runs_root"])

        mode = app_settings.review_execution_mode
        if mode == "mock":
            store_results(project_id, task_id, {"execution_mode": "mock", "quality": {"status": "degraded", "reason": "开发 mock 未执行 MinerU/LLM"}, "findings": run_procurement_mock(doc_path)})
            return
        llm_cfg = config.get("llm", {})
        if not (llm_cfg.get("api_url") and llm_cfg.get("api_key") and llm_cfg.get("model")):
            raise RuntimeError("live 执行模式需要完整 LLM 配置")

        def report_progress(store, state, completed_step) -> None:
            completed = state.get("completed_steps", [])
            progress_task(
                project_id,
                task_id,
                state["run_id"],
                completed_step,
                len(completed),
                len(STEPS),
            )

        engine = WorkflowEngine(runs_root, skills_path, config, progress_callback=report_progress)
        run_dir = runs_root / engine_run_id if engine_run_id else None
        store = engine.resume(run_dir) if run_dir and (run_dir / "state.json").is_file() else engine.start("procurement", {"procurement": doc_path}, mode=mode)
        state = store.load_state()

        if state["status"] == "completed":
            report = store.read_artifact(11, "final_report")
            store_results(project_id, task_id, {**report, "engine_run_id": state["run_id"], "execution_mode": "live", "quality": {"status": "passed"}})
        else:
            error_info = state.get("error", {})
            msg = f"{error_info.get('step', '')}: {error_info.get('message', '未知错误')}"[:500]
            fail_task(project_id, task_id, msg)
    except Exception as exc:
        fail_task(project_id, task_id, str(exc)[:500])
