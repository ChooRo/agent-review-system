"""Repository contract and JSON implementation for procurement-review business data."""

from pathlib import Path
from typing import Any

from app.repositories.json_store import JsonStore


class ReviewRepository:
    """Single business-data boundary; services never open JSON files directly."""

    collections = ("projects", "tasks", "findings", "comments", "events", "audit", "idempotency")

    def __init__(self, root: Path) -> None:
        self._store = JsonStore(root / "review_data.json")

    def load(self) -> dict[str, list[dict[str, Any]]]:
        value = self._store.read()
        return {name: list(value.get(name, [])) for name in self.collections}

    def commit(self, value: dict[str, list[dict[str, Any]]]) -> None:
        self._store.write({"schema_version": 1, **{name: value.get(name, []) for name in self.collections}})

    def collection(self, name: str) -> "ReviewCollection":
        if name not in self.collections:
            raise ValueError(f"未知业务集合：{name}")
        return ReviewCollection(self, name)


class ReviewCollection:
    """Compatibility-sized repository contract for one real business aggregate."""

    def __init__(self, repository: ReviewRepository, name: str) -> None:
        self.repository, self.name = repository, name

    def read(self) -> dict[str, Any]:
        return {"schema_version": 1, "items": self.repository.load()[self.name]}

    def write(self, value: dict[str, Any]) -> None:
        state = self.repository.load()
        state[self.name] = value["items"]
        self.repository.commit(state)
