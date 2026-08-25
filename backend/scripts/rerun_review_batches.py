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
    parser = argparse.ArgumentParser(description="重跑指定采购提取批次并刷新下游审查结果")
    parser.add_argument("--output-tokens", type=int, default=5_000)
    parser.add_argument("--no-stream", action="store_true")
    parser.add_argument("run_id")
    parser.add_argument("batches", nargs="+", type=int)
    args = parser.parse_args()

    backend_dir = BACKEND_DIR
    config_path = backend_dir / "review_config.json"
    config = load_settings(config_path)
    config["workflow"]["rerun_batches"] = args.batches
    config["workflow"]["rerun_output_tokens"] = args.output_tokens
    config["workflow"]["rerun_stream"] = not args.no_stream
    runs_root = Path(config["runtime"]["runs_root"]).resolve()
    run_dir = (runs_root / args.run_id).resolve()
    if run_dir.parent != runs_root or not (run_dir / "state.json").is_file():
        raise SystemExit(f"无效运行目录：{run_dir}")

    state = read_json(run_dir / "state.json")
    if state.get("status") != "completed":
        raise SystemExit(f"只允许重跑已完成任务，当前状态：{state.get('status')}")

    checkpoint_dir = run_dir / "batch_artifacts" / "extract_candidates"
    checkpoints = [checkpoint_dir / f"procurement_{batch:03d}.json" for batch in args.batches]
    missing = [str(path) for path in checkpoints if not path.is_file()]
    if missing:
        raise SystemExit("缺少批次检查点：" + ", ".join(missing))

    service = ProcurementReviewService()
    tasks = service.tasks.read()["items"]
    task = next((item for item in tasks if item.get("engine_run_id") == args.run_id), None)
    if not task:
        raise SystemExit("未找到绑定该运行的审查任务")
    task_id = task["id"]
    document_version = int(task.get("document", {}).get("version") or 1)
    old_findings = [
        item for item in service.findings.read()["items"]
        if item.get("task_id") == task_id
        and item.get("source_type") == "ai"
        and int(item.get("document_version") or 1) == document_version
    ]
    protected = [
        item["id"] for item in old_findings
        if any(item.get(key) for key in ("primary_decision", "operator_decision", "rectification_status", "comments"))
    ]
    if protected:
        raise SystemExit("已有人工处置的 AI 发现，拒绝自动替换：" + ", ".join(protected))

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = run_dir / "rerun_backups" / stamp
    backup_dir.mkdir(parents=True)
    shutil.copy2(run_dir / "state.json", backup_dir / "state.json")
    review_data = Path(get_settings().data_dir) / "review_data.json"
    shutil.copy2(review_data, backup_dir / "review_data.json")
    artifacts_backup = backup_dir / "artifacts"
    artifacts_backup.mkdir()
    extract_index = STEPS.index("extract_candidates")
    for index, step in enumerate(STEPS[extract_index:], start=extract_index + 1):
        artifact = run_dir / "artifacts" / f"{index:02d}_{step}.json"
        if artifact.is_file():
            shutil.copy2(artifact, artifacts_backup / artifact.name)
    checkpoints_backup = backup_dir / "checkpoints"
    checkpoints_backup.mkdir()
    for checkpoint in checkpoints:
        shutil.move(checkpoint, checkpoints_backup / checkpoint.name)

    state["completed_steps"] = STEPS[:extract_index]
    state["status"] = "paused"
    state["current_step"] = None
    state["pause_after"] = None
    state["error"] = None
    write_json(run_dir / "state.json", state)

    engine_dir = backend_dir / "app" / "review_engine"
    store = WorkflowEngine(runs_root, engine_dir / "skills.json", config).resume(run_dir)
    final_state = store.load_state()
    if final_state.get("status") != "completed":
        raise SystemExit(f"重跑未完成：{final_state.get('error')}")

    report = store.read_artifact(STEPS.index("final_report") + 1, "final_report")
    quality = store.read_artifact(STEPS.index("quality_check") + 1, "quality_check")["quality"].get("procurement", {})

    def remove_old_ai_findings(data: dict) -> None:
        data["findings"] = [
            item for item in data["findings"]
            if not (
                item.get("task_id") == task_id
                and item.get("source_type") == "ai"
                and int(item.get("document_version") or 1) == document_version
            )
        ]

    service.repository.transaction(remove_old_ai_findings)
    service._store_review_results(
        task["project_id"], task_id,
        {**report, "engine_run_id": args.run_id, "quality": quality},
    )
    print(
        f"completed run={args.run_id} batches={','.join(map(str, args.batches))} "
        f"old_findings={len(old_findings)} new_findings={len(report.get('findings', []))} backup={backup_dir}"
    )


if __name__ == "__main__":
    main()
