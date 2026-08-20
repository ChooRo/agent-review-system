"""可执行规则的 JSON 仓储；法律知识保持独立。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, TypeVar

from app.repositories.json_store import JsonStore

T = TypeVar("T")


class RuleRepository:
    """规则资产、不可变快照及审计记录的持久化边界。"""

    def __init__(self, data_dir: Path) -> None:
        self.store = JsonStore(data_dir / "rules.json")

    def _state(self) -> dict[str, list[dict[str, Any]] | int]:
        value = self.store.read()
        return {
            "schema_version": 1,
            "rules": list(value.get("rules", [])),
            "versions": list(value.get("versions", [])),
            "audit": list(value.get("audit", [])),
        }

    def transaction(self, mutate: Callable[[dict[str, Any]], T]) -> T:
        with JsonStore._lock:
            state = self._state()
            result = mutate(state)
            self.store.write(state)
            return result

    def current(self, rule_id: str) -> dict[str, Any] | None:
        return next((deepcopy(rule) for rule in self._state()["rules"] if rule["id"] == rule_id), None)

    def list_current(self) -> list[dict[str, Any]]:
        return [deepcopy(rule) for rule in self._state()["rules"]]

    def versions(self, rule_id: str) -> list[dict[str, Any]]:
        return [deepcopy(item) for item in self._state()["versions"] if item["id"] == rule_id]

    def published(self, module: str | None = None) -> list[dict[str, Any]]:
        state = self._state()
        snapshots = state["versions"]
        results: list[dict[str, Any]] = []
        for current in state["rules"]:
            if current["status"] == "expired" or module and current["module"] != module:
                continue
            published = [item for item in snapshots if item["id"] == current["id"] and item["status"] == "published"]
            if published:
                results.append(deepcopy(published[-1]))
        return results

    def applicable_rules(self, module: str) -> list[dict[str, Any]]:
        """审查引擎唯一的可执行规则查询：只查询已发布规则。"""
        return self.published(module)
