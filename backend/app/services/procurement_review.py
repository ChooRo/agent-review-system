from __future__ import annotations

import hashlib
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from app.core.auth_store import AuthStore
from app.core.config import get_settings
from app.repositories.review_repository import ReviewRepository
from app.review_engine.mock_runner import run_procurement_mock

STATES = {"draft", "queued", "parsing", "reviewing", "operator_review", "primary_review", "primary_recheck", "completed", "failed", "cancelled"}
ALLOWED_TYPES = {".pdf": {"application/pdf"}, ".doc": {"application/msword", "application/octet-stream"}, ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/zip", "application/octet-stream"}}

def now() -> str: return datetime.now(UTC).isoformat()
def uid(prefix: str) -> str: return f"{prefix}_{uuid.uuid4().hex}"
def fail(code: int, detail: str) -> None: raise HTTPException(code, detail)


class ProcurementReviewService:
    def __init__(self) -> None:
        root = Path(get_settings().data_dir)
        self.repository = ReviewRepository(root)
        self.projects, self.tasks, self.findings = (self.repository.collection("projects"), self.repository.collection("tasks"), self.repository.collection("findings"))
        self.events_store, self.comments, self.audit, self.keys = (self.repository.collection("events"), self.repository.collection("comments"), self.repository.collection("audit"), self.repository.collection("idempotency"))
        self.auth = AuthStore()

    def create_project(self, payload: dict[str, Any], user: dict, key: str | None) -> dict:
        self._role(user, "operator"); replay = self._replay(key)
        if replay: return replay
        data = self.projects.read()
        if any(x["project_code"] == payload["project_code"] for x in data["items"]): fail(409, "项目编号已存在")
        item = {**payload, "id": uid("prj"), "status": "draft", "task_ids": [], "archive_index": [], "created_by": user["id"], "created_at": now(), "updated_at": now(), "version": 1}
        data["items"].append(item); self.projects.write(data); self._audit(user, "project_created", item["id"]); return self._remember(key, item)

    def list_projects(self, user: dict) -> list[dict]:
        rows = self.projects.read()["items"]
        return rows if "admin" in user["role_codes"] or "supervisor" in user["role_codes"] else [x for x in rows if x["created_by"] == user["id"]]

    def get_project(self, project_id: str, user: dict) -> dict:
        project = self._project(project_id, user)
        return self._project_out(project, user, [x for x in self.tasks.read()["items"] if x["project_id"] == project_id])

    def update_project(self, project_id: str, payload: dict[str, Any], user: dict) -> dict:
        project, data = self._project_row(project_id); self._project_access(project, user)
        if project["created_by"] != user["id"]: fail(403, "仅创建人可修改项目")
        self._version(project, payload.pop("version")); project.update(payload); project["version"] += 1; project["updated_at"] = now(); self.projects.write(data); self._audit(user, "project_updated", project_id); return project

    def create_task(self, project_id: str, payload: dict[str, Any], user: dict, key: str | None) -> dict:
        self._role(user, "operator"); project, projects = self._project_row(project_id)
        if project["created_by"] != user["id"]: fail(404, "项目不存在")
        replay = self._replay(key)
        if replay: return replay
        tasks = self.tasks.read()
        if any(x["project_id"] == project_id and x["status"] != "cancelled" for x in tasks["items"]): fail(409, "项目已有有效采购文件审查任务")
        primary = next((x for x in self.auth._read()["users"] if "supervisor" in x["role_codes"] and x["department"] == "采购部门"), None)
        if not primary: fail(500, "未配置采购部门主责监督")
        collaborators = [x for x in self.auth._read()["users"] if x["id"] in payload["collaborative_supervisor_ids"] and "supervisor" in x["role_codes"] and x["id"] != primary["id"]]
        item = {"id": uid("prt"), "project_id": project_id, "title": payload["title"], "status": "draft", "operator_id": user["id"], "members": [{"user_id": user["id"], "task_role": "operator", "department": user["department"], "module_scope": ["procurement"]}, {"user_id": primary["id"], "task_role": "primary_supervisor", "department": primary["department"], "module_scope": ["procurement"]}] + [{"user_id": x["id"], "task_role": "collaborative_supervisor", "department": x["department"], "module_scope": ["procurement"]} for x in collaborators], "document": None, "engine_run_id": None, "created_at": now(), "updated_at": now(), "version": 1}
        tasks["items"].append(item); self.tasks.write(tasks); project["task_ids"].append(item["id"]); project["version"] += 1; self.projects.write(projects); self._event(item, user, None, "draft", "任务已创建"); return self._remember(key, self._task_out(item, user))

    def tasks_for_project(self, project_id: str, user: dict) -> list[dict]: self._project(project_id, user); return [self._task_out(x, user) for x in self.tasks.read()["items"] if x["project_id"] == project_id]
    def get_task(self, project_id: str, task_id: str, user: dict) -> dict: task, _ = self._task(project_id, task_id); self._task_access(task, user); return self._task_out(task, user)
    def events(self, project_id: str, task_id: str, user: dict, after: str | None = None) -> list[dict]:
        self.get_task(project_id, task_id, user); rows = [x for x in self.events_store.read()["items"] if x["task_id"] == task_id]
        if after:
            index = next((i for i, item in enumerate(rows) if item["id"] == after), None)
            rows = rows[index + 1:] if index is not None else []
        return rows

    def upload(self, project_id: str, task_id: str, file: UploadFile, user: dict) -> dict:
        task, data = self._task(project_id, task_id); self._operator(task, user); self._writable(task)
        if task["status"] != "draft": fail(409, "当前状态不能上传文件")
        suffix = Path(file.filename or "").suffix.lower(); content_type = file.content_type or "application/octet-stream"
        if suffix not in ALLOWED_TYPES or content_type not in ALLOWED_TYPES[suffix]: fail(400, "文件扩展名或 MIME 类型不支持")
        target = Path(get_settings().uploads_dir) / project_id / task_id / uid("doc") / (file.filename or "document")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as out: shutil.copyfileobj(file.file, out)
        size = target.stat().st_size
        if not size or size > get_settings().max_upload_bytes: target.unlink(); fail(413, "文件为空或超过大小限制")
        head = target.read_bytes()[:8]
        if (suffix == ".pdf" and not head.startswith(b"%PDF-")) or (suffix == ".docx" and not head.startswith(b"PK")) or (suffix == ".doc" and not head.startswith(b"\xd0\xcf\x11\xe0")): target.unlink(); fail(400, "文件头与扩展名不匹配")
        with target.open("rb") as stream: digest = hashlib.file_digest(stream, "sha256").hexdigest()
        task["document"] = {"id": target.parent.name, "file_name": file.filename, "content_type": content_type, "size": size, "sha256": digest, "path": str(target), "version": 1, "uploaded_by": user["id"], "uploaded_at": now()}; task["version"] += 1; task["updated_at"] = now(); self.tasks.write(data); self._audit(user, "document_uploaded", task_id); return self._task_out(task, user)

    def start(self, project_id: str, task_id: str, user: dict, key: str | None) -> dict:
        task, data = self._task(project_id, task_id); self._operator(task, user); replay = self._replay(key)
        if replay: return replay
        if not task["document"]: fail(400, "请先上传采购文件")
        if task["status"] not in {"draft", "failed"}: fail(409, "任务已启动")
        for state in ("queued", "parsing", "reviewing"):
            self._transition(task, user, state, "启动审查")
        try:
            candidates = run_procurement_mock(task["document"]["path"])
            findings = self.findings.read()
            for candidate in candidates:
                findings["items"].append({"id": uid("fdg"), "task_id": task_id, "source_type": "ai", "risk_level": candidate["risk_level"], "title": candidate["title"], "description": candidate["description"], "suggestion": candidate["recommendation"], "source": candidate["source"], "rule_refs": candidate["rule_refs"], "version": 1})
            self.findings.write(findings)
            task["status"] = "operator_review"; task["progress"] = 100; task["updated_at"] = now(); task["version"] += 1; self._event(task, user, "reviewing", "operator_review", "审查完成，等待经办处置")
        except Exception as exc:
            task["status"] = "failed"; task["progress"] = 0; task["error"] = str(exc); task["updated_at"] = now(); task["version"] += 1; self._event(task, user, "reviewing", "failed", f"审查失败：{exc}")
        self.tasks.write(data); return self._remember(key, self._task_out(task, user))

    def findings_for_task(self, project_id: str, task_id: str, user: dict) -> list[dict]:
        self.get_task(project_id, task_id, user); return [self._finding_out(x) for x in self.findings.read()["items"] if x["task_id"] == task_id]
    def operator_disposition(self, project_id: str, task_id: str, finding_id: str, payload: dict, user: dict) -> dict:
        task, _ = self._task(project_id, task_id); self._operator(task, user); self._writable(task)
        if task["status"] != "operator_review": fail(409, "当前不接受经办处置")
        if payload["action"] != "accept" and not payload.get("comment"): fail(422, "非采纳处置必须填写 comment")
        finding, data = self._finding(task_id, finding_id); self._version(finding, payload["version"]); finding["operator_disposition"] = {"action": payload["action"], "comment": payload.get("comment"), "by": user["id"], "at": now()}; finding["version"] += 1; self.findings.write(data); self._audit(user, "operator_disposition", finding_id); return finding
    def operator_submit(self, project_id: str, task_id: str, user: dict, key: str | None) -> dict:
        task, data = self._task(project_id, task_id); self._operator(task, user); replay = self._replay(key)
        if replay: return replay
        if task["status"] != "operator_review": fail(409, "当前不能提交复核")
        if any(not x.get("operator_disposition") for x in self.findings.read()["items"] if x["task_id"] == task_id): fail(400, "仍有候选问题未处置")
        self._transition(task, user, "primary_review", "经办已提交复核"); self.tasks.write(data); return self._remember(key, task)
    def primary_decision(self, project_id: str, task_id: str, finding_id: str, payload: dict, user: dict) -> dict:
        task, _ = self._task(project_id, task_id); self._primary(task, user); self._writable(task)
        if task["status"] not in {"primary_review", "primary_recheck"}: fail(409, "当前不接受主责复核")
        finding, data = self._finding(task_id, finding_id); self._version(finding, payload["version"]); finding["primary_decision"] = {**payload, "by": user["id"], "at": now()}; finding.pop("recheck_required", None); finding["version"] += 1; self.findings.write(data); self._audit(user, "primary_decision", finding_id); return finding
    def primary_confirm(self, project_id: str, task_id: str, user: dict, key: str | None) -> dict:
        task, data = self._task(project_id, task_id); self._primary(task, user); replay = self._replay(key)
        if replay: return replay
        if task["status"] not in {"primary_review", "primary_recheck"}: fail(409, "当前不能确认")
        rows = [x for x in self.findings.read()["items"] if x["task_id"] == task_id]
        if any(not x.get("primary_decision") or x.get("recheck_required") for x in rows): fail(400, "仍有问题未完成主责复核")
        self._transition(task, user, "completed", "主责确认正式复核结果"); self.tasks.write(data); return self._remember(key, task)
    def collaborative_comment(self, project_id: str, task_id: str, finding_id: str, comment_id: str | None, payload: dict, user: dict) -> dict:
        task, data = self._task(project_id, task_id); self._collaborator(task, user); self._writable(task); finding, findings = self._finding(task_id, finding_id)
        rows = self.comments.read()
        comment = next((x for x in rows["items"] if x["id"] == comment_id and x["finding_id"] == finding_id), None) if comment_id else None
        if comment_id and (not comment or comment["author_id"] != user["id"]): fail(404, "意见不存在")
        if comment and payload.get("version") != comment["version"]: fail(409, "版本冲突")
        before = comment["comment"] if comment else None
        if comment: comment.update({"comment": payload["comment"], "version": comment["version"] + 1, "updated_at": now()})
        else: comment = {"id": uid("cmt"), "task_id": task_id, "finding_id": finding_id, "author_id": user["id"], "department": user["department"], "comment": payload["comment"], "version": 1, "created_at": now(), "updated_at": now()}; rows["items"].append(comment)
        self.comments.write(rows)
        if task["status"] in {"primary_review", "primary_recheck"} and before != comment["comment"]: finding["recheck_required"] = True; finding["version"] += 1; self.findings.write(findings); self._transition(task, user, "primary_recheck", "协同监督意见变更") ; self.tasks.write(data)
        self._audit(user, "collaborative_comment", finding_id); return comment

    def _project_row(self, project_id: str) -> tuple[dict, dict]:
        data = self.projects.read(); row = next((x for x in data["items"] if x["id"] == project_id), None)
        if not row: fail(404, "项目不存在")
        return row, data
    def _project(self, project_id: str, user: dict) -> dict: row, _ = self._project_row(project_id); self._project_access(row, user); return row
    def _task(self, project_id: str, task_id: str) -> tuple[dict, dict]:
        data = self.tasks.read(); row = next((x for x in data["items"] if x["id"] == task_id and x["project_id"] == project_id), None)
        if not row: fail(404, "任务不存在")
        return row, data
    def _finding(self, task_id: str, finding_id: str) -> tuple[dict, dict]:
        data = self.findings.read(); row = next((x for x in data["items"] if x["id"] == finding_id and x["task_id"] == task_id), None)
        if not row: fail(404, "问题不存在")
        return row, data
    def _project_access(self, project: dict, user: dict) -> None:
        if "admin" in user["role_codes"] or project["created_by"] == user["id"] or any(x["user_id"] == user["id"] for t in self.tasks.read()["items"] if t["project_id"] == project["id"] for x in t["members"]): return
        fail(404, "项目不存在")
    def _task_access(self, task: dict, user: dict) -> None:
        if "admin" in user["role_codes"] or any(x["user_id"] == user["id"] for x in task["members"]): return
        fail(404, "任务不存在")
    def _role(self, user: dict, role: str) -> None:
        if role not in user["role_codes"]: fail(403, "无此业务权限")
    def _operator(self, task: dict, user: dict) -> None:
        if task["operator_id"] != user["id"]: fail(404, "任务不存在")
    def _primary(self, task: dict, user: dict) -> None:
        if not any(x["user_id"] == user["id"] and x["task_role"] == "primary_supervisor" and x["department"] == "采购部门" for x in task["members"]): fail(403, "仅采购部门主责监督可操作")
    def _collaborator(self, task: dict, user: dict) -> None:
        if not any(x["user_id"] == user["id"] and x["task_role"] == "collaborative_supervisor" and "procurement" in x["module_scope"] for x in task["members"]): fail(403, "无协同监督权限")
    def _writable(self, task: dict) -> None:
        if task["status"] == "completed": fail(409, "已完成任务只读")
    def _version(self, item: dict, version: int) -> None:
        if item["version"] != version: fail(409, "版本冲突")
    def _transition(self, task: dict, user: dict, state: str, reason: str) -> None:
        if state not in STATES: fail(500, "非法状态")
        before = task["status"]; task["status"] = state; task["version"] += 1; task["updated_at"] = now(); self._event(task, user, before, state, reason); self._audit(user, "task_status_changed", task["id"], {"before": before, "after": state, "reason": reason})
    def _event(self, task: dict, user: dict, before: str | None, after: str, reason: str) -> None:
        data = self.events_store.read(); data["items"].append({"id": uid("evt"), "task_id": task["id"], "actor_id": user["id"], "at": now(), "before_status": before, "after_status": after, "reason": reason}); self.events_store.write(data)
    def _audit(self, user: dict, action: str, target_id: str, details: dict | None = None) -> None:
        data = self.audit.read(); data["items"].append({"id": uid("aud"), "actor_id": user["id"], "action": action, "target_id": target_id, "at": now(), "details": details or {}}); self.audit.write(data)
    def _replay(self, key: str | None) -> dict | None:
        return next((x["response"] for x in self.keys.read()["items"] if key and x["key"] == key), None)
    def _remember(self, key: str | None, response: dict) -> dict:
        if key: data = self.keys.read(); data["items"].append({"key": key, "response": response}); self.keys.write(data)
        return response
    def _finding_out(self, finding: dict) -> dict:
        return {**finding, "recheck_required": finding.get("recheck_required", False), "collaborative_comments": [x for x in self.comments.read()["items"] if x["finding_id"] == finding["id"]]}
    def _task_out(self, task: dict, user: dict) -> dict:
        role = next((x for x in task["members"] if x["user_id"] == user["id"]), None)
        rows = [x for x in self.findings.read()["items"] if x["task_id"] == task["id"]]
        summary = {"total": len(rows), "high": sum(x["risk_level"] == "high" for x in rows), "medium": sum(x["risk_level"] == "medium" for x in rows), "low": sum(x["risk_level"] == "low" for x in rows), "pending": sum(not x.get("primary_decision") for x in rows)}
        document = task.get("document")
        if document: document = {k: v for k, v in document.items() if k != "path"}
        return {**task, "document": document, "finding_summary": summary, "progress": task.get("progress", 0), "task_role": role["task_role"] if role else None, "module_scope": role["module_scope"] if role else []}
    def _project_out(self, project: dict, user: dict, tasks: list[dict]) -> dict:
        return {**project, "task_summaries": [self._task_out(x, user) for x in tasks]}
