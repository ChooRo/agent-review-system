from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from typing import Any, Callable, TypeVar

from psycopg.types.json import Jsonb

from app.repositories.postgres.db import lock_table, run_with_retry, transaction

T = TypeVar("T")
COLLECTIONS = ("projects", "tasks", "findings", "comments", "events", "audit", "idempotency")


def _json(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (datetime, date)) else value


def _drop_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def _simple_rows(conn, table: str, order: str) -> list[dict[str, Any]]:
    return [{key: _json(value) for key, value in dict(row).items()} for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")]


def _read_collection(conn, name: str) -> list[dict[str, Any]]:
    if name == "projects":
        projects = []
        for row in conn.execute("SELECT * FROM projects ORDER BY created_at,id"):
            item = {key: _json(value) for key, value in _drop_none(dict(row)).items()}
            item["archive_index"] = [row["value"] for row in conn.execute("SELECT value FROM project_archive_items WHERE project_id=%s ORDER BY item_order", (item["id"],))]
            item["task_ids"] = [row["id"] for row in conn.execute("SELECT id FROM tasks WHERE project_id=%s ORDER BY created_at,id", (item["id"],))]
            projects.append(item)
        return projects
    if name == "tasks":
        tasks = []
        for row in conn.execute("SELECT * FROM tasks ORDER BY created_at,id"):
            raw = {key: _json(value) for key, value in _drop_none(dict(row)).items()}
            item = {**(raw.pop("payload") or {}), **raw}
            docs = [{key: _json(value) for key, value in dict(doc).items()} for doc in conn.execute(
                "SELECT id,file_name,content_type,size,sha256,path,version,uploaded_by,uploaded_at FROM documents WHERE task_id=%s ORDER BY version", (item["id"],)
            )]
            item["document_versions"] = docs
            if docs:
                item["document"] = docs[-1]
            item["members"] = [{key: _json(value) for key, value in dict(member).items()} for member in conn.execute(
                "SELECT user_id,task_role,department,module_scope FROM task_members WHERE task_id=%s ORDER BY user_id,task_role", (item["id"],)
            )]
            tasks.append(item)
        return tasks
    if name == "findings":
        findings = []
        for row in conn.execute("SELECT * FROM findings ORDER BY id"):
            raw = {key: _json(value) for key, value in _drop_none(dict(row)).items()}
            findings.append({**(raw.pop("payload") or {}), **raw})
        return findings
    return {
        "comments": lambda: _simple_rows(conn, "comments", "id"),
        "events": lambda: _simple_rows(conn, "events", "at,id"),
        "audit": lambda: _simple_rows(conn, "audit", "at,id"),
        "idempotency": lambda: _simple_rows(conn, "idempotency", "key"),
    }[name]()


class _ReviewState:
    """Lazy collection view; only touched collections are read and flushed."""

    def __init__(self, conn) -> None:
        self.conn = conn
        self.values: dict[str, list[dict[str, Any]]] = {}
        self.originals: dict[str, list[dict[str, Any]]] = {}

    def __getitem__(self, name: str) -> list[dict[str, Any]]:
        if name not in COLLECTIONS:
            raise KeyError(name)
        if name not in self.values:
            value = _read_collection(self.conn, name)
            self.values[name] = value
            self.originals[name] = deepcopy(value)
        return self.values[name]

    def __setitem__(self, name: str, value: list[dict[str, Any]]) -> None:
        self[name]
        self.values[name] = value

    def update(self, values: dict[str, list[dict[str, Any]]]) -> None:
        for name, value in values.items():
            if name in COLLECTIONS:
                self[name] = value

    def flush(self) -> None:
        for name, value in self.values.items():
            if value != self.originals[name]:
                _sync_collection(self.conn, name, self.originals[name], value)


def _upsert(conn, table: str, conflict: str, columns: tuple[str, ...], values: list[Any]) -> None:
    assignments = ",".join(f"{column}=EXCLUDED.{column}" for column in columns if column not in conflict.split(","))
    conn.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join(['%s'] * len(columns))}) "
        f"ON CONFLICT ({conflict}) DO UPDATE SET {assignments}", values,
    )


def _sync_projects(conn, before: list[dict[str, Any]], after: list[dict[str, Any]]) -> None:
    columns = ("id", "name", "project_code", "handling_department", "project_owner", "project_owner_id", "status", "created_by", "created_at", "updated_at", "version")
    old, new = {item["id"]: item for item in before}, {item["id"]: item for item in after}
    for key, item in new.items():
        if old.get(key) == item:
            continue
        _upsert(conn, "projects", "id", columns, [item.get(column) for column in columns])
        conn.execute("DELETE FROM project_archive_items WHERE project_id=%s", (key,))
        for order, value in enumerate(item.get("archive_index", [])):
            conn.execute("INSERT INTO project_archive_items (project_id,item_order,value) VALUES (%s,%s,%s)", (key, order, Jsonb(value)))
    for key in old.keys() - new.keys():
        conn.execute("DELETE FROM projects WHERE id=%s", (key,))


def _sync_tasks(conn, before: list[dict[str, Any]], after: list[dict[str, Any]]) -> None:
    columns = ("id", "project_id", "title", "status", "operator_id", "engine_run_id", "created_at", "updated_at", "version", "progress", "error")
    old, new = {item["id"]: item for item in before}, {item["id"]: item for item in after}
    for key, item in new.items():
        if old.get(key) == item:
            continue
        known = set(columns) | {"document", "document_versions", "members"}
        payload = {name: value for name, value in item.items() if name not in known}
        _upsert(conn, "tasks", "id", columns + ("payload",), [item.get(column) for column in columns] + [Jsonb(payload)])
        conn.execute("DELETE FROM documents WHERE task_id=%s", (key,))
        documents = item.get("document_versions") or ([item["document"]] if item.get("document") else [])
        for document in documents:
            dcolumns = ("id", "task_id", "file_name", "content_type", "size", "sha256", "path", "version", "uploaded_by", "uploaded_at")
            _upsert(conn, "documents", "task_id,id", dcolumns, [document.get("id"), key] + [document.get(column) for column in dcolumns[2:]])
        conn.execute("DELETE FROM task_members WHERE task_id=%s", (key,))
        for member in item.get("members", []):
            conn.execute("INSERT INTO task_members (task_id,user_id,task_role,department,module_scope) VALUES (%s,%s,%s,%s,%s)", (key, member["user_id"], member["task_role"], member["department"], Jsonb(member.get("module_scope", []))))
    for key in old.keys() - new.keys():
        conn.execute("DELETE FROM tasks WHERE id=%s", (key,))


def _sync_findings(conn, before: list[dict[str, Any]], after: list[dict[str, Any]]) -> None:
    columns = ("id", "task_id", "source_type", "risk_level", "title", "description", "suggestion", "document_version", "rectification_status", "rectification_version", "version")
    old, new = {item["id"]: item for item in before}, {item["id"]: item for item in after}
    for key, item in new.items():
        if old.get(key) == item:
            continue
        payload = {name: value for name, value in item.items() if name not in set(columns)}
        _upsert(conn, "findings", "id", columns + ("payload",), [item.get(column) for column in columns] + [Jsonb(payload)])
    for key in old.keys() - new.keys():
        conn.execute("DELETE FROM findings WHERE id=%s", (key,))


def _sync_simple(conn, name: str, before: list[dict[str, Any]], after: list[dict[str, Any]]) -> None:
    definitions = {
        "comments": ("id", ("id", "task_id", "finding_id", "author_id", "department", "comment", "version", "created_at", "updated_at")),
        "events": ("id", ("id", "task_id", "actor_id", "at", "before_status", "after_status", "reason")),
        "audit": ("id", ("id", "actor_id", "action", "target_id", "at", "details")),
        "idempotency": ("key", ("key", "response")),
    }
    key, columns = definitions[name]
    old, new = {item[key]: item for item in before}, {item[key]: item for item in after}
    for item_key, item in new.items():
        if old.get(item_key) == item:
            continue
        values = [item.get(column) for column in columns]
        if name == "audit":
            values[-1] = Jsonb(values[-1] or {})
        elif name == "idempotency":
            values[-1] = Jsonb(values[-1])
        _upsert(conn, name, key, columns, values)
    for item_key in old.keys() - new.keys():
        conn.execute(f"DELETE FROM {name} WHERE {key}=%s", (item_key,))


def _sync_collection(conn, name: str, before: list[dict[str, Any]], after: list[dict[str, Any]]) -> None:
    if name == "projects":
        return _sync_projects(conn, before, after)
    if name == "tasks":
        return _sync_tasks(conn, before, after)
    if name == "findings":
        return _sync_findings(conn, before, after)
    return _sync_simple(conn, name, before, after)


class PostgresReviewCollection:
    def __init__(self, repository: "PostgresReviewRepository", name: str) -> None:
        self.repository, self.name = repository, name

    def read(self) -> dict[str, Any]:
        return {"schema_version": 2, "items": self.repository.read_collection(self.name)}

    def write(self, value: dict[str, Any]) -> None:
        self.repository.transaction(lambda state: state.__setitem__(self.name, value["items"]))


class PostgresReviewRepository:
    def __init__(self, root: Any) -> None:
        self.root = root

    def read_collection(self, name: str) -> list[dict[str, Any]]:
        with transaction() as conn:
            return _read_collection(conn, name)

    def load(self) -> dict[str, list[dict[str, Any]]]:
        with transaction() as conn:
            return {name: _read_collection(conn, name) for name in COLLECTIONS}

    def commit(self, value: dict[str, list[dict[str, Any]]]) -> None:
        def persist(state: _ReviewState) -> None:
            state.update({name: value.get(name, []) for name in COLLECTIONS if name in value})
        self.transaction(persist)

    def transaction(self, mutate: Callable[[Any], T]) -> T:
        return run_with_retry(lambda: self._transaction(mutate))

    def _transaction(self, mutate: Callable[[Any], T]) -> T:
        with transaction() as conn:
            for name in COLLECTIONS:
                lock_table(conn, f"review:{name}")
            state = _ReviewState(conn)
            result = mutate(state)
            state.flush()
            return result

    def collection(self, name: str) -> PostgresReviewCollection:
        if name not in COLLECTIONS:
            raise ValueError(f"未知业务集合：{name}")
        return PostgresReviewCollection(self, name)
