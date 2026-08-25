from __future__ import annotations

import re
import shutil
import threading
import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from app.core.config import get_settings
from app.policies import knowledge as knowledge_policy
from app.integrations.storage.local import LocalStorage
from app.repositories.postgres.knowledge_repository import DuplicateKnowledgeUpload, PostgresKnowledgeRepository
from app.review_engine.legal.knowledge import check_legal_quality, ingest_legal_document
from app.review_engine.legal.metadata import extract_applicability, prepare_metadata_extraction
from app.integrations.llm import LLMService
from app.integrations.mineru import MinerUService
from app.review_engine.runner import RunStore
from app.review_engine.settings import load_settings as load_review_settings
from app.services.procurement.review import ALLOWED_TYPES


class KnowledgeService:
    _executor = ThreadPoolExecutor(max_workers=2)
    _tasks: dict[str, dict[str, Any]] = {}
    _tasks_lock = threading.Lock()
    _parse_retries = 3
    def __init__(self) -> None:
        settings = get_settings()
        self.repository = PostgresKnowledgeRepository(Path(settings.data_dir), LocalStorage(settings.uploads_dir))

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def list_documents(self, keyword: str | None, status: str | None, user: dict) -> list[dict]:
        return self.repository.list_documents(keyword, knowledge_policy.visible_document_status(user, status))

    def detail(self, key: str, user: dict) -> dict:
        value = self.repository.get_document(key)
        if not value or not knowledge_policy.can_view_knowledge_document(user, value["legal_document"]):
            raise HTTPException(404, "legal knowledge document not found")
        value["metadata_extraction"] = self._metadata_audit(value, value.get("metadata_extraction", {}), key)
        return value

    @staticmethod
    def _metadata_audit(value: dict[str, Any], extraction: dict[str, Any], key: str) -> dict[str, Any]:
        return {**extraction, "audit": {"parser": {"tool": "MinerUService", "input": value.get("legal_document", {}).get("source_file"), "output": "PostgreSQL legal_documents + legal_units"}, "calls": extraction.get("audit", {}).get("calls", [])}}

    def rules(self, keyword: str | None, _user: dict) -> list[dict]:
        return self.repository.applicable_rules(keyword)

    def update(self, key: str, payload: dict[str, Any], user: dict) -> dict:
        requested_version = payload.pop("metadata_version")
        requested = {name: value for name, value in payload.items() if value is not None}

        def mutate(value: dict[str, Any]) -> dict:
            doc = value["legal_document"]
            if requested_version != doc["metadata_version"]:
                raise HTTPException(409, "metadata version conflict")
            if not knowledge_policy.can_maintain_knowledge(user):
                raise HTTPException(403, "only administrators can maintain legal knowledge")
            if not requested:
                raise HTTPException(422, "no editable metadata supplied")
            target_status = requested.get("status")
            extraction = value.get("metadata_extraction", {})
            if doc["status"] == "unknown" and target_status == "effective" and extraction.get("status") != "ready":
                raise HTTPException(409, "metadata extraction must be ready before publication")
            if target_status and (doc["status"], target_status) not in {("unknown", "effective"), ("effective", "repealed")}:
                raise HTTPException(409, "invalid document status transition")
            if target_status == "effective":
                basic = extraction.get("basic_information", {})
                doc.update({name: field for name, field in basic.items() if field is not None})
                if basic.get("canonical_title"):
                    doc["title"] = basic["canonical_title"]
                doc["applicability"] = extraction.get("applicability", {})
                doc["applicable_scope"] = "；".join(item["value"] for item in doc["applicability"].get("activities", []))
                extraction.update({"status": "confirmed", "confirmed_at": self._now(), "confirmed_by": user["id"]})
                value["metadata_extraction"] = extraction
            doc.update(requested)
            if target_status in {"effective", "repealed"}:
                unit_date = doc.get("current_version_effective_date") or doc.get("effective_date")
                for unit in value.get("units", []):
                    unit["status"] = target_status
                    unit["effective_date"] = unit_date
            value["quality"] = check_legal_quality(value.get("units", []), doc)
            history = value.setdefault("metadata_history", [])
            history.append({"metadata_version": doc["metadata_version"], "updated_at": self._now(), "updated_by": user["id"], "snapshot": dict(doc)})
            doc.update({"metadata_version": doc["metadata_version"] + 1, "updated_at": self._now(), "updated_by": user["id"]})
            value["legal_document"] = doc
            return self.repository.document_item(value, key)

        result = self.repository.update_document(key, mutate)
        if result is None:
            raise HTTPException(404, "legal knowledge document not found")
        return result

    def extract_metadata(self, key: str, user: dict) -> dict:
        if not knowledge_policy.can_maintain_knowledge(user):
            raise HTTPException(403, "only administrators can extract legal metadata")
        knowledge = self.repository.get_document(key)
        if not knowledge:
            raise HTTPException(404, "legal knowledge document not found")
        settings = get_settings()
        document = self.repository.get_source_document(key)
        # 每次明确执行时都重新构建本地候选项，同时恢复因服务器或浏览器停止而遗留的
        # 持久化 `processing` 状态。
        extraction = prepare_metadata_extraction(knowledge, document)
        candidate_ids = set(extraction.get("candidate_unit_ids", []))
        candidates = [unit for unit in knowledge.get("units", []) if unit.get("legal_unit_id") in candidate_ids]
        config_path = Path(__file__).resolve().parents[3] / "review_config.json"
        config = load_review_settings(config_path if config_path.is_file() else None).get("llm", {})
        if not all(config.get(name) for name in ("api_url", "api_key", "model")):
            extraction.update({"status": "pending_ai", "updated_at": self._now()})
            extraction.setdefault("warnings", []).append({"code": "LLM_NOT_CONFIGURED", "message": "AI metadata extraction is waiting for LLM configuration"})
            self.repository.update_document(key, lambda value: value.update({"metadata_extraction": extraction}))
            return self.detail(key, user)

        extraction.update({"status": "processing", "updated_at": self._now()})
        self.repository.update_document(key, lambda value: value.update({"metadata_extraction": extraction}))
        try:
            metadata_config = {
                **config,
                "timeout_seconds": min(float(config.get("timeout_seconds", 120)), 120),
                "max_retries": 0,
            }
            llm = LLMService(metadata_config, RunStore(Path(settings.data_dir) / "knowledge_metadata" / key))
            applicability, warnings = extract_applicability(llm, candidates)
        except Exception as exc:
            def fail(value: dict[str, Any]) -> None:
                current = value.setdefault("metadata_extraction", extraction)
                current.update({"status": "failed", "updated_at": self._now()})
                current.setdefault("warnings", []).append({"code": "AI_EXTRACTION_FAILED", "message": f"{type(exc).__name__}: {exc}"})
            self.repository.update_document(key, fail)
            return self.detail(key, user)

        def complete(value: dict[str, Any]) -> None:
            doc = value["legal_document"]
            value.setdefault("metadata_history", []).append({"metadata_version": doc["metadata_version"], "updated_at": self._now(), "updated_by": user["id"], "snapshot": dict(doc)})
            doc.update({"metadata_version": doc["metadata_version"] + 1, "updated_at": self._now(), "updated_by": user["id"]})
            current = value.setdefault("metadata_extraction", extraction)
            current.update({"status": "ready", "applicability": applicability, "warnings": warnings, "updated_at": self._now()})
            basic = current.get("basic_information", {})
            doc.update({name: field for name, field in basic.items() if field is not None})
            if basic.get("canonical_title"):
                doc["canonical_title"] = basic["canonical_title"]
                doc["title"] = basic["canonical_title"]
            value["legal_document"] = doc
        self.repository.update_document(key, complete)
        return self.detail(key, user)

    def upload(self, file: UploadFile, metadata: dict, user: dict) -> dict:
        if not knowledge_policy.can_maintain_knowledge(user):
            raise HTTPException(403, "only administrators can upload legal knowledge")
        settings = get_settings()
        suffix = Path(file.filename or "").suffix.lower()
        content_type = file.content_type or "application/octet-stream"
        if suffix not in ALLOWED_TYPES or content_type not in ALLOWED_TYPES[suffix]:
            raise HTTPException(400, "unsupported file extension or MIME type")
        data_dir = Path(settings.data_dir)
        temp_dir = data_dir / "knowledge_ingest" / uuid.uuid4().hex
        output_dir = temp_dir / "output"
        source = temp_dir / f"source{suffix}"
        task_registered = False
        try:
            temp_dir.mkdir(parents=True)
            size = 0
            with source.open("wb") as destination:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(413, "file exceeds the configured size limit")
                    destination.write(chunk)
            if not size:
                raise HTTPException(422, "file is empty")
            head = source.read_bytes()[:8]
            if (suffix == ".pdf" and not head.startswith(b"%PDF-")) or (suffix == ".docx" and not head.startswith(b"PK")) or (suffix == ".doc" and not head.startswith(b"\xd0\xcf\x11\xe0")):
                raise HTTPException(400, "file header does not match extension")
            task_id = uuid.uuid4().hex
            source_fingerprint = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
            source_filename = file.filename or source.name
            reservation_key = self.repository.reserve_upload(source_fingerprint, task_id, source_filename)
            task = {"id": task_id, "status": "queued", "progress": 0, "retry_count": 0, "max_retries": self._parse_retries, "error": None, "document_key": None}
            task.update({"_source": source, "_output_dir": output_dir, "_suffix": suffix, "_metadata": metadata, "_user": user, "_settings": settings, "_repository": self.repository, "_source_fingerprint": source_fingerprint, "_source_filename": source_filename, "_reservation_key": reservation_key})
            with self._tasks_lock:
                self._tasks[task_id] = task
                task_registered = True
            # 任务存续期间，该目录由工作进程负责管理。
            self._executor.submit(self._run_upload_task, task_id, source, output_dir, suffix, metadata, user, settings, self.repository, source_fingerprint, source_filename, reservation_key)
            return {"task_id": task_id, **(self.task(task_id) or {})}
        except DuplicateKnowledgeUpload:
            raise HTTPException(409, "legal document already exists") from None
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(502, f"legal document upload failed: {type(exc).__name__}") from exc
        finally:
            # 异步工作进程会在解析完成后清理自己的临时目录。
            if not task_registered:
                shutil.rmtree(temp_dir, ignore_errors=True)

    @classmethod
    def task(cls, task_id: str) -> dict[str, Any] | None:
        with cls._tasks_lock:
            return {key: value for key, value in cls._tasks[task_id].items() if not key.startswith("_")} if task_id in cls._tasks else None

    @classmethod
    def retry_task(cls, task_id: str) -> dict[str, Any] | None:
        with cls._tasks_lock:
            task = cls._tasks.get(task_id)
            if not task or task.get("status") != "failed" or task.get("retry_count", 0) >= cls._parse_retries:
                return None
            task.update({"status": "queued", "progress": 0, "error": None, "message": "等待手动重试", "retry_count": task.get("retry_count", 0) + 1})
            args = (task_id, task["_source"], task["_output_dir"], task["_suffix"], task["_metadata"], task["_user"], task["_settings"], task["_repository"], task["_source_fingerprint"], task["_source_filename"], task["_reservation_key"])
        cls._executor.submit(cls._run_upload_task, *args)
        return cls.task(task_id)

    @classmethod
    def _update_task(cls, task_id: str, **changes: Any) -> None:
        with cls._tasks_lock:
            if task_id in cls._tasks:
                cls._tasks[task_id].update(changes)

    @classmethod
    def _run_upload_task(cls, task_id: str, source: Path, output_dir: Path, suffix: str, metadata: dict, user: dict, settings: Any, repository: PostgresKnowledgeRepository, source_fingerprint: str, source_filename: str, reservation_key: str) -> None:
        try:
            cls._update_task(task_id, status="parsing", progress=10)
            cls._update_task(task_id, message="正在解析法规文档")
            if output_dir.exists():
                shutil.rmtree(output_dir)
            knowledge = ingest_legal_document(source, output_dir, MinerUService(settings.mineru_api_url, timeout_seconds=settings.mineru_timeout_seconds), metadata)
            cls._update_task(task_id, status="storing", progress=70)
            document_key = str(knowledge.get("legal_document", {}).get("document_key") or "")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", document_key):
                raise ValueError("parser did not produce a safe document_key")
            if not (output_dir / "document.json").is_file() or not (output_dir / "legal_knowledge.json").is_file():
                raise ValueError("parser did not produce required knowledge artifacts")
            now = cls._now()
            document = knowledge.setdefault("legal_document", {})
            document.update({"title": metadata.get("title") or document.get("title") or document_key, "issuer": metadata.get("issuer") or document.get("issuer"), "department": metadata.get("department"), "document_version": metadata.get("document_version") or "unknown", "applicable_scope": metadata.get("applicable_scope") or "", "effective_date": metadata.get("effective_date") or document.get("effective_date"), "expiry_date": metadata.get("expiry_date") or document.get("expiry_date"), "status": "unknown", "metadata_version": 1, "updated_at": now, "updated_by": user["id"]})
            parsed_document = json.loads((output_dir / "document.json").read_text(encoding="utf-8"))
            storage_key = f"legal/{document_key}/original{suffix}"
            document.update({"source_file": storage_key, "source_storage_key": storage_key}); parsed_document["source_file"] = storage_key
            knowledge["metadata_extraction"] = prepare_metadata_extraction(knowledge, parsed_document)
            if repository.get_document(document_key):
                raise ValueError("document_key already exists")
            repository.storage.upload(storage_key, source.read_bytes())
            repository.upsert_legacy(knowledge, parsed_document, storage_key, source_fingerprint=source_fingerprint, source_filename=source_filename)
            repository.release_upload_reservation(reservation_key)
            cls._update_task(task_id, status="completed", progress=100, document_key=document_key, result=repository.document_item(knowledge, document_key), error=None)
        except Exception as exc:
            repository.release_upload_reservation(reservation_key)
            cls._update_task(task_id, status="failed", progress=100, error=f"{type(exc).__name__}: {exc}")
        finally:
            if cls.task(task_id) and cls.task(task_id).get("status") == "completed":
                shutil.rmtree(source.parent, ignore_errors=True)
