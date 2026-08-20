"""采购审查业务数据的仓储契约和 JSON 实现。"""

from pathlib import Path
import threading
from typing import Any

from app.repositories.json_store import JsonStore


class ReviewRepository:
    """唯一的业务数据边界；服务不会直接打开 JSON 文件。"""

    collections = ("projects", "tasks", "findings", "comments", "events", "audit", "idempotency")
    _lock = threading.RLock()

    def __init__(self, root: Path) -> None:
        self._store = JsonStore(root / "review_data.json")

    def load(self) -> dict[str, list[dict[str, Any]]]:
        value = self._store.read()
        return {name: list(value.get(name, [])) for name in self.collections}

    def commit(self, value: dict[str, list[dict[str, Any]]]) -> None:
        self._store.write({"schema_version": 1, **{name: value.get(name, []) for name in self.collections}})

    def transaction(self, mutate) -> None:
        """将相关任务、问题和审计变更作为一次原子 JSON 替换提交。"""
        with self._lock:
            state = self.load()
            mutate(state)
            self.commit(state)

    def collection(self, name: str) -> "ReviewCollection":
        if name not in self.collections:
            raise ValueError(f"未知业务集合：{name}")
        return ReviewCollection(self, name)


class ReviewCollection:
    """面向一个实际业务聚合的兼容型仓储契约。"""

    def __init__(self, repository: ReviewRepository, name: str) -> None:
        self.repository, self.name = repository, name

    def read(self) -> dict[str, Any]:
        return {"schema_version": 1, "items": self.repository.load()[self.name]}

    def write(self, value: dict[str, Any]) -> None:
        state = self.repository.load()
        state[self.name] = value["items"]
        self.repository.commit(state)
