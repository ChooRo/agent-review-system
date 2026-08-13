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
from app.policies import common as common_policy
from app.policies import review as review_policy
from app.repositories.review_repository import ReviewRepository
import threading
import json

from app.services.procurement_workflow import run_review_workflow

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
        owner_value = payload["project_owner"]
        owner = self.auth.get_user_by_id(owner_value) if isinstance(owner_value, int) else self.auth.get_user_by_username(owner_value)
        if not owner or not owner.get("is_active"):
            fail(422, "项目负责人不存在或已停用")
        payload["project_owner_id"] = owner["id"]
        payload["project_owner"] = owner["display_name"]
        data = self.projects.read()
        if any(x["project_code"] == payload["project_code"] for x in data["items"]): fail(409, "项目编号已存在")
        item = {**payload, "id": uid("prj"), "status": "draft", "task_ids": [], "archive_index": [], "created_by": user["id"], "created_at": now(), "updated_at": now(), "version": 1}
        data["items"].append(item); self.projects.write(data); self._audit(user, "project_created", item["id"]); return self._remember(key, item)

    def list_projects(self, user: dict) -> list[dict]:
        rows = self.projects.read()["items"]
        return rows if review_policy.can_list_all_projects(user) else [x for x in rows if x["created_by"] == user["id"]]

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
        primary = next((x for x in self.auth._read()["users"] if review_policy.can_be_primary_supervisor(x)), None)
        if not primary: fail(500, "未配置采购部门主责监督")
        collaborators = [x for x in self.auth._read()["users"] if x["id"] in payload["collaborative_supervisor_ids"] and review_policy.can_be_collaborative_supervisor(x) and x["id"] != primary["id"]]
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

    def debug_traces(self, project_id: str, task_id: str, user: dict) -> dict[str, Any]:
        """Return a read-only, redacted view of one task's AI execution trace."""
        task, _ = self._task(project_id, task_id)
        self._task_access(task, user)
        run_id = task.get("engine_run_id")
        if not run_id:
            return {"task_id": task_id, "run_id": None, "status": task.get("status"), "llm_calls": [], "tool_calls": [], "events": []}
        config_path = Path(__file__).resolve().parents[1] / "review_config.json"
        config = load_settings(config_path if config_path.is_file() else None)
        run_dir = Path(config["runtime"]["runs_root"]) / run_id
        if not run_dir.is_dir():
            return {"task_id": task_id, "run_id": run_id, "status": task.get("status"), "llm_calls": [], "tool_calls": [], "events": []}
        events = []
        events_path = run_dir / "events.jsonl"
        if events_path.is_file():
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line]
        llm_calls = []
        for trace_path in sorted((run_dir / "llm_traces").glob("*.json")):
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            llm_calls.append({"id": trace_path.stem, "step": trace.get("step", trace_path.stem.rsplit("_", 1)[0]), **trace})
        tool_calls = [event for event in events if event.get("event") == "tool_called"]
        return {
            "task_id": task_id, "run_id": run_id, "status": task.get("status"),
            "state": self._read_debug_json(run_dir / "state.json"),
            "llm_calls": llm_calls, "tool_calls": tool_calls,
            "events": events,
        }

    @staticmethod
    def _read_debug_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

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
        previous_run_id = task.get("engine_run_id")
        task.pop("error", None)
        for state in ("queued", "parsing", "reviewing"):
            self._transition(task, user, state, "启动审查")
        task["progress"] = 5; task["updated_at"] = now(); task["version"] += 1
        self.tasks.write(data)
        outcome = self._remember(key, self._task_out(task, user))
        project, _ = self._project_row(project_id)
        task_context = {"project": {"name": project.get("name"), "project_code": project.get("project_code")}, "title": task.get("title")}
        thread = threading.Thread(
            target=run_review_workflow,
            args=(project_id, task_id, task["document"]["path"], self._store_review_results, self._fail_review_task, self._update_review_progress, previous_run_id, task_context),
            daemon=True,
        )
        thread.start()
        return outcome

    def _store_review_results(self, project_id: str, task_id: str, report: dict) -> None:
        def persist(state: dict) -> None:
            task = next((t for t in state["tasks"] if t["id"] == task_id), None)
            if not task:
                return
            for finding in report.get("findings", []):
                evidence = finding.get("evidence") or []
                first = evidence[0] if evidence else {}
                source_info = {
                    "page": first.get("page_no"),
                    "section_path": first.get("heading_path", []),
                    "quote": (first.get("quote") or finding.get("description", ""))[:500],
                    "block_id": ",".join(finding.get("evidence_block_ids", [])[:5]),
                }
                source_refs = [{"page": item.get("page_no"), "section_path": item.get("heading_path", []), "quote": (item.get("quote") or finding.get("description", ""))[:500], "block_id": item.get("block_id")} for item in evidence[:10]]
                legal_refs = [{"legal_unit_id": u.get("legal_unit_id"), "document_title": u.get("document_title"), "article_no": u.get("article_no"), "quote": u.get("quote") or u.get("text"), "page": u.get("page") or u.get("page_no")} for u in finding.get("legal_evidence", [])[:10]]
                rule_refs = [{"id": rule_id} for rule_id in finding.get("rule_ids", []) if rule_id]
                state["findings"].append({
                    "id": uid("fdg"), "task_id": task_id, "source_type": "ai",
                    "risk_level": finding.get("risk_level", "unknown"),
                    "title": finding.get("title", "审查发现"),
                    "description": finding.get("description", ""),
                    "suggestion": finding.get("recommendation", ""),
                    "source": source_info, "sources": source_refs, "rule_refs": rule_refs, "legal_refs": legal_refs, "version": 1,
                })
            task["status"] = "operator_review"; task["progress"] = 100; task["updated_at"] = now(); task["version"] += 1
            task["engine_run_id"] = report.get("engine_run_id", task.get("engine_run_id"))
            task.pop("error", None)
            task["quality"] = report.get("quality", {"status": "unknown"})
            task["legal_facts"] = report.get("task_legal_facts", {})
            task["legal_applicability"] = report.get("legal_applicability", [])
            task["legal_context_freeze"] = report.get("legal_context_freeze", [])
            state["audit"].append({"id": uid("aud"), "actor_id": "system", "action": "review_completed", "target_id": task_id, "at": now(), "details": {}})
        self.repository.transaction(persist)

    def _fail_review_task(self, project_id: str, task_id: str, error_msg: str) -> None:
        def persist(state: dict) -> None:
            task = next((t for t in state["tasks"] if t["id"] == task_id), None)
            if not task: return
            task["status"] = "failed"; task["error"] = error_msg; task["updated_at"] = now(); task["version"] += 1
            state["audit"].append({"id": uid("aud"), "actor_id": "system", "action": "review_failed", "target_id": task_id, "at": now(), "details": {"error": error_msg}})
        self.repository.transaction(persist)

    def _update_review_progress(self, project_id: str, task_id: str, run_id: str, step: str | None, completed: int, total: int) -> None:
        def persist(state: dict) -> None:
            task = next((t for t in state["tasks"] if t["id"] == task_id and t["project_id"] == project_id), None)
            if not task:
                return
            task["engine_run_id"] = run_id
            task["progress"] = max(task.get("progress", 5), 5 + completed * 90 // total)
            task["updated_at"] = now()
            task["version"] += 1
            if step:
                state["events"].append({"id": uid("evt"), "task_id": task_id, "actor_id": 0, "at": now(), "before_status": task["status"], "after_status": task["status"], "reason": f"审查引擎阶段完成：{step}"})
        self.repository.transaction(persist)

    def _audit_impl(self, task_id: str, user_id: str, action: str, target_id: str, details: dict | None = None) -> None:
        data = self.audit.read()
        data["items"].append({"id": uid("aud"), "actor_id": user_id, "action": action, "target_id": target_id, "at": now(), "details": details or {}})
        self.audit.write(data)

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
        if review_policy.can_access_project(project, self.tasks.read()["items"], user): return
        fail(404, "项目不存在")
    def _task_access(self, task: dict, user: dict) -> None:
        if review_policy.can_access_task(task, user): return
        fail(404, "任务不存在")
    def _role(self, user: dict, role: str) -> None:
        if not common_policy.has_role(user, role): fail(403, "无此业务权限")
    def _operator(self, task: dict, user: dict) -> None:
        if not review_policy.is_task_operator(task, user): fail(404, "任务不存在")
    def _primary(self, task: dict, user: dict) -> None:
        if not review_policy.is_primary_supervisor(task, user): fail(403, "仅采购部门主责监督可操作")
    def _collaborator(self, task: dict, user: dict) -> None:
        if not review_policy.is_collaborative_supervisor(task, user): fail(403, "无协同监督权限")
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
        return {**finding, "rule_refs": finding.get("rule_refs", []), "legal_refs": finding.get("legal_refs", []), "recheck_required": finding.get("recheck_required", False), "collaborative_comments": [x for x in self.comments.read()["items"] if x["finding_id"] == finding["id"]]}
    def _task_out(self, task: dict, user: dict) -> dict:
        role = review_policy.task_member(task, user)
        rows = [x for x in self.findings.read()["items"] if x["task_id"] == task["id"]]
        summary = {"total": len(rows), "high": sum(x["risk_level"] == "high" for x in rows), "medium": sum(x["risk_level"] == "medium" for x in rows), "low": sum(x["risk_level"] == "low" for x in rows), "pending": sum(not x.get("primary_decision") for x in rows)}
        document = task.get("document")
        if document: document = {k: v for k, v in document.items() if k != "path"}
        output = {key: value for key, value in task.items() if key != "execution" + "_mode"}
        return {**output, "document": document, "finding_summary": summary, "progress": task.get("progress", 0), "task_role": role["task_role"] if role else None, "module_scope": role["module_scope"] if role else [], "quality": task.get("quality", {"status": "pending"}), "legal_facts": task.get("legal_facts", {}), "legal_applicability": task.get("legal_applicability", []), "legal_context_freeze": task.get("legal_context_freeze", []), "error": task.get("error")}
    def _project_out(self, project: dict, user: dict, tasks: list[dict]) -> dict:
        return {**project, "task_summaries": [self._task_out(x, user) for x in tasks]}
