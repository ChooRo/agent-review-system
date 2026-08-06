"""运行状态、步骤产物和可审计事件日志。"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    """返回UTC ISO时间，供状态和事件统一使用。"""
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: Any) -> None:
    """原子写入JSON，避免进程中断留下半个状态文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def read_json(path: Path) -> Any:
    """读取UTF-8 JSON文件。"""
    return json.loads(path.read_text(encoding="utf-8"))


class RunStore:
    """把一次审查运行的状态、事件和步骤产物持久化到独立目录。"""

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir.resolve()
        self.state_path = self.run_dir / "state.json"
        self.events_path = self.run_dir / "events.jsonl"
        self.artifacts_dir = self.run_dir / "artifacts"

    @classmethod
    def create(
        cls,
        runs_root: Path,
        scenario: str,
        documents: dict[str, str],
        mode: str,
        pause_after: str | None,
    ) -> "RunStore":
        """创建新运行目录并保存初始状态。"""
        run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
        store = cls(runs_root / run_id)
        store.artifacts_dir.mkdir(parents=True, exist_ok=False)
        store.save_state(
            {
                "run_id": run_id,
                "scenario": scenario,
                "documents": documents,
                "mode": mode,
                "pause_after": pause_after,
                "status": "created",
                "current_step": None,
                "completed_steps": [],
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "error": None,
            }
        )
        store.event("INFO", "run", "created", f"创建{scenario}运行")
        return store

    def load_state(self) -> dict[str, Any]:
        """读取当前运行状态。"""
        if not self.state_path.is_file():
            raise FileNotFoundError(f"运行状态不存在：{self.state_path}")
        return read_json(self.state_path)

    def save_state(self, state: dict[str, Any]) -> None:
        """更新运行状态并刷新更新时间。"""
        state["updated_at"] = now_iso()
        write_json(self.state_path, state)

    def artifact_path(self, index: int, step: str) -> Path:
        """返回带顺序号的步骤产物路径。"""
        return self.artifacts_dir / f"{index:02d}_{step}.json"

    def write_artifact(self, index: int, step: str, value: Any) -> Path:
        """保存步骤完整输出，供页面查看或断点恢复。"""
        path = self.artifact_path(index, step)
        write_json(path, value)
        return path

    def read_artifact(self, index: int, step: str) -> Any:
        """读取指定步骤产物。"""
        return read_json(self.artifact_path(index, step))

    def event(
        self,
        level: str,
        step: str,
        event: str,
        message: str,
        **details: Any,
    ) -> None:
        """追加一条不含密钥的JSONL事件，并同步输出控制台日志。"""
        record = {
            "timestamp": now_iso(),
            "level": level,
            "step": step,
            "event": event,
            "message": message,
            **details,
        }
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        getattr(logging.getLogger("review_mvp"), level.lower(), logging.info)(
            "[%s] %s", step, message
        )

    def events(self) -> list[dict[str, Any]]:
        """读取运行的全部事件，供CLI和API监控。"""
        if not self.events_path.exists():
            return []
        return [json.loads(line) for line in self.events_path.read_text(encoding="utf-8").splitlines() if line]
