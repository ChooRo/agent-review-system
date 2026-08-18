"""Run the real procurement review workflow in a backend thread."""

from pathlib import Path
from typing import Callable

from app.core.config import get_settings
from app.review_engine.services.procurement.workflow import STEPS, WorkflowEngine
from app.review_engine.services.runtime import read_json, write_json
from app.review_engine.settings import load_settings


def run_review_workflow(
    project_id: str, task_id: str, doc_path: str,
    store_results: Callable[[str, str, dict], None], fail_task: Callable[[str, str, str], None],
    progress_task: Callable[[str, str, str, str | None, int, int], None], engine_run_id: str | None = None,
    task_context: dict | None = None, pause_task: Callable[[str, str, dict], None] | None = None,
    legal_confirmations: dict[str, dict] | None = None,
) -> None:
    try:
        config_path = Path(__file__).resolve().parents[3] / "review_config.json"
        config = load_settings(config_path if config_path.is_file() else None)
        config["mineru"]["api_url"] = get_settings().mineru_api_url
        llm = config.get("llm", {})
        if not (llm.get("api_url") and llm.get("api_key") and llm.get("model")):
            raise RuntimeError("LLM configuration requires api_url, api_key, and model")
        engine_dir = Path(__file__).resolve().parents[2] / "review_engine"
        runs_root = Path(config["runtime"]["runs_root"])

        applicability_enabled = bool(config.get("legal_applicability", {}).get("enabled", False))
        disabled_steps = set() if applicability_enabled else {"derive_legal_facts", "match_legal_applicability"}

        def report_progress(store, state, completed_step) -> None:
            completed = [step for step in state.get("completed_steps", []) if step not in disabled_steps]
            visible_step = completed_step if completed_step not in disabled_steps else (completed[-1] if completed else None)
            step_progress = state.get("step_progress") or {}
            if step_progress and step_progress.get("step") == visible_step:
                progress_task(
                    project_id, task_id, state["run_id"], visible_step,
                    len(completed), len(STEPS) - len(disabled_steps),
                    int(step_progress.get("completed") or 0), int(step_progress.get("total") or 0),
                )
            else:
                progress_task(project_id, task_id, state["run_id"], visible_step, len(completed), len(STEPS) - len(disabled_steps))

        engine = WorkflowEngine(runs_root, engine_dir / "skills.json", config, progress_callback=report_progress)
        run_dir = runs_root / engine_run_id if engine_run_id else None
        if applicability_enabled and run_dir and legal_confirmations:
            gate_path = run_dir / "artifacts" / f"{STEPS.index('match_legal_applicability') + 1:02d}_match_legal_applicability.json"
            gate = read_json(gate_path)
            accepted_keys = {key for key, value in legal_confirmations.items() if value.get("decision") == "confirmed"}
            gate["applicable_legal_units"] = [unit for unit in gate.get("candidate_legal_units", []) if unit.get("document_key") in accepted_keys]
            gate["frozen_context"] = [item["source_freeze"] for item in gate.get("decisions", []) if item.get("document_key") in accepted_keys]
            for item in gate.get("decisions", []): item["human_confirmation"] = legal_confirmations.get(item.get("document_key"))
            write_json(gate_path, gate)
            state = read_json(run_dir / "state.json"); state["pause_after"] = None; write_json(run_dir / "state.json", state)
        store = engine.resume(run_dir) if run_dir and (run_dir / "state.json").is_file() else engine.start(
            "procurement", {"procurement": doc_path},
            pause_after="match_legal_applicability" if applicability_enabled else None,
            task_context=task_context,
        )
        state = store.load_state()
        if applicability_enabled and state["status"] == "paused":
            gate = store.read_artifact(STEPS.index("match_legal_applicability") + 1, "match_legal_applicability")
            required = [item for item in gate.get("decisions", []) if item.get("status") in {"applicable", "potential", "insufficient_facts"}]
            if required:
                if pause_task: pause_task(project_id, task_id, {**gate, "engine_run_id": state["run_id"]})
                return
            store = engine.resume(store.run_dir)
            state = store.load_state()
        if state["status"] == "completed":
            report = store.read_artifact(STEPS.index("final_report") + 1, "final_report")
            quality = store.read_artifact(STEPS.index("quality_check") + 1, "quality_check")["quality"].get("procurement", {})
            store_results(project_id, task_id, {**report, "engine_run_id": state["run_id"], "quality": quality})
        else:
            error_info = state.get("error", {})
            fail_task(project_id, task_id, f"{error_info.get('step', '')}: {error_info.get('message', 'unknown error')}"[:500])
    except Exception as exc:
        fail_task(project_id, task_id, str(exc)[:500])
