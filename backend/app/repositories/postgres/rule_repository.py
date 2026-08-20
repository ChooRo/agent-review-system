"""可执行规则的 PostgreSQL 实现；契约与 JSON 版一致，服务层零改动。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, TypeVar

from psycopg.types.json import Jsonb

from app.repositories.postgres.db import lock_table, run_with_retry, transaction

T = TypeVar("T")

# JSON 集合名 -> 表名；业务记录完整存在 data jsonb
_TABLES = {"rules": "rules", "versions": "rule_versions", "audit": "rule_audit"}


def _read_collection(conn, name: str) -> list[dict[str, Any]]:
    return [row["data"] for row in conn.execute(f'SELECT data FROM "{_TABLES[name]}" ORDER BY seq')]


def _replace_collection(conn, name: str, items: list[dict[str, Any]]) -> None:
    table = _TABLES[name]
    conn.execute(f'DELETE FROM "{table}"')
    for seq, item in enumerate(items):
        conn.execute(f'INSERT INTO "{table}" (seq, data) VALUES (%s, %s)', (seq, Jsonb(item)))


class PostgresRuleRepository:
    """规则资产、不可变快照及审计记录的持久化边界。"""

    def __init__(self, data_dir: Any) -> None:
        self.data_dir = data_dir  # 保持签名；Postgres 不使用文件目录

    def transaction(self, mutate: Callable[[dict[str, Any]], T]) -> T:
        """读-改-写按规则集合串行化；序列化冲突时重试（advisory 锁保证重试见新数据）。"""
        return run_with_retry(lambda: self._transaction(mutate))

    def _transaction(self, mutate: Callable[[dict[str, Any]], T]) -> T:
        with transaction() as conn:
            for name in _TABLES:
                lock_table(conn, name)  # 必须先锁后读，避免用过期快照写
            state = {name: _read_collection(conn, name) for name in _TABLES}
            result = mutate(state)
            for name in _TABLES:
                _replace_collection(conn, name, state[name])
            return result

    def _state(self) -> dict[str, list[dict[str, Any]] | int]:
        with transaction() as conn:
            return {"schema_version": 1, **{name: _read_collection(conn, name) for name in _TABLES}}

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
