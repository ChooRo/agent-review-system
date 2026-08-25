from __future__ import annotations

import argparse
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings
from app.review_engine.procurement.agent_workflow import STEPS, WorkflowEngine
from app.review_engine.runner import read_json, write_json
from app.review_engine.settings import load_settings
from app.services.procurement.review import ProcurementReviewService


def main() -> None:
    parser = argparse.ArgumentParser(description="将已有有效候选的局部重提失败批次标记为完成")
    parser.add_argument("run_id")
    parser.add_argument("batch", type=int)
    args = parser.parse_args()

    config = load_settings(BACKEND_DIR / "review_config.json")
    runs_root = Path(config["runtime"]["runs_root"]).resolve()
    run_dir = (runs_root / args.run_id).resolve()
    if run_dir.parent != runs_root:
        raise SystemExit("无效运行目录")

    state_path = run_dir / "state.json"
    checkpoint_path = run_dir / "batch_artifacts" / "extract_candidates" / f"procurement_{args.batch:03d}.json"
    extraction_path = run_dir / "artifacts" / "06_extract_candidates.json"
    final_path = run_dir / "artifacts" / "16_final_report.json"
    state = read_json(state_path)
    checkpoint = read_json(checkpoint_path)
    failure = checkpoint.get("failure") or {}
    if state.get("status") != "completed" or failure.get("code") != "CANDIDATE_RETRY_UNAVAILABLE":
        raise SystemExit("当前批次不是可收敛的局部重提失败状态")
    if not checkpoint.get("accepted"):
        raise SystemExit("当前批次没有可用候选，不能收敛")

    service = ProcurementReviewService()
    task = next((item for item in service.tasks.read()["items"] if item.get("engine_run_id") == args.run_id), None)
    if not task:
        raise SystemExit("未找到绑定任务")
    task_id = task["id"]
    document_version = int(task.get("document", {}).get("version") or 1)
    old_findings = [
        item for item in service.findings.read()["items"]
        if item.get("task_id") == task_id and item.get("source_type") == "ai"
        and int(item.get("document_version") or 1) == document_version
    ]
    if any(any(item.get(key) for key in ("primary_decision", "operator_decision", "rectification_status", "comments")) for item in old_findings):
        raise SystemExit("已有人工处置结果，拒绝自动替换")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = run_dir / "rerun_backups" / f"finalize-{stamp}"
    backup_dir.mkdir(parents=True)
    for path in (state_path, checkpoint_path, extraction_path, final_path, Path(get_settings().data_dir) / "review_data.json"):
        shutil.copy2(path, backup_dir / path.name)

    checkpoint["failure"] = None
    write_json(checkpoint_path, checkpoint)
    extraction = read_json(extraction_path)
    report = next(item for item in extraction["batch_reports"]["procurement"] if item["batch_no"] == args.batch)
    report["status"] = "completed"
    report["failure"] = None
    extraction["extraction_findings"] = [
        item for item in extraction.get("extraction_findings", []) if item.get("source_batch") != args.batch
    ]
    extraction["status"] = "degraded" if extraction["extraction_findings"] else "completed"
    write_json(extraction_path, extraction)

    state["completed_steps"] = [step for step in state["completed_steps"] if step != "final_report"]
    state["status"] = "paused"
    state["current_step"] = None
    state["error"] = None
    write_json(state_path, state)
    engine_dir = BACKEND_DIR / "app" / "review_engine"
    store = WorkflowEngine(runs_root, engine_dir / "skills.json", config).resume(run_dir)
    if store.load_state().get("status") != "completed":
        raise SystemExit("最终报告刷新失败")
    final = store.read_artifact(STEPS.index("final_report") + 1, "final_report")
    quality = store.read_artifact(STEPS.index("quality_check") + 1, "quality_check")["quality"].get("procurement", {})

    def remove_old_ai_findings(data: dict) -> None:
        data["findings"] = [
            item for item in data["findings"]
            if not (
                item.get("task_id") == task_id and item.get("source_type") == "ai"
                and int(item.get("document_version") or 1) == document_version
            )
        ]

    service.repository.transaction(remove_old_ai_findings)
    service._store_review_results(
        task["project_id"], task_id,
        {**final, "engine_run_id": args.run_id, "quality": quality},
    )
    print(f"completed run={args.run_id} batch={args.batch} findings={len(final.get('findings', []))} backup={backup_dir}")


if __name__ == "__main__":
    main()
