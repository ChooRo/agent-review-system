"""运行状态、步骤产物和可审计事件日志。"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable


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


def configure_logging(level: str = "INFO", log_path: Path | None = None) -> Path:
    """配置控制台和按大小轮转的应用日志。"""
    target = (log_path or Path(__file__).resolve().parents[3] / "logs" / "app.log").resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger()
    logger.setLevel(level.upper())
    if not any(isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename).resolve() == target for handler in logger.handlers):
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        file_handler = RotatingFileHandler(target, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    return target


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
        pause_after: str | None,
        task_context: dict[str, Any] | None = None,
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
                "task_context": task_context or {},
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


class CheckpointRunner:
    """通用检查点执行器；不包含任何采购或其他业务步骤。"""

    STEPS: tuple[str, ...] = ()

    def __init__(
        self,
        runs_root: Path,
        config: dict[str, Any] | None = None,
        progress_callback: Callable[[RunStore, dict[str, Any], str | None], None] | None = None,
    ) -> None:
        self.runs_root = runs_root.resolve()
        self.config = config or {}
        self.progress_callback = progress_callback

    def start(
        self,
        scenario: str,
        documents: dict[str, str],
        pause_after: str | None = None,
        task_context: dict[str, Any] | None = None,
    ) -> RunStore:
        self.validate_request(scenario, documents, pause_after)
        store = RunStore.create(self.runs_root, scenario, documents, pause_after, task_context)
        return self.run(store)

    def validate_request(
        self, scenario: str, documents: dict[str, str], pause_after: str | None
    ) -> None:
        """业务工作流可覆盖的输入校验钩子。"""

    def resume(self, run_dir: Path, pause_after: str | None = None) -> RunStore:
        store = RunStore(run_dir)
        state = store.load_state()
        if state["status"] == "completed":
            return store
        if pause_after is not None:
            if pause_after not in self.STEPS:
                raise ValueError(f"未知断点：{pause_after}")
            state["pause_after"] = pause_after
            store.save_state(state)
        store.event("INFO", "run", "resumed", "从检查点恢复运行")
        return self.run(store)

    def runtime_services(self, store: RunStore) -> tuple[Any, Any]:
        """返回步骤需要的外部服务；业务工作流负责决定具体实现。"""
        return None, None

    def run(self, store: RunStore) -> RunStore:
        state = store.load_state()
        llm, mineru = self.runtime_services(store)
        handlers = {name: getattr(self, f"_{name}") for name in self.STEPS}
        state["status"] = "running"
        state["error"] = None
        store.save_state(state)
        if self.progress_callback:
            self.progress_callback(store, state, None)
        for index, step in enumerate(self.STEPS, start=1):
            state = store.load_state()
            if step in state["completed_steps"]:
                continue
            state["current_step"] = step
            store.save_state(state)
            started = time.perf_counter()
            store.event("INFO", step, "started", "步骤开始")
            try:
                output = handlers[step](store, state, llm, mineru)
                artifact = store.write_artifact(index, step, output)
                state = store.load_state()
                state["completed_steps"].append(step)
                state["current_step"] = None
                state["error"] = None
                duration = round(time.perf_counter() - started, 3)
                store.save_state(state)
                if self.progress_callback:
                    self.progress_callback(store, state, step)
                store.event(
                    "INFO", step, "completed", "步骤完成",
                    duration_seconds=duration, artifact=str(artifact), summary=summarize(output),
                )
                if state.get("pause_after") == step:
                    state["status"] = "paused"
                    store.save_state(state)
                    store.event("INFO", step, "paused", "命中断点，等待恢复")
                    return store
            except Exception as exc:
                state = store.load_state()
                state["status"] = "failed"
                state["error"] = {"step": step, "type": type(exc).__name__, "message": str(exc)}
                store.save_state(state)
                store.event("ERROR", step, "failed", str(exc), error_type=type(exc).__name__)
                logging.getLogger("review_mvp").exception("步骤失败：%s", step)
                return store
        state = store.load_state()
        state["status"] = "completed"
        state["current_step"] = None
        store.save_state(state)
        store.event("INFO", "run", "completed", "全部步骤完成")
        return store

    def _previous(self, store: RunStore, step: str) -> Any:
        return store.read_artifact(self.STEPS.index(step) + 1, step)


Runner = CheckpointRunner


def summarize(value: Any) -> dict[str, Any]:
    """生成日志摘要，避免把步骤全文写入运行事件。"""
    if not isinstance(value, dict):
        return {"type": type(value).__name__}
    summary: dict[str, Any] = {"keys": list(value)[:12]}
    for key in ("documents", "profiles", "candidates", "ledgers", "quality"):
        if isinstance(value.get(key), dict):
            summary[f"{key}_counts"] = {
                name: len(item) if isinstance(item, (list, dict)) else 1
                for name, item in value[key].items()
            }
    for key in ("finding_count", "verified_count", "insufficient_count"):
        if key in value:
            summary[key] = value[key]
    return summary
