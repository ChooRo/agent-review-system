"""法律知识库的 PostgreSQL 持久化边界。

法规原始文件由 LocalStorage 保存；元数据、版本、条款和解析后的结构化文档
由本仓储写入 PostgreSQL。历史 JSON 只由 backfill 脚本通过 ``upsert_legacy``
导入，运行时不会扫描 knowledge/rules 目录。
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

from psycopg.types.json import Jsonb

from app.repositories.postgres.db import lock_table, run_with_retry, transaction
from app.repositories.postgres.rule_repository import PostgresRuleRepository

T = TypeVar("T")


class DuplicateKnowledgeUpload(RuntimeError):
    """同一原始文件已经入库或正在解析。"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _metadata(value: dict[str, Any], fallback_key: str = "") -> dict[str, Any]:
    """将旧知识 JSON 的元数据整理成当前 API 契约。"""
    doc = dict(value.get("legal_document", {}))
    extracted = value.get("metadata_extraction", {}).get("basic_information", {})
    candidate_title = str(doc.get("canonical_title") or extracted.get("canonical_title") or "")
    if re.match(r"^(第?[一二三四五六七八九十百千万0-9]+[条章节]|[（(][一二三四五六七八九十百千万0-9]+[）)])", candidate_title.strip()):
        candidate_title = ""
    if not candidate_title or candidate_title == doc.get("title"):
        for unit in value.get("units", [])[:30]:
            text = str(unit.get("text") or unit.get("search_text") or "")
            match = re.search(r"根据《([^》]+)》.*制定本条例", text)
            if match:
                candidate_title = f"{match.group(1)}实施条例"
                break
    return {
        **doc,
        "document_key": doc.get("document_key") or fallback_key,
        "title": candidate_title or str(doc.get("title") or fallback_key),
        "status": doc.get("status") or "unknown",
        "document_version": doc.get("document_version") or "unknown",
        "department": doc.get("department"),
        "applicable_scope": doc.get("applicable_scope") or "",
        "expiry_date": doc.get("expiry_date"),
        "metadata_version": int(doc.get("metadata_version") or 1),
        "updated_at": doc.get("updated_at"),
        "updated_by": str(doc["updated_by"]) if doc.get("updated_by") is not None else None,
        "canonical_title": candidate_title or doc.get("title") or fallback_key,
        "legal_level": doc.get("legal_level") or "other",
        "document_number": doc.get("document_number"),
        "adoption_date": doc.get("adoption_date"),
        "original_effective_date": doc.get("original_effective_date"),
        "current_version_effective_date": doc.get("current_version_effective_date") or doc.get("effective_date"),
        "applicability": doc.get("applicability") or {},
    }


def _row_value(row: Any) -> dict[str, Any]:
    return deepcopy(dict(row["data"]))


class PostgresKnowledgeRepository:
    """法律文档、条款单元和元数据版本的当前唯一运行时仓储。"""

    _metadata = staticmethod(_metadata)

    def __init__(self, data_dir: Path | None = None, storage: Any = None) -> None:
        self.data_dir = data_dir
        self.storage = storage

    @staticmethod
    def _value_from_rows(document: dict[str, Any], units: list[dict[str, Any]]) -> dict[str, Any]:
        result = {
            "schema_version": document.get("schema_version", "1.0.0"),
            "legal_document": document["metadata"],
            "units": units,
            "quality": document.get("quality") or {},
            "metadata_extraction": document.get("metadata_extraction") or {},
            "metadata_history": document.get("metadata_history") or [],
        }
        parsed = document.get("document_json") or {}
        if isinstance(parsed, dict):
            result["document"] = parsed
        if document.get("topic_vocabulary_version"):
            result["topic_vocabulary_version"] = document["topic_vocabulary_version"]
        if document.get("content_fingerprint"):
            result["content_fingerprint"] = document["content_fingerprint"]
        return result

    @classmethod
    def document_item(cls, value: dict[str, Any], fallback_key: str = "") -> dict[str, Any]:
        doc = _metadata(value, fallback_key)
        quality = value.get("quality", {})
        extraction = value.get("metadata_extraction", {})
        applicability = doc.get("applicability") or extraction.get("applicability") or {}
        return {
            "document_key": doc["document_key"], "title": doc["title"], "canonical_title": doc.get("canonical_title"),
            "issuer": doc.get("issuer"), "effective_date": doc.get("effective_date"), "expiry_date": doc["expiry_date"],
            "status": doc["status"], "document_version": doc["document_version"], "department": doc["department"],
            "applicable_scope": doc["applicable_scope"], "metadata_version": doc["metadata_version"],
            "updated_at": doc["updated_at"], "updated_by": doc["updated_by"], "summary": applicability.get("summary"),
            "unit_count": len(value.get("units", [])), "article_count": quality.get("article_count", 0),
            "quality_status": quality.get("status"), "extraction_status": extraction.get("status"),
        }

    @staticmethod
    def _read(conn, key: str) -> dict[str, Any] | None:
        row = conn.execute("SELECT * FROM legal_documents WHERE document_key=%s", (key,)).fetchone()
        if not row:
            return None
        units = [_row_value(unit) for unit in conn.execute(
            "SELECT data FROM legal_units WHERE document_key=%s ORDER BY ordinal", (key,)
        )]
        return PostgresKnowledgeRepository._value_from_rows(dict(row), units)

    def list_knowledge(self) -> list[dict[str, Any]]:
        with transaction() as conn:
            return [self._read(conn, row["document_key"]) for row in conn.execute(
                "SELECT document_key FROM legal_documents WHERE COALESCE(metadata->>'ingest_reservation', 'false') <> 'true' ORDER BY title, document_key"
            )]

    def list_documents(self, keyword: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        items = [self.document_item(value, value["legal_document"].get("document_key", "")) for value in self.list_knowledge()]
        if keyword:
            term = keyword.lower()
            items = [item for item in items if term in item["title"].lower()]
        if status:
            items = [item for item in items if item["status"] == status]
        return items

    def get_document(self, key: str) -> dict[str, Any] | None:
        with transaction() as conn:
            value = self._read(conn, key)
        return value

    def get_source_document(self, key: str) -> dict[str, Any]:
        value = self.get_document(key)
        if not value:
            return {"blocks": []}
        return value.get("document", {"blocks": []})

    def update_document(self, key: str, mutate: Callable[[dict[str, Any]], T]) -> T | None:
        def operation() -> T | None:
            with transaction() as conn:
                lock_table(conn, f"legal-document:{key}")
                value = self._read(conn, key)
                if value is None:
                    return None
                result = mutate(value)
                self._replace(conn, key, value)
                self._replace_versions(conn, key, value.get("metadata_history", []))
                return result
        return run_with_retry(operation)

    def upsert_legacy(
        self, value: dict[str, Any], document_json: dict[str, Any] | None = None,
        source_storage_key: str | None = None, content_fingerprint: str | None = None,
        source_fingerprint: str | None = None, source_filename: str | None = None,
    ) -> None:
        """幂等导入历史 JSON 或上传解析结果；不删除任何本地文件。"""
        document = _metadata(value)
        key = document["document_key"]
        if not key:
            raise ValueError("legal document_key is required")
        value = deepcopy(value)
        value["legal_document"] = {**value.get("legal_document", {}), **document}
        if source_storage_key:
            value["legal_document"]["source_storage_key"] = source_storage_key
            value["legal_document"]["source_file"] = source_storage_key
        if source_fingerprint:
            value["legal_document"]["source_fingerprint"] = source_fingerprint
        if source_filename:
            value["legal_document"]["source_filename"] = source_filename
        if document_json is not None:
            value["document"] = document_json
        self._write(key, value, content_fingerprint)

    def legal_documents_for_review(self) -> list[dict[str, Any]]:
        return self.list_knowledge()

    def reserve_upload(self, source_fingerprint: str, task_id: str, source_filename: str) -> str:
        """在 PostgreSQL 中预留原始文件指纹，消除异步解析的重复上传竞态。"""
        reservation_key = f"__upload__{source_fingerprint.removeprefix('sha256:')}"
        with transaction() as conn:
            lock_table(conn, "legal-upload-fingerprints")
            existing = conn.execute(
                "SELECT document_key FROM legal_documents WHERE metadata->>'source_fingerprint'=%s OR metadata->>'source_filename'=%s",
                (source_fingerprint, source_filename),
            ).fetchone()
            if existing:
                raise DuplicateKnowledgeUpload("legal document already exists")
            self._replace(conn, reservation_key, {
                "schema_version": "1.0.0",
                "legal_document": {
                    "document_key": reservation_key, "title": "正在导入法规", "status": "processing",
                    "metadata_version": 1, "updated_at": _now(), "source_fingerprint": source_fingerprint,
                    "source_filename": source_filename, "ingest_reservation": True, "upload_task_id": task_id,
                },
                "units": [], "quality": {}, "metadata_extraction": {}, "metadata_history": [],
            }, source_fingerprint)
        return reservation_key

    def release_upload_reservation(self, key: str) -> None:
        with transaction() as conn:
            conn.execute(
                "DELETE FROM legal_documents WHERE document_key=%s AND metadata->>'ingest_reservation'='true'",
                (key,),
            )

    def delete_document(self, key: str) -> bool:
        with transaction() as conn:
            lock_table(conn, f"legal-document:{key}")
            deleted = conn.execute("DELETE FROM legal_documents WHERE document_key=%s", (key,)).rowcount
        return bool(deleted)

    def applicable_rules(self, keyword: str | None = None) -> list[dict[str, Any]]:
        rules = PostgresRuleRepository(self.data_dir).applicable_rules("procurement")
        if not keyword:
            return rules
        term = keyword.lower()
        return [rule for rule in rules if term in " ".join([rule["title"], rule["description"], *rule.get("tags", [])]).lower()]

    def _write(self, key: str, value: dict[str, Any], content_fingerprint: str | None = None) -> None:
        metadata = _metadata(value, key)
        history = value.get("metadata_history", [])
        raw_content = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        content_fingerprint = content_fingerprint or "sha256:" + hashlib.sha256(raw_content).hexdigest()
        with transaction() as conn:
            lock_table(conn, f"legal-document:{key}")
            self._replace(conn, key, value, content_fingerprint)
            self._replace_versions(conn, key, history)

    def _replace(self, conn: Any, key: str, value: dict[str, Any], content_fingerprint: str | None = None) -> None:
        metadata = _metadata(value, key)
        history = value.get("metadata_history", [])
        raw_content = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        fingerprint = content_fingerprint or "sha256:" + hashlib.sha256(raw_content).hexdigest()
        source_key = metadata.get("source_storage_key") or metadata.get("source_file")
        conn.execute(
            """INSERT INTO legal_documents
            (document_key, schema_version, metadata, title, canonical_title, issuer, status,
             effective_date, expiry_date, department, document_version, applicable_scope,
             metadata_version, quality, metadata_extraction, metadata_history, document_json,
             source_storage_key, content_fingerprint, topic_vocabulary_version, updated_at, updated_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (document_key) DO UPDATE SET schema_version=EXCLUDED.schema_version,
             metadata=EXCLUDED.metadata, title=EXCLUDED.title, canonical_title=EXCLUDED.canonical_title,
             issuer=EXCLUDED.issuer, status=EXCLUDED.status, effective_date=EXCLUDED.effective_date,
             expiry_date=EXCLUDED.expiry_date, department=EXCLUDED.department,
             document_version=EXCLUDED.document_version, applicable_scope=EXCLUDED.applicable_scope,
             metadata_version=EXCLUDED.metadata_version, quality=EXCLUDED.quality,
             metadata_extraction=EXCLUDED.metadata_extraction, metadata_history=EXCLUDED.metadata_history,
             document_json=EXCLUDED.document_json, source_storage_key=EXCLUDED.source_storage_key,
             content_fingerprint=EXCLUDED.content_fingerprint, topic_vocabulary_version=EXCLUDED.topic_vocabulary_version,
             updated_at=EXCLUDED.updated_at, updated_by=EXCLUDED.updated_by""",
            (
                key, value.get("schema_version", "1.0.0"), Jsonb(metadata), metadata["title"], metadata.get("canonical_title"),
                metadata.get("issuer"), metadata.get("status", "unknown"), metadata.get("effective_date"), metadata.get("expiry_date"),
                metadata.get("department"), metadata.get("document_version", "unknown"), metadata.get("applicable_scope", ""),
                metadata.get("metadata_version", 1), Jsonb(value.get("quality", {})), Jsonb(value.get("metadata_extraction", {})),
                Jsonb(history), Jsonb(value.get("document") or value.get("document_json") or {}), source_key, fingerprint,
                value.get("topic_vocabulary_version"), metadata.get("updated_at") or _now(), metadata.get("updated_by"),
            ),
        )
        conn.execute("DELETE FROM legal_units WHERE document_key=%s", (key,))
        for ordinal, unit in enumerate(value.get("units", [])):
            unit_id = unit.get("legal_unit_id")
            if not unit_id:
                continue
            conn.execute(
                "INSERT INTO legal_units (document_key, legal_unit_id, ordinal, article_no, article_index, status, effective_date, data) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (key, unit_id, ordinal, unit.get("article_no"), unit.get("article_index"), unit.get("status", metadata.get("status", "unknown")), unit.get("effective_date"), Jsonb(unit)),
            )

    @staticmethod
    def _replace_versions(conn: Any, key: str, history: list[dict[str, Any]]) -> None:
        conn.execute("DELETE FROM legal_document_versions WHERE document_key=%s", (key,))
        for index, snapshot in enumerate(history):
            version = int(snapshot.get("metadata_version") or index + 1)
            conn.execute(
                "INSERT INTO legal_document_versions (document_key, metadata_version, updated_at, updated_by, snapshot) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (document_key, metadata_version) DO UPDATE SET updated_at=EXCLUDED.updated_at, updated_by=EXCLUDED.updated_by, snapshot=EXCLUDED.snapshot",
                (key, version, snapshot.get("updated_at") or _now(), snapshot.get("updated_by"), Jsonb(snapshot.get("snapshot") or {})),
            )
