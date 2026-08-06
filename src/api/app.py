"""用于启动、暂停、恢复和监控审查运行的FastAPI接口。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from services.runtime import RunStore, read_json
from services.workflow import STEPS, WorkflowEngine, validate_request
from settings import load_settings


SRC_ROOT = Path(__file__).resolve().parents[1]
SKILLS_PATH = SRC_ROOT / "skills.json"


class RunRequest(BaseModel):
    """创建审查运行所需字段。"""

    scenario: Literal["procurement", "response", "contract"]
    documents: dict[str, str]
    mode: Literal["mock", "live"] = "mock"
    pause_after: str | None = Field(default=None, description=f"可选步骤：{', '.join(STEPS)}")


class ResumeRequest(BaseModel):
    """恢复运行时可替换下一断点。"""

    pause_after: str | None = None


def create_app() -> FastAPI:
    """创建FastAPI应用，并从环境变量指定的配置文件读取模型参数。"""
    config_path = Path(os.getenv("REVIEW_MVP_CONFIG", SRC_ROOT / "config.json"))
    settings = load_settings(config_path)
    runs_root = Path(settings["runtime"]["runs_root"])
    engine = WorkflowEngine(runs_root, SKILLS_PATH, settings)
    app = FastAPI(title="厦门烟草招采智审MVP", version="0.1.0")

    @app.get("/")
    def root() -> dict[str, str]:
        return {"name": "厦门烟草招采智审MVP", "monitor": "/docs", "runs": "/runs"}

    @app.get("/steps")
    def steps() -> dict[str, list[str]]:
        return {"steps": STEPS}

    @app.get("/runs")
    def list_runs() -> list[dict]:
        runs_root.mkdir(parents=True, exist_ok=True)
        states = []
        for path in sorted(runs_root.glob("*/state.json"), reverse=True):
            try:
                states.append(read_json(path))
            except Exception:
                states.append({"run_id": path.parent.name, "status": "state_corrupted"})
        return states

    @app.post("/runs", status_code=202)
    def create_run(request: RunRequest, background_tasks: BackgroundTasks) -> dict:
        try:
            validate_request(request.scenario, request.documents, request.mode, request.pause_after)
            store = RunStore.create(
                runs_root, request.scenario, request.documents, request.mode, request.pause_after
            )
            background_tasks.add_task(engine.run, store)
            return store.load_state()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        return get_store(run_id).load_state()

    @app.get("/runs/{run_id}/events")
    def get_events(run_id: str) -> list[dict]:
        return get_store(run_id).events()

    @app.get("/runs/{run_id}/artifacts")
    def list_artifacts(run_id: str) -> list[str]:
        store = get_store(run_id)
        return [path.name for path in sorted(store.artifacts_dir.glob("*.json"))]

    @app.get("/runs/{run_id}/artifacts/{artifact_name}")
    def get_artifact(run_id: str, artifact_name: str):
        store = get_store(run_id)
        path = (store.artifacts_dir / artifact_name).resolve()
        try:
            path.relative_to(store.artifacts_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="非法产物路径") from exc
        if not path.is_file() or path.suffix != ".json":
            raise HTTPException(status_code=404, detail="产物不存在")
        return read_json(path)

    @app.post("/runs/{run_id}/resume", status_code=202)
    def resume_run(run_id: str, request: ResumeRequest, background_tasks: BackgroundTasks) -> dict:
        store = get_store(run_id)
        state = store.load_state()
        if state["status"] == "running":
            raise HTTPException(status_code=409, detail="运行正在执行")
        background_tasks.add_task(engine.resume, store.run_dir, request.pause_after)
        return state

    def get_store(run_id: str) -> RunStore:
        """验证运行ID不能越出runs目录。"""
        run_dir = (runs_root / run_id).resolve()
        try:
            run_dir.relative_to(runs_root.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="非法运行ID") from exc
        store = RunStore(run_dir)
        if not store.state_path.is_file():
            raise HTTPException(status_code=404, detail="运行不存在")
        return store

    return app


app = create_app()
