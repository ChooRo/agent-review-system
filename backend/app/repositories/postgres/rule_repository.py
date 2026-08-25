"""可执行规则的 PostgreSQL 实现；按规则/快照/审计记录行级同步。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, TypeVar

from psycopg.types.json import Jsonb

from app.repositories.postgres.db import lock_table, run_with_retry, transaction

T = TypeVar("T")
_TABLES = {"rules": "rules", "versions": "rule_versions", "audit": "rule_audit"}


def _read_collection(conn, name: str) -> list[dict[str, Any]]:
    return [row["data"] for row in conn.execute(f'SELECT data FROM "{_TABLES[name]}" ORDER BY seq')]


def _identity(name: str, item: dict[str, Any], index: int) -> str:
    if name == "versions":
        return str(item.get("snapshot_id") or f"{item.get('id', '')}:{item.get('recorded_at', '')}:{index}")
    return str(item.get("id") or index)


def _sync_collection(conn, name: str, before: list[dict[str, Any]], after: list[dict[str, Any]]) -> None:
    table = _TABLES[name]
    rows = { _identity(name, row["data"], index): (row["seq"], row["data"]) for index, row in enumerate(conn.execute(f'SELECT seq,data FROM "{table}" ORDER BY seq')) }
    old = {_identity(name, item, index): (rows.get(_identity(name, item, index), (None, item))[0], item) for index, item in enumerate(before)}
    new = {_identity(name, item, index): (index, item) for index, item in enumerate(after)}
    next_seq = conn.execute(f'SELECT COALESCE(MAX(seq), -1) + 1 AS seq FROM "{table}"').fetchone()["seq"]
    for key, (index, item) in new.items():
        if old.get(key, (None, None))[1] == item:
            continue
        if key in old:
            seq = old[key][0]
            row = conn.execute(f'SELECT seq FROM "{table}" WHERE seq=%s', (seq,)).fetchone() if seq is not None else None
            if row:
                conn.execute(f'UPDATE "{table}" SET data=%s WHERE seq=%s', (Jsonb(item), seq))
                continue
        conn.execute(f'INSERT INTO "{table}" (seq,data) VALUES (%s,%s)', (next_seq, Jsonb(item)))
        next_seq += 1
    for key, (seq, _item) in old.items():
        if key not in new:
            if seq is not None:
                conn.execute(f'DELETE FROM "{table}" WHERE seq=%s', (seq,))


class PostgresRuleRepository:
    """规则资产、不可变快照及审计记录的持久化边界。"""

    def __init__(self, data_dir: Any) -> None:
        self.data_dir = data_dir

    def transaction(self, mutate: Callable[[dict[str, list[dict[str, Any]]]], T]) -> T:
        return run_with_retry(lambda: self._transaction(mutate))

    def _transaction(self, mutate: Callable[[dict[str, list[dict[str, Any]]]], T]) -> T:
        with transaction() as conn:
            for name in _TABLES:
                lock_table(conn, f"rules:{name}")
            state = {name: _read_collection(conn, name) for name in _TABLES}
            original = deepcopy(state)
            result = mutate(state)
            for name in _TABLES:
                if state[name] != original[name]:
                    _sync_collection(conn, name, original[name], state[name])
            return result

    def current(self, rule_id: str) -> dict[str, Any] | None:
        with transaction() as conn:
            row = conn.execute("SELECT data FROM rules WHERE data->>'id'=%s", (rule_id,)).fetchone()
            return deepcopy(row["data"]) if row else None

    def list_current(self) -> list[dict[str, Any]]:
        with transaction() as conn:
            return [deepcopy(row["data"]) for row in conn.execute("SELECT data FROM rules ORDER BY seq")]

    def versions(self, rule_id: str) -> list[dict[str, Any]]:
        with transaction() as conn:
            return [deepcopy(row["data"]) for row in conn.execute("SELECT data FROM rule_versions WHERE data->>'id'=%s ORDER BY seq", (rule_id,))]

    def published(self, module: str | None = None) -> list[dict[str, Any]]:
        with transaction() as conn:
            current = [row["data"] for row in conn.execute("SELECT data FROM rules WHERE COALESCE(data->>'status','') <> 'expired' ORDER BY seq")]
            if module:
                current = [rule for rule in current if rule.get("module") == module]
            snapshots = [row["data"] for row in conn.execute("SELECT data FROM rule_versions WHERE data->>'status'='published' ORDER BY seq")]
            latest = {}
            for snapshot in snapshots:
                latest[snapshot.get("id")] = snapshot
            return [deepcopy(latest[rule["id"]]) for rule in current if rule.get("id") in latest]

    def applicable_rules(self, module: str) -> list[dict[str, Any]]:
        return self.published(module)
