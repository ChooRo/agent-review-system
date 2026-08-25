"""安全删除明确选定的已上传法律文档目录。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
from app.integrations.storage.local import LocalStorage
from app.repositories.postgres.knowledge_repository import PostgresKnowledgeRepository


class CleanupError(RuntimeError):
    pass


def repository() -> PostgresKnowledgeRepository:
    settings = get_settings()
    return PostgresKnowledgeRepository(Path(settings.data_dir), LocalStorage(settings.uploads_dir))


def plan(keys: set[str]) -> list[dict]:
    store = repository()
    documents = []
    missing = []
    for key in keys:
        value = store.get_document(key)
        if not value:
            missing.append(key)
        else:
            documents.append(value)
    if missing:
        raise CleanupError(f"document_key does not exist: {', '.join(sorted(missing))}")
    return documents


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely delete explicitly selected uploaded legal documents.")
    parser.add_argument("--list", action="store_true", help="List uploaded legal documents without changing data.")
    parser.add_argument("--document-key", action="append", default=[], help="Exact document key; may be repeated.")
    parser.add_argument("--confirm", action="store_true", help="Actually delete after backup.")
    args = parser.parse_args(argv)
    store = repository()
    if args.list:
        if args.document_key:
            parser.error("--list cannot be combined with --document-key")
        for item in store.list_documents():
            print(f"{item['document_key']}\t{item['status']}\t{item['title']}")
        return 0
    if not args.document_key:
        parser.error("provide --list or at least one exact --document-key")
    try:
        documents = plan(set(args.document_key))
        print("DRY-RUN" if not args.confirm else "DELETE")
        print("document_keys:", ", ".join(sorted(args.document_key)))
        print("documents:", len(documents))
        if not args.confirm:
            return 0
        backup = BACKEND_ROOT / "data" / "backups" / f"delete_legal_{datetime.now(UTC):%Y%m%dT%H%M%SZ}_{uuid4().hex[:8]}"
        backup.mkdir(parents=True, exist_ok=True)
        (backup / "legal_documents.json").write_text(json.dumps(documents, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            for document in documents:
                if not store.delete_document(document["legal_document"]["document_key"]):
                    raise CleanupError(f"document disappeared during deletion: {document['legal_document']['document_key']}")
        except Exception as exc:
            raise CleanupError(f"delete failed; metadata backup: {backup}: {exc}") from exc
        print(f"Deleted. Recovery backup: {backup}")
        return 0
    except CleanupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
