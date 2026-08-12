from __future__ import annotations

import os
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from app.core.config import get_settings
from app.policies import knowledge as knowledge_policy
from app.repositories.json_store import JsonStore
from app.repositories.knowledge_repository import KnowledgeRepository
from app.review_engine.services.legal_knowledge import ingest_legal_document
from app.review_engine.services.legal_knowledge import check_legal_quality
from app.review_engine.services.legal_metadata import extract_applicability, prepare_metadata_extraction
from app.review_engine.services.llm import LLMService
from app.review_engine.services.mineru import MinerUService
from app.review_engine.services.runtime import RunStore
from app.review_engine.settings import load_settings as load_review_settings
from app.services.procurement_review import ALLOWED_TYPES


class KnowledgeService:
    def __init__(self) -> None:
        self.repository = KnowledgeRepository(Path(__file__).resolve().parents[3] / "knowledge" / "rules", Path(get_settings().data_dir))

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def list_documents(self, keyword: str | None, status: str | None, user: dict) -> list[dict]:
        return self.repository.list_documents(keyword, knowledge_policy.visible_document_status(user, status))

    def detail(self, key: str, user: dict) -> dict:
        value = self.repository.get_document(key)
        if not value or not knowledge_policy.can_view_knowledge_document(user, value["legal_document"]):
            raise HTTPException(404, "legal knowledge document not found")
        return value

    def rules(self, keyword: str | None, _user: dict) -> list[dict]:
        return self.repository.applicable_rules(keyword)

    def update(self, key: str, payload: dict[str, Any], user: dict) -> dict:
        requested_version = payload.pop("metadata_version")
        requested = {name: value for name, value in payload.items() if value is not None}

        def mutate(value: dict[str, Any]) -> dict:
            doc = KnowledgeRepository._metadata(value, key)
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
        found = self.repository._path_and_value(key)
        if not found:
            raise HTTPException(404, "legal knowledge document not found")
        path, knowledge = found
        document_path = path.parent / "document.json"
        document = JsonStore(document_path).read() if document_path.is_file() else {"blocks": []}
        # Rebuild local candidates on every explicit run. This also recovers a
        # persisted `processing` state left by a stopped server or browser.
        extraction = prepare_metadata_extraction(knowledge, document)
        candidate_ids = set(extraction.get("candidate_unit_ids", []))
        candidates = [unit for unit in knowledge.get("units", []) if unit.get("legal_unit_id") in candidate_ids]
        config_path = Path(__file__).resolve().parents[2] / "review_config.json"
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
            llm = LLMService(metadata_config, RunStore(path.parent / "metadata_extraction"))
            applicability, warnings = extract_applicability(llm, candidates)
        except Exception as exc:
            def fail(value: dict[str, Any]) -> None:
                current = value.setdefault("metadata_extraction", extraction)
                current.update({"status": "failed", "updated_at": self._now()})
                current.setdefault("warnings", []).append({"code": "AI_EXTRACTION_FAILED", "message": f"{type(exc).__name__}: {exc}"})
            self.repository.update_document(key, fail)
            return self.detail(key, user)

        def complete(value: dict[str, Any]) -> None:
            doc = KnowledgeRepository._metadata(value, key)
            doc.update({"metadata_version": doc["metadata_version"] + 1, "updated_at": self._now(), "updated_by": user["id"]})
            current = value.setdefault("metadata_extraction", extraction)
            current.update({"status": "ready", "applicability": applicability, "warnings": warnings, "updated_at": self._now()})
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
            try:
                knowledge = ingest_legal_document(source, output_dir, MinerUService(settings.mineru_api_url, timeout_seconds=settings.mineru_timeout_seconds), metadata)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(502, f"legal document parsing failed: {type(exc).__name__}") from exc
            document_key = str(knowledge.get("legal_document", {}).get("document_key") or "")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", document_key):
                raise HTTPException(422, "parser did not produce a safe document_key")
            if not (output_dir / "document.json").is_file() or not (output_dir / "legal_knowledge.json").is_file():
                raise HTTPException(422, "parser did not produce required knowledge artifacts")
            now = self._now()
            document = knowledge.setdefault("legal_document", {})
            document.update({
                "title": metadata.get("title") or document.get("title") or document_key,
                "issuer": metadata.get("issuer") or document.get("issuer"),
                "department": metadata.get("department"),
                "document_version": metadata.get("document_version") or "unknown",
                "applicable_scope": metadata.get("applicable_scope") or "",
                "effective_date": metadata.get("effective_date") or document.get("effective_date"),
                "expiry_date": metadata.get("expiry_date") or document.get("expiry_date"),
                "status": "unknown",
                "metadata_version": 1,
                "updated_at": now,
                "updated_by": user["id"],
            })
            parsed_document = JsonStore(output_dir / "document.json").read()
            storage_key = f"{document_key}/original{suffix}"
            document.update({"source_file": storage_key, "source_storage_key": storage_key})
            parsed_document["source_file"] = storage_key
            knowledge["metadata_extraction"] = prepare_metadata_extraction(knowledge, parsed_document)
            JsonStore(output_dir / "document.json").write(parsed_document)
            JsonStore(output_dir / "legal_knowledge.json").write(knowledge)
            shutil.copy2(source, output_dir / f"original{suffix}")
            final_dir = self.repository.root / document_key
            if final_dir.exists():
                raise HTTPException(409, "document_key already exists")
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            archive_stage = final_dir.parent / f".{document_key}.upload-{uuid.uuid4().hex}"
            try:
                shutil.copytree(output_dir, archive_stage)
                os.replace(archive_stage, final_dir)
            except FileExistsError as exc:
                raise HTTPException(409, "document_key already exists") from exc
            finally:
                shutil.rmtree(archive_stage, ignore_errors=True)
            return self.repository.document_item(knowledge, document_key)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
