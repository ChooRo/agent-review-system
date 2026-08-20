from __future__ import annotations

import hashlib
import shutil
import uuid
from difflib import SequenceMatcher
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from app.core.auth_store import get_auth_store
from app.core.config import get_settings
from app.policies import common as common_policy
from app.policies import review as review_policy
from app.repositories.backend import get_review_repository
import threading
import json

from app.services.procurement.workflow import run_review_workflow
from app.review_engine.settings import load_settings

STATES = {"draft", "rectification_draft", "queued", "parsing", "reviewing", "applicability_review", "operator_review", "primary_review", "primary_recheck", "completed", "final_locked", "failed", "cancelled"}
ALLOWED_TYPES = {".pdf": {"application/pdf"}, ".doc": {"application/msword", "application/octet-stream"}, ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/zip", "application/octet-stream"}}

def now() -> str: return datetime.now(UTC).isoformat()
def uid(prefix: str) -> str: return f"{prefix}_{uuid.uuid4().hex}"
def fail(code: int, detail: str) -> None: raise HTTPException(code, detail)


class ProcurementReviewService:
    def __init__(self) -> None:
        root = Path(get_settings().data_dir)
        self.repository = get_review_repository(root)
        self.projects, self.tasks, self.findings = (self.repository.collection("projects"), self.repository.collection("tasks"), self.repository.collection("findings"))
        self.events_store, self.comments, self.audit, self.keys = (self.repository.collection("events"), self.repository.collection("comments"), self.repository.collection("audit"), self.repository.collection("idempotency"))
        self.auth = get_auth_store()

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
        self._expire_stalled_tasks()
        rows = self.projects.read()["items"]
        return rows if review_policy.can_list_all_projects(user) else [x for x in rows if x["created_by"] == user["id"]]

    def get_project(self, project_id: str, user: dict) -> dict:
        self._expire_stalled_tasks()
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
        item = {"id": uid("prt"), "project_id": project_id, "title": payload["title"], "status": "draft", "operator_id": user["id"], "progress": 0, "members": [{"user_id": user["id"], "task_role": "operator", "department": user["department"], "module_scope": ["procurement"]}, {"user_id": primary["id"], "task_role": "primary_supervisor", "department": primary["department"], "module_scope": ["procurement"]}] + [{"user_id": x["id"], "task_role": "collaborative_supervisor", "department": x["department"], "module_scope": ["procurement"]} for x in collaborators], "document": None, "document_versions": [], "final_baseline": None, "engine_run_id": None, "created_at": now(), "updated_at": now(), "version": 1}
        tasks["items"].append(item); self.tasks.write(tasks); project["task_ids"].append(item["id"]); project["version"] += 1; self.projects.write(projects); self._event(item, user, None, "draft", "任务已创建"); return self._remember(key, self._task_out(item, user))

    def tasks_for_project(self, project_id: str, user: dict) -> list[dict]: self._expire_stalled_tasks(); self._project(project_id, user); return [self._task_out(x, user) for x in self.tasks.read()["items"] if x["project_id"] == project_id]
    def get_task(self, project_id: str, task_id: str, user: dict) -> dict: self._expire_stalled_tasks(); task, _ = self._task(project_id, task_id); self._task_access(task, user); return self._task_out(task, user)
    def events(self, project_id: str, task_id: str, user: dict, after: str | None = None) -> list[dict]:
        self._expire_stalled_tasks()
        self.get_task(project_id, task_id, user); rows = [x for x in self.events_store.read()["items"] if x["task_id"] == task_id]
        if after:
            index = next((i for i, item in enumerate(rows) if item["id"] == after), None)
            rows = rows[index + 1:] if index is not None else []
        return rows

    def debug_traces(self, project_id: str, task_id: str, user: dict) -> dict[str, Any]:
        """返回单个任务 AI 执行轨迹的只读脱敏视图。"""
        self._expire_stalled_tasks()
        task, _ = self._task(project_id, task_id)
        self._task_access(task, user)
        run_id = task.get("engine_run_id")
        if not run_id:
            return {"task_id": task_id, "run_id": None, "status": task.get("status"), "llm_calls": [], "tool_calls": [], "events": [], "stage_results": []}
        config_path = Path(__file__).resolve().parents[3] / "review_config.json"
        config = load_settings(config_path if config_path.is_file() else None)
        run_dir = Path(config["runtime"]["runs_root"]) / run_id
        if not run_dir.is_dir():
            return {"task_id": task_id, "run_id": run_id, "status": task.get("status"), "llm_calls": [], "tool_calls": [], "events": [], "stage_results": []}
        events = []
        events_path = run_dir / "events.jsonl"
        if events_path.is_file():
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line]
        llm_calls = []
        for trace_path in sorted((run_dir / "llm_traces").glob("*.json")):
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            step = trace.get("step", trace_path.stem.rsplit("_", 1)[0])
            skill = {"structure_profile": "understand-document-structure", "extract_candidates": "understand-procurement-document", "agent_review": "review-procurement-document"}.get(step)
            llm_calls.append({"id": trace_path.stem, "step": step, "skill": skill, **trace})
        tool_calls = [event for event in events if event.get("event") == "tool_called"]
        stage_results = self._debug_stage_results(run_dir)
        return {
            "task_id": task_id, "run_id": run_id, "status": task.get("status"),
            "state": self._read_debug_json(run_dir / "state.json"),
            "llm_calls": llm_calls, "tool_calls": tool_calls,
            "events": events, "stage_results": stage_results,
        }

    @staticmethod
    def _read_debug_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    @classmethod
    def _debug_stage_results(cls, run_dir: Path) -> list[dict[str, Any]]:
        stage_defs = [
            ("parse_documents", "MinerU 文档解析", "01_parse_documents.json"),
            ("quality_check", "解析质量检查", "02_quality_check.json"),
            ("structure_profile", "规则生成结构", "03_structure_profile.json"),
            ("build_logical_units", "逻辑单元重建", "04_build_logical_units.json"),
            ("assemble_review_batches", "Review Batch 与校验", "05_assemble_review_batches.json"),
            ("extract_candidates", "候选提取·分批结果", "06_extract_candidates.json"),
            ("build_ledger", "三层采购台账", "07_build_ledger.json"),
            ("build_scene_view", "采购主题视图", "08_build_scene_view.json"),
            ("global_validation", "文件全局检查", "09_global_validation.json"),
            ("derive_legal_facts", "任务事实推导", "10_derive_legal_facts.json"),
            ("match_rules", "执行规则匹配", "11_match_rules.json"),
            ("match_legal_applicability", "适用法规判断", "12_match_legal_applicability.json"),
            ("build_compliance_matrix", "逐主题合规核验矩阵", "13_build_compliance_matrix.json"),
            ("agent_review", "Agent 审查结论", "14_agent_review.json"),
            ("validate_evidence", "证据校验", "15_validate_evidence.json"),
            ("final_report", "最终合并结果", "16_final_report.json"),
        ]
        results = []
        for key, title, filename in stage_defs:
            path = run_dir / "artifacts" / filename
            live_extraction = key == "extract_candidates" and any(
                (run_dir / "batch_artifacts" / "extract_candidates").glob("*.json")
            )
            if not path.is_file() and not live_extraction:
                continue
            data = cls._read_debug_json(path) if path.is_file() else {"status": "running"}
            kind = "ai" if key in {"structure_profile", "extract_candidates", "build_compliance_matrix", "agent_review"} else "deterministic"
            item = {"key": key, "title": title, "kind": kind, "data": data}
            if key == "extract_candidates":
                batches = []
                for batch_path in sorted((run_dir / "batch_artifacts" / "extract_candidates").glob("*.json")):
                    batch = cls._read_debug_json(batch_path)
                    accepted = batch.get("accepted", [])
                    batches.append({
                        "file": batch_path.name,
                        "batch_no": batch.get("batch_no"),
                        "status": batch.get("status"),
                        "primary_block_count": batch.get("primary_block_count"),
                        "candidate_estimate": batch.get("candidate_estimate"),
                        "table_row_count": batch.get("table_row_count"),
                        "request_tokens": batch.get("request_tokens"),
                        "output_characters": batch.get("output_characters"),
                        "accepted_count": len(accepted),
                        "evidence_pending_count": sum(item.get("evidence_status") != "verified" for item in accepted),
                        "rejected_count": len(batch.get("rejected", [])),
                        "accepted": accepted,
                        "rejected": batch.get("rejected", []),
                    })
                item["batches"] = batches
            results.append(item)
        # 将持久化的引擎产物映射到界面展示的业务流程。
        # 在引擎开始分别持久化之前，多个命名步骤会有意共用同一个产物。
        by_key = {item["key"]: item for item in results}
        process = [
            ("parse_documents", "mineru_parse"),
            ("quality_check", "quality_check"),
            ("structure_profile", "deterministic_structure"),
            ("structure_profile", "semantic_structure"),
            ("build_logical_units", "logical_units"),
            ("assemble_review_batches", "review_batches"),
            ("extract_candidates", "business_understanding"),
            ("build_ledger", "global_ledger"),
            ("match_rules", "legal_candidates"),
            ("build_compliance_matrix", "compliance_matrix"),
            ("agent_review", "professional_review"),
            ("validate_evidence", "evidence_validation"),
        ]
        labels = {
            "mineru_parse": "MinerU 文档解析", "quality_check": "解析质量检查",
            "deterministic_structure": "确定性结构识别", "semantic_structure": "疑难结构语义理解",
            "logical_units": "逻辑单元重建", "review_batches": "Review Batch 与校验", "business_understanding": "大模型分批业务理解",
            "global_ledger": "全局归并与采购台账",
            "legal_candidates": "规则与全量法规上下文装载",
            "compliance_matrix": "法规义务—采购事实—差异核验",
            "professional_review": "采购文件专业审查",
            "evidence_validation": "独立证据校验",
        }
        aliases = {"build_scene_view": (run_dir / "artifacts" / "08_build_scene_view.json"), "global_validation": (run_dir / "artifacts" / "09_global_validation.json")}
        mapped = []
        for source, key in process:
            item = by_key.get(source)
            if item is None and source in aliases and aliases[source].is_file():
                item = {"key": source, "kind": "deterministic", "data": cls._read_debug_json(aliases[source])}
            if item is None:
                continue
            llm_step = {
                "semantic_structure": "structure_profile",
                "business_understanding": "extract_candidates",
                "compliance_matrix": "compliance_matrix",
                "professional_review": "agent_review",
            }.get(key)
            if llm_step:
                item["llm_calls"] = [call for call in cls._debug_llm_calls(run_dir) if call.get("step") == llm_step]
            if key == "review_batches":
                batch_data = cls._read_debug_json(run_dir / "batch_artifacts" / "review_batches" / "procurement.json")
                item["batches"] = batch_data.get("batches", [])
                item["validation"] = batch_data.get("validation", item.get("data", {}).get("validations", {}).get("procurement", {}))
            if key == "professional_review":
                item["tools"] = cls._professional_review_tools(run_dir)
            mapped.append({**item, "key": key, "title": labels[key], "kind": "ai" if key in {"semantic_structure", "business_understanding", "compliance_matrix", "professional_review"} else "deterministic"})
        return mapped

    @classmethod
    def _debug_llm_calls(cls, run_dir: Path) -> list[dict[str, Any]]:
        calls = []
        for path in sorted((run_dir / "llm_traces").glob("*.json")):
            trace = cls._read_debug_json(path)
            calls.append({"id": path.stem, "step": trace.get("step", path.stem.rsplit("_", 1)[0]), **trace})
        return calls

    @classmethod
    def _professional_review_tools(cls, run_dir: Path) -> list[dict[str, Any]]:
        review = cls._read_debug_json(run_dir / "artifacts" / "14_agent_review.json")
        tool_results = review.get("tool_results", {})
        items = [
            {"key": key, "title": title, "triggered": key in tool_results, "data": tool_results.get(key, {})}
            for key, title in (
                ("rule_coverage", "规则覆盖 Tool"),
                ("required_elements", "必备要素 Tool"),
                ("section_conflicts", "跨章节冲突 Tool"),
                ("follow_up", "疑点补查 Tool"),
            )
        ]
        if tool_results:
            return items
        checks = cls._read_debug_json(run_dir / "artifacts" / "09_global_validation.json")
        rules = cls._read_debug_json(run_dir / "artifacts" / "11_match_rules.json")
        if rules: items.append({"key": "rule_coverage", "title": "规则覆盖 Tool", "triggered": True, "data": rules})
        if checks: items.append({"key": "global_check", "title": "文件全局检查 Tool", "triggered": True, "data": checks})
        items.append({"key": "follow_up", "title": "疑点补查 Tool", "triggered": False, "data": {"reason": "历史运行未保存疑点补查结果。"}})
        return items

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
        document = {"id": target.parent.name, "file_name": file.filename, "content_type": content_type, "size": size, "sha256": digest, "path": str(target), "version": 1, "uploaded_by": user["id"], "uploaded_at": now()}
        task["document"] = document; task.setdefault("document_versions", []).append(document); task["version"] += 1; task["updated_at"] = now(); self.tasks.write(data); self._audit(user, "document_uploaded", task_id); return self._task_out(task, user)

    def upload_rectification(self, project_id: str, task_id: str, file: UploadFile, user: dict) -> dict:
        """正式首次审查后追加不可变的整改版本。"""
        task, data = self._task(project_id, task_id); self._operator(task, user)
        if task.get("final_baseline"): fail(409, "采购文件终版已锁定")
        if task["status"] != "completed": fail(409, "仅已完成正式复核的任务可上传整改版")
        suffix = Path(file.filename or "").suffix.lower(); content_type = file.content_type or "application/octet-stream"
        if suffix not in ALLOWED_TYPES or content_type not in ALLOWED_TYPES[suffix]: fail(400, "文件扩展名或 MIME 类型不支持")
        version = len(task.get("document_versions") or ([task["document"]] if task.get("document") else [])) + 1
        target = Path(get_settings().uploads_dir) / project_id / task_id / uid("doc") / (file.filename or "document")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as out: shutil.copyfileobj(file.file, out)
        size = target.stat().st_size; head = target.read_bytes()[:8]
        if not size or size > get_settings().max_upload_bytes: target.unlink(); fail(413, "文件为空或超过大小限制")
        if (suffix == ".pdf" and not head.startswith(b"%PDF-")) or (suffix == ".docx" and not head.startswith(b"PK")) or (suffix == ".doc" and not head.startswith(b"\xd0\xcf\x11\xe0")): target.unlink(); fail(400, "文件头与扩展名不匹配")
        with target.open("rb") as stream: digest = hashlib.file_digest(stream, "sha256").hexdigest()
        document = {"id": target.parent.name, "file_name": file.filename, "content_type": content_type, "size": size, "sha256": digest, "path": str(target), "version": version, "uploaded_by": user["id"], "uploaded_at": now()}
        task.setdefault("document_versions", [task["document"]] if task.get("document") else []).append(document)
        task["document"] = document; before = task["status"]; task["status"] = "rectification_draft"; task["engine_run_id"] = None; task["progress"] = 0; task["version"] += 1; task["updated_at"] = now()
        self.tasks.write(data); self._event(task, user, before, "rectification_draft", "已上传采购文件整改版"); self._audit(user, "rectification_uploaded", task_id, {"document_version": version, "sha256": digest}); return self._task_out(task, user)

    def start(self, project_id: str, task_id: str, user: dict, key: str | None) -> dict:
        task, data = self._task(project_id, task_id); self._operator(task, user); replay = self._replay(key)
        if replay: return replay
        if not task["document"]: fail(400, "请先上传采购文件")
        if task["status"] not in {"draft", "rectification_draft", "failed"}: fail(409, "任务已启动")
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
            target=self._run_review_workflow_with_heartbeat,
            args=(project_id, task_id, task["document"]["path"], previous_run_id, task_context),
            daemon=True,
        )
        thread.start()
        return outcome

    def _run_review_workflow_with_heartbeat(self, project_id: str, task_id: str, doc_path: str, engine_run_id: str | None, task_context: dict) -> None:
        """工作进程执行耗时的解析器或 LLM 调用时，持续写入可靠的心跳。"""
        stopped = threading.Event()

        def heartbeat() -> None:
            while not stopped.wait(30):
                self._touch_review_task(project_id, task_id)

        monitor = threading.Thread(target=heartbeat, daemon=True)
        monitor.start()
        try:
            confirmations = self.tasks.read()["items"]
            task = next((item for item in confirmations if item["id"] == task_id), {})
            run_review_workflow(project_id, task_id, doc_path, self._store_review_results, self._fail_review_task, self._update_review_progress, engine_run_id, task_context, self._pause_for_legal_applicability, task.get("legal_applicability_confirmations"))
        finally:
            stopped.set()

    def _pause_for_legal_applicability(self, project_id: str, task_id: str, gate: dict) -> None:
        def persist(state: dict) -> None:
            task = next((item for item in state["tasks"] if item["id"] == task_id and item["project_id"] == project_id), None)
            if not task: return
            before = task["status"]
            task["status"] = "applicability_review"; task["progress"] = 77; task["engine_run_id"] = gate.get("engine_run_id")
            task["legal_facts"] = gate.get("task_legal_facts", {}); task["legal_applicability"] = gate.get("decisions", [])
            task["legal_context_freeze"] = gate.get("candidate_frozen_context", gate.get("frozen_context", [])); task["legal_applicability_confirmations"] = {}
            task["updated_at"] = now(); task["version"] += 1
            state["events"].append({"id": uid("evt"), "task_id": task_id, "actor_id": 0, "at": now(), "before_status": before, "after_status": "applicability_review", "reason": "适用法规匹配完成，等待审查前人工确认"})
        self.repository.transaction(persist)

    def _touch_review_task(self, project_id: str, task_id: str) -> None:
        def persist(state: dict) -> None:
            task = next((t for t in state["tasks"] if t["id"] == task_id and t["project_id"] == project_id), None)
            if task and task.get("status") in {"queued", "parsing", "reviewing"}:
                task["updated_at"] = now()
        self.repository.transaction(persist)

    def _expire_stalled_tasks(self) -> None:
        timeout = get_settings().review_task_timeout_seconds
        cutoff = datetime.now(UTC).timestamp() - timeout

        def stale(task: dict) -> bool:
            if task.get("status") not in {"queued", "parsing", "reviewing"}:
                return False
            try:
                updated = datetime.fromisoformat(task["updated_at"].replace("Z", "+00:00")).timestamp()
            except (KeyError, TypeError, ValueError):
                updated = 0
            return updated < cutoff

        # 绝大多数请求没有超时任务；先只读判断，避免每次页面加载都触发全量读改写。
        if not any(stale(t) for t in self.tasks.read()["items"]):
            return

        def persist(state: dict) -> None:
            for task in state["tasks"]:
                if not stale(task):
                    continue
                task["status"] = "failed"
                task["error"] = "审查任务心跳超时，可能因服务重启或后台进程中断。请重新发起审查。"
                task["updated_at"] = now()
                task["version"] = task.get("version", 0) + 1
                state["audit"].append({"id": uid("aud"), "actor_id": "system", "action": "review_failed", "target_id": task["id"], "at": now(), "details": {"error": task["error"]}})

        self.repository.transaction(persist)

    def _store_review_results(self, project_id: str, task_id: str, report: dict) -> None:
        def persist(state: dict) -> None:
            task = next((t for t in state["tasks"] if t["id"] == task_id), None)
            if not task:
                return
            document_version = int(task.get("document", {}).get("version") or 1)
            for finding in report.get("findings", []):
                evidence = finding.get("evidence") or []
                first = evidence[0] if evidence else {}
                source_info = {
                    "page": first.get("page_no"),
                    "section_path": first.get("heading_path", []),
                    "quote": first.get("quote") or finding.get("description", ""),
                    "block_id": ",".join(finding.get("evidence_block_ids", [])[:5]),
                    "bbox": first.get("bbox"),
                }
                source_refs = [{"page": item.get("page_no"), "section_path": item.get("heading_path", []), "quote": item.get("quote") or finding.get("description", ""), "block_id": item.get("block_id"), "bbox": item.get("bbox")} for item in evidence[:10]]
                legal_refs = [{"legal_unit_id": u.get("legal_unit_id"), "document_title": u.get("document_title"), "article_no": u.get("article_no"), "quote": u.get("quote") or u.get("text"), "page": u.get("page") or u.get("page_no")} for u in finding.get("legal_evidence", [])[:10]]
                rule_refs = [{"id": rule_id} for rule_id in finding.get("rule_ids", []) if rule_id]
                risk_level = finding.get("risk_level", "pending")
                if risk_level not in {"high", "medium", "low", "pending", "unknown"}:
                    risk_level = "pending"
                applicability_status = finding.get("legal_applicability") or finding.get("legal_applicability_status")
                is_applicability_gate = applicability_status in {"potential", "insufficient_facts", "not_applicable"} or finding.get("title") == "法规适用性待确认"
                if is_applicability_gate:
                    continue
                state["findings"].append({
                    "id": uid("fdg"), "task_id": task_id, "source_type": "ai",
                    "risk_level": risk_level,
                    "title": finding.get("title", "审查发现"),
                    "description": finding.get("description", ""),
                    "suggestion": finding.get("recommendation", ""),
                    "source": source_info, "sources": source_refs, "rule_refs": rule_refs, "legal_refs": legal_refs,
                    "finding_type": finding.get("finding_type"), "review_scope": "applicability_gate" if is_applicability_gate else "finding",
                    "evidence_status": finding.get("evidence_status", "evidence_insufficient"), "evidence_validation": finding.get("evidence_validation", {}),
                    "document_version": document_version, "rectification_status": "new" if document_version > 1 else None, "version": 1,
                })
            if document_version > 1:
                current = [item for item in state["findings"] if item["task_id"] == task_id and item.get("document_version") == document_version]
                previous = [item for item in state["findings"] if item["task_id"] == task_id and int(item.get("document_version") or 1) < document_version and item.get("primary_decision", {}).get("decision") in {"receive", "adjust"}]
                for old in previous:
                    best = max((SequenceMatcher(None, old.get("description", ""), item.get("description", "")).ratio() for item in current), default=0.0)
                    old["rectification_status"] = "still_present" if best >= 0.35 else "resolved_candidate"
                    old["rectification_version"] = document_version
            task["status"] = "operator_review"; task["progress"] = 100; task["updated_at"] = now(); task["version"] += 1
            task["engine_run_id"] = report.get("engine_run_id", task.get("engine_run_id"))
            task.pop("error", None)
            task["quality"] = report.get("quality", {"status": "unknown"})
            task["legal_facts"] = report.get("task_legal_facts", {})
            task["legal_applicability"] = report.get("legal_applicability", [])
            task["legal_context_freeze"] = report.get("legal_context_freeze", [])
            task["pipeline_status"] = report.get("pipeline_status", "completed")
            task["degraded_steps"] = report.get("degraded_steps", [])
            task["system_warnings"] = report.get("system_warnings", [])
            task["coverage_matrix"] = report.get("coverage_matrix", [])
            task.setdefault("legal_applicability_confirmations", {})
            state["audit"].append({"id": uid("aud"), "actor_id": "system", "action": "review_completed", "target_id": task_id, "at": now(), "details": {}})
        self.repository.transaction(persist)

    def _fail_review_task(self, project_id: str, task_id: str, error_msg: str) -> None:
        def persist(state: dict) -> None:
            task = next((t for t in state["tasks"] if t["id"] == task_id), None)
            if not task: return
            task["status"] = "failed"; task["error"] = error_msg; task["updated_at"] = now(); task["version"] += 1
            state["audit"].append({"id": uid("aud"), "actor_id": "system", "action": "review_failed", "target_id": task_id, "at": now(), "details": {"error": error_msg}})
        self.repository.transaction(persist)

    def _update_review_progress(
        self, project_id: str, task_id: str, run_id: str, step: str | None,
        completed: int, total: int, batch_completed: int = 0, batch_total: int = 0,
    ) -> None:
        def persist(state: dict) -> None:
            task = next((t for t in state["tasks"] if t["id"] == task_id and t["project_id"] == project_id), None)
            if not task:
                return
            task["engine_run_id"] = run_id
            stage_progress = 5 + completed * 90 // total
            if batch_total > 0:
                next_stage_progress = 5 + (completed + 1) * 90 // total
                stage_progress += (next_stage_progress - stage_progress) * min(batch_completed, batch_total) / batch_total
                task["batch_completed"] = min(batch_completed, batch_total)
                task["batch_total"] = batch_total
            else:
                task.pop("batch_completed", None)
                task.pop("batch_total", None)
            task["progress"] = round(max(task.get("progress", 5), stage_progress), 1)
            if step:
                task["progress_step"] = step
            task["updated_at"] = now()
            task["version"] += 1
            if step and batch_total == 0:
                state["events"].append({"id": uid("evt"), "task_id": task_id, "actor_id": 0, "at": now(), "before_status": task["status"], "after_status": task["status"], "reason": f"审查引擎阶段完成：{step}"})
        self.repository.transaction(persist)

    def _audit_impl(self, task_id: str, user_id: str, action: str, target_id: str, details: dict | None = None) -> None:
        data = self.audit.read()
        data["items"].append({"id": uid("aud"), "actor_id": user_id, "action": action, "target_id": target_id, "at": now(), "details": details or {}})
        self.audit.write(data)

    def findings_for_task(self, project_id: str, task_id: str, user: dict) -> list[dict]:
        self.get_task(project_id, task_id, user); return [self._finding_out(x) for x in self.findings.read()["items"] if x["task_id"] == task_id and self._is_actionable_finding(x)]
    def confirm_legal_applicability(self, project_id: str, task_id: str, document_key: str, payload: dict, user: dict) -> dict:
        task, data = self._task(project_id, task_id); self._operator(task, user); self._writable(task)
        if task["status"] != "applicability_review": fail(409, "仅审查前法规适用性确认阶段可操作")
        self._version(task, payload["version"])
        decisions = task.get("legal_applicability", [])
        if not any(item.get("document_key") == document_key for item in decisions): fail(404, "法规匹配记录不存在")
        confirmation = {"decision": payload["decision"], "comment": payload.get("comment"), "by": user["id"], "by_name": user.get("display_name") or user.get("username"), "at": now()}
        task.setdefault("legal_applicability_confirmations", {})[document_key] = confirmation
        task["version"] += 1; task["updated_at"] = now(); self.tasks.write(data)
        self._audit(user, "legal_applicability_confirmed", task["id"], {"document_key": document_key, **confirmation})
        required = [item["document_key"] for item in decisions if item.get("status") in {"applicable", "potential", "insufficient_facts"}]
        confirmations = task["legal_applicability_confirmations"]
        if required and all(key in confirmations for key in required) and not any(confirmations[key]["decision"] == "needs_more_facts" for key in required):
            before = task["status"]; task["status"] = "reviewing"; task["updated_at"] = now(); task["version"] += 1; self.tasks.write(data)
            self._event(task, user, before, "reviewing", "审查前法规适用性确认完成，恢复专业审查")
            project, _ = self._project_row(project_id)
            context = {"project": {"name": project.get("name"), "project_code": project.get("project_code")}, "title": task.get("title")}
            threading.Thread(target=self._run_review_workflow_with_heartbeat, args=(project_id, task_id, task["document"]["path"], task.get("engine_run_id"), context), daemon=True).start()
        return self._task_out(task, user)
    def operator_disposition(self, project_id: str, task_id: str, finding_id: str, payload: dict, user: dict) -> dict:
        task, _ = self._task(project_id, task_id); self._operator(task, user); self._writable(task)
        if task["status"] != "operator_review": fail(409, "当前不接受经办处置")
        if payload["action"] != "accept" and not payload.get("comment"): fail(422, "非采纳处置必须填写 comment")
        finding, data = self._finding(task_id, finding_id); self._version(finding, payload["version"]); finding["operator_disposition"] = {"action": payload["action"], "comment": payload.get("comment"), "by": user["id"], "at": now()}; finding["version"] += 1; self.findings.write(data); self._audit(user, "operator_disposition", finding_id); return finding
    def operator_submit(self, project_id: str, task_id: str, user: dict, key: str | None) -> dict:
        task, data = self._task(project_id, task_id); self._operator(task, user); replay = self._replay(key)
        if replay: return replay
        if task["status"] != "operator_review": fail(409, "当前不能提交复核")
        if any(not x.get("operator_disposition") for x in self.findings.read()["items"] if x["task_id"] == task_id and self._is_actionable_finding(x)): fail(400, "仍有候选问题未处置")
        self._transition(task, user, "primary_review", "经办已提交复核"); self.tasks.write(data); return self._remember(key, task)
    def primary_decision(self, project_id: str, task_id: str, finding_id: str, payload: dict, user: dict) -> dict:
        task, _ = self._task(project_id, task_id); self._primary(task, user); self._writable(task)
        if task["status"] not in {"primary_review", "primary_recheck"}: fail(409, "当前不接受主责复核")
        finding, data = self._finding(task_id, finding_id); self._version(finding, payload["version"]); finding["primary_decision"] = {**payload, "by": user["id"], "at": now()}; finding.pop("recheck_required", None); finding["version"] += 1; self.findings.write(data); self._audit(user, "primary_decision", finding_id); return finding
    def primary_confirm(self, project_id: str, task_id: str, user: dict, key: str | None) -> dict:
        task, data = self._task(project_id, task_id); self._primary(task, user); replay = self._replay(key)
        if replay: return replay
        if task["status"] not in {"primary_review", "primary_recheck"}: fail(409, "当前不能确认")
        rows = [x for x in self.findings.read()["items"] if x["task_id"] == task_id and self._is_actionable_finding(x)]
        if any(not x.get("primary_decision") or x.get("recheck_required") for x in rows): fail(400, "仍有问题未完成主责复核")
        self._transition(task, user, "completed", "主责确认正式复核结果"); self.tasks.write(data); return self._remember(key, task)

    def lock_final(self, project_id: str, task_id: str, user: dict) -> dict:
        task, data = self._task(project_id, task_id); self._primary(task, user)
        if task["status"] != "completed": fail(409, "仅已完成复核的任务可锁定终版")
        version = int(task.get("document", {}).get("version") or 1)
        unresolved = [item for item in self.findings.read()["items"] if item["task_id"] == task_id and self._is_actionable_finding(item) and int(item.get("document_version") or 1) == version and item.get("primary_decision", {}).get("decision") in {"receive", "adjust"}]
        if unresolved: fail(409, "当前版本仍有正式问题，不能锁定终版")
        task["final_baseline"] = {"document_id": task["document"]["id"], "document_version": version, "sha256": task["document"]["sha256"], "engine_run_id": task.get("engine_run_id"), "locked_by": user["id"], "locked_at": now()}
        self._transition(task, user, "final_locked", "问题清零并锁定采购文件终版"); self.tasks.write(data); self._audit(user, "procurement_final_locked", task_id, task["final_baseline"]); return self._task_out(task, user)
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
        if task["status"] in {"completed", "final_locked"}: fail(409, "已完成任务只读")
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
        risk_level = finding.get("risk_level", "pending")
        if risk_level not in {"high", "medium", "low", "pending", "unknown"}: risk_level = "pending"
        return {**finding, "risk_level": risk_level, "sources": finding.get("sources", []), "finding_type": finding.get("finding_type"), "review_scope": finding.get("review_scope", "finding"), "rule_refs": finding.get("rule_refs", []), "legal_refs": finding.get("legal_refs", []), "recheck_required": finding.get("recheck_required", False), "collaborative_comments": [x for x in self.comments.read()["items"] if x["finding_id"] == finding["id"]]}
    @staticmethod
    def _is_actionable_finding(finding: dict) -> bool:
        if finding.get("review_scope") == "applicability_gate": return False
        return not (not finding.get("legal_refs") and finding.get("title") in {"法规适用性待确认", "适用法律体系待确认"})
    def _task_out(self, task: dict, user: dict) -> dict:
        role = review_policy.task_member(task, user)
        rows = [x for x in self.findings.read()["items"] if x["task_id"] == task["id"] and self._is_actionable_finding(x)]
        summary = {"total": len(rows), "high": sum(x.get("risk_level") == "high" for x in rows), "medium": sum(x.get("risk_level") == "medium" for x in rows), "low": sum(x.get("risk_level") == "low" for x in rows), "pending": sum(x.get("risk_level") == "pending" for x in rows), "unknown": sum(x.get("risk_level") == "unknown" for x in rows)}
        document = task.get("document")
        if document: document = {k: v for k, v in document.items() if k != "path"}
        document_versions = [{k: v for k, v in item.items() if k != "path"} for item in task.get("document_versions", [])]
        output = {key: value for key, value in task.items() if key != "execution" + "_mode"}
        # 未生成报告时列值为 None（JSON 时代键不存在），统一回落默认，避免响应校验报错。
        for key, default in (("quality", {"status": "pending"}), ("system_warnings", []), ("coverage_matrix", []),
                             ("legal_facts", {}), ("legal_applicability", []), ("legal_context_freeze", []),
                             ("degraded_steps", []), ("legal_applicability_confirmations", {})):
            value = task.get(key)
            output[key] = value if value is not None else default
        return {**output, "document": document, "document_versions": document_versions, "finding_summary": summary, "progress": task.get("progress", 0), "task_role": role["task_role"] if role else None, "module_scope": role["module_scope"] if role else [], "error": task.get("error")}
    def _project_out(self, project: dict, user: dict, tasks: list[dict]) -> dict:
        return {**project, "task_summaries": [self._task_out(x, user) for x in tasks]}
