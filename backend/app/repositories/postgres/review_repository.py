from __future__ import annotations

import threading
from datetime import date, datetime
from typing import Any, Callable, TypeVar

from psycopg.types.json import Jsonb

from app.repositories.postgres.db import lock_table, run_with_retry, transaction

T = TypeVar("T")
COLLECTIONS = ("projects", "tasks", "findings", "comments", "events", "audit", "idempotency")


def _json(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (datetime, date)) else value


def _rows(conn, sql: str, args=()):
    return conn.execute(sql, args).fetchall()


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    """列值为 NULL 时去掉键，恢复 JSON 时代"缺席即缺席"的往返语义。"""
    return {k: v for k, v in d.items() if v is not None}


def _state(conn) -> dict[str, list[dict[str, Any]]]:
    projects, project_map = [], {}
    for row in _rows(conn, "SELECT * FROM projects ORDER BY created_at,id"):
        item = {k: _json(v) for k, v in _drop_none(dict(row)).items()}
        pid = item["id"]
        item["archive_index"] = [r["value"] for r in _rows(conn, "SELECT value FROM project_archive_items WHERE project_id=%s ORDER BY item_order", (pid,))]
        item["task_ids"] = []
        projects.append(item); project_map[pid] = item

    tasks = []
    for row in _rows(conn, "SELECT * FROM tasks ORDER BY created_at,id"):
        raw = {k: _json(v) for k, v in _drop_none(dict(row)).items()}
        item = {**(raw.pop("payload") or {}), **raw}
        docs = [{k: _json(v) for k, v in dict(d).items()} for d in _rows(conn, "SELECT id,file_name,content_type,size,sha256,path,version,uploaded_by,uploaded_at FROM documents WHERE task_id=%s ORDER BY version", (item["id"],))]
        item["document_versions"] = docs
        if docs: item["document"] = docs[-1]
        item["members"] = [{k: _json(v) for k, v in dict(m).items()} for m in _rows(conn, "SELECT user_id,task_role,department,module_scope FROM task_members WHERE task_id=%s ORDER BY user_id,task_role", (item["id"],))]
        tasks.append(item)
        if item.get("project_id") in project_map: project_map[item["project_id"]]["task_ids"].append(item["id"])

    findings = []
    for row in _rows(conn, "SELECT * FROM findings ORDER BY id"):
        raw = {k: _json(v) for k, v in _drop_none(dict(row)).items()}; findings.append({**(raw.pop("payload") or {}), **raw})

    # 事件/审计等表的可空列（如 before_status）在 JSON 时代显式存 None，
    # 读侧保留原键（None），不剥除，避免响应模型缺字段。
    def simple(table: str, order: str = "id"):
        return [{k: _json(v) for k, v in dict(row).items()} for row in _rows(conn, f"SELECT * FROM {table} ORDER BY {order}")]
    return {"projects": projects, "tasks": tasks, "findings": findings, "comments": simple("comments"), "events": simple("events", "at"), "audit": simple("audit", "at"), "idempotency": simple("idempotency", "key")}


def _replace(conn, state: dict[str, list[dict[str, Any]]]) -> None:
    for table in ("project_archive_items", "comments", "events", "audit", "idempotency", "findings", "task_members", "documents", "tasks", "projects"):
        conn.execute(f"DELETE FROM {table}")
    for p in state["projects"]:
        cols = ("id","name","project_code","handling_department","project_owner","project_owner_id","status","created_by","created_at","updated_at","version")
        conn.execute(f"INSERT INTO projects ({','.join(cols)}) VALUES ({','.join(['%s']*len(cols))})", [p.get(k) for k in cols])
        for i, value in enumerate(p.get("archive_index", [])): conn.execute("INSERT INTO project_archive_items VALUES (%s,%s,%s)", (p["id"], i, Jsonb(value)))

    cols = ("id","project_id","title","status","operator_id","engine_run_id","created_at","updated_at","version","progress","error")
    for task in state["tasks"]:
        known = set(cols) | {"document", "document_versions", "members"}; payload = {k:v for k,v in task.items() if k not in known}
        all_cols = cols + ("payload",)
        values = [task.get(k) for k in cols]
        conn.execute(f"INSERT INTO tasks ({','.join(all_cols)}) VALUES ({','.join(['%s']*len(all_cols))})", values + [Jsonb(payload)])
        for doc in task.get("document_versions") or ([task["document"]] if task.get("document") else []):
            dcols = ("id","task_id","file_name","content_type","size","sha256","path","version","uploaded_by","uploaded_at")
            conn.execute(f"INSERT INTO documents ({','.join(dcols)}) VALUES ({','.join(['%s']*len(dcols))})", [doc.get("id"),task["id"]]+[doc.get(k) for k in dcols[2:]])
        for member in task.get("members", []): conn.execute("INSERT INTO task_members VALUES (%s,%s,%s,%s,%s)", (task["id"],member["user_id"],member["task_role"],member["department"],Jsonb(member.get("module_scope",[]))))

    cols = ("id","task_id","source_type","risk_level","title","description","suggestion","document_version","rectification_status","rectification_version","version")
    for finding in state["findings"]:
        payload = {k:v for k,v in finding.items() if k not in cols}; all_cols = cols + ("payload",)
        conn.execute(f"INSERT INTO findings ({','.join(all_cols)}) VALUES ({','.join(['%s']*len(all_cols))})", [finding.get(k) for k in cols]+[Jsonb(payload)])
    for table, cols in (("comments",("id","task_id","finding_id","author_id","department","comment","version","created_at","updated_at")), ("events",("id","task_id","actor_id","at","before_status","after_status","reason")), ("audit",("id","actor_id","action","target_id","at","details"))):
        for item in state[table]:
            values = [item.get(k) for k in cols]
            if table == "audit": values[-1] = Jsonb(values[-1] or {})
            conn.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join(['%s']*len(cols))})", values)
    for item in state["idempotency"]: conn.execute("INSERT INTO idempotency (key,response) VALUES (%s,%s)", (item["key"],Jsonb(item["response"])))


class PostgresReviewCollection:
    def __init__(self, repository: "PostgresReviewRepository", name: str): self.repository, self.name = repository, name
    def read(self) -> dict[str, Any]:
        return {"schema_version": 2, "items": self.repository.load()[self.name]}
    def write(self, value: dict[str, Any]) -> None: self.repository.transaction(lambda state: state.__setitem__(self.name, value["items"]))


class PostgresReviewRepository:
    def __init__(self, root: Any) -> None:
        self.root = root
        self._tl = threading.local()

    def load(self) -> dict[str, list[dict[str, Any]]]:
        # 同一请求线程内的重复全量读只查一次库（服务层一次请求会 read() 十几次）；
        # 写事务后失效重建，写路径 _transaction 永远读库，不受缓存影响。
        cached = getattr(self._tl, "state", None)
        if cached is None:
            with transaction() as conn: cached = _state(conn)
            self._tl.state = cached
        return cached

    def commit(self, value: dict[str, list[dict[str, Any]]]) -> None:
        self.transaction(lambda state: state.update({name:value.get(name,[]) for name in COLLECTIONS}))
    def transaction(self, mutate: Callable[[dict[str, Any]], T]) -> T: return run_with_retry(lambda: self._transaction(mutate))
    def _transaction(self, mutate: Callable[[dict[str, Any]], T]) -> T:
        try:
            with transaction() as conn:
                for name in COLLECTIONS: lock_table(conn, f"review:{name}")
                state = _state(conn); result = mutate(state); _replace(conn, state); return result
        finally:
            self._tl.__dict__.pop("state", None)
    def collection(self, name: str) -> PostgresReviewCollection:
        if name not in COLLECTIONS: raise ValueError(f"未知业务集合：{name}")
        return PostgresReviewCollection(self, name)
