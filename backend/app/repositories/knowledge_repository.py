"""Read-only repository for versioned legal/rule knowledge assets."""

import json
from pathlib import Path
from typing import Any


class KnowledgeRepository:
    def __init__(self, root: Path) -> None:
        self.root = root

    def list_documents(self, keyword: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        items = []
        for path in self.root.glob("*/legal_knowledge.json") if self.root.is_dir() else []:
            value = self._read(path); doc = value.get("legal_document", {}); title = str(doc.get("title", path.parent.name))
            if keyword and keyword.lower() not in title.lower(): continue
            if status and doc.get("status") != status: continue
            quality = value.get("quality", {})
            items.append({"document_key": doc.get("document_key", path.parent.name), "title": title, "issuer": doc.get("issuer"), "effective_date": doc.get("effective_date"), "status": doc.get("status"), "unit_count": len(value.get("units", [])), "article_count": quality.get("article_count", 0), "quality_status": quality.get("status")})
        return items

    def get_document(self, key: str) -> dict[str, Any] | None:
        for path in self.root.glob("*/legal_knowledge.json") if self.root.is_dir() else []:
            value = self._read(path); doc = value.get("legal_document", {})
            if key in {path.parent.name, doc.get("document_key")}: return value
        return None

    def applicable_rules(self, keyword: str | None = None) -> list[dict[str, Any]]:
        """Return only published executable rules; legal units remain evidence, not rules."""
        return []

    @staticmethod
    def _read(path: Path) -> dict[str, Any]: return json.loads(path.read_text(encoding="utf-8"))
