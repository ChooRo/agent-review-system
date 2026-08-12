"""Repository boundary for legal evidence documents; executable rules stay separate."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, TypeVar

from app.repositories.json_store import JsonStore
from app.repositories.rule_repository import RuleRepository

T = TypeVar("T")


class KnowledgeRepository:
    def __init__(self, root: Path, rules_data_dir: Path | None = None) -> None:
        self.root = root
        self.rules_data_dir = rules_data_dir

    @staticmethod
    def _metadata(value: dict[str, Any], fallback_key: str = "") -> dict[str, Any]:
        """Provide backward-compatible document metadata without rewriting old files."""
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

    def list_documents(self, keyword: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        items = []
        for path in self.root.glob("*/legal_knowledge.json") if self.root.is_dir() else []:
            item = self.document_item(self._read(path), path.parent.name)
            if keyword and keyword.lower() not in item["title"].lower():
                continue
            if status and item["status"] != status:
                continue
            items.append(item)
        return items

    @classmethod
    def document_item(cls, value: dict[str, Any], fallback_key: str = "") -> dict[str, Any]:
        doc = cls._metadata(value, fallback_key)
        quality = value.get("quality", {})
        extraction = value.get("metadata_extraction", {})
        applicability = doc.get("applicability") or extraction.get("applicability") or {}
        return {
            "document_key": doc["document_key"],
            "title": doc["title"],
            "canonical_title": doc.get("canonical_title"),
            "issuer": doc.get("issuer"),
            "effective_date": doc.get("effective_date"),
            "expiry_date": doc["expiry_date"],
            "status": doc["status"],
            "document_version": doc["document_version"],
            "department": doc["department"],
            "applicable_scope": doc["applicable_scope"],
            "metadata_version": doc["metadata_version"],
            "updated_at": doc["updated_at"],
            "updated_by": doc["updated_by"],
            "summary": applicability.get("summary"),
            "unit_count": len(value.get("units", [])),
            "article_count": quality.get("article_count", 0),
            "quality_status": quality.get("status"),
            "extraction_status": extraction.get("status"),
        }

    def _path_and_value(self, key: str) -> tuple[Path, dict[str, Any]] | None:
        for path in self.root.glob("*/legal_knowledge.json") if self.root.is_dir() else []:
            value = self._read(path)
            if key in {path.parent.name, value.get("legal_document", {}).get("document_key")}:
                return path, value
        return None

    def get_document(self, key: str) -> dict[str, Any] | None:
        found = self._path_and_value(key)
        if not found:
            return None
        path, value = found
        return {**value, "legal_document": self._metadata(value, path.parent.name)}

    def update_document(self, key: str, mutate: Callable[[dict[str, Any]], T]) -> T | None:
        found = self._path_and_value(key)
        if not found:
            return None
        path, _ = found
        store = JsonStore(path)
        with JsonStore._lock:
            value = store.read()
            result = mutate(value)
            store.write(value)
            return result

    def applicable_rules(self, keyword: str | None = None) -> list[dict[str, Any]]:
        """Return only published executable rules; legal units remain evidence, not rules."""
        if self.rules_data_dir is None:
            return []
        rules = RuleRepository(self.rules_data_dir).applicable_rules("procurement")
        if not keyword:
            return rules
        term = keyword.lower()
        return [rule for rule in rules if term in " ".join([rule["title"], rule["description"], *rule.get("tags", [])]).lower()]

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))
