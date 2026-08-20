"""安全删除明确选定的已上传法律文档目录。"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.repositories.knowledge_repository import KnowledgeRepository


class CleanupError(RuntimeError):
    pass


def knowledge_root() -> Path:
    return PROJECT_ROOT / "knowledge" / "rules"


def plan(root: Path, keys: set[str]) -> list[Path]:
    repository = KnowledgeRepository(root)
    paths = []
    missing = []
    for key in keys:
        found = repository._path_and_value(key)
        if not found:
            missing.append(key)
        else:
            paths.append(found[0].parent)
    if missing:
        raise CleanupError(f"document_key does not exist: {', '.join(sorted(missing))}")
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely delete explicitly selected uploaded legal documents.")
    parser.add_argument("--list", action="store_true", help="List uploaded legal documents without changing data.")
    parser.add_argument("--document-key", action="append", default=[], help="Exact document key; may be repeated.")
    parser.add_argument("--confirm", action="store_true", help="Actually delete after backup.")
    parser.add_argument("--knowledge-dir", type=Path, default=knowledge_root(), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = args.knowledge_dir.resolve()
    repository = KnowledgeRepository(root)
    if args.list:
        if args.document_key:
            parser.error("--list cannot be combined with --document-key")
        for item in repository.list_documents():
            print(f"{item['document_key']}\t{item['status']}\t{item['title']}")
        return 0
    if not args.document_key:
        parser.error("provide --list or at least one exact --document-key")
    try:
        paths = plan(root, set(args.document_key))
        print("DRY-RUN" if not args.confirm else "DELETE")
        print("document_keys:", ", ".join(sorted(args.document_key)))
        print("directories:", len(paths))
        if not args.confirm:
            return 0
        backup = BACKEND_ROOT / "data" / "backups" / f"delete_legal_{datetime.now(UTC):%Y%m%dT%H%M%SZ}_{uuid4().hex[:8]}"
        moved = []
        try:
            for source in paths:
                destination = backup / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                moved.append((source, destination))
        except Exception as exc:
            for source, destination in reversed(moved):
                if destination.exists():
                    shutil.move(str(destination), str(source))
            raise CleanupError(f"delete failed; restored documents: {exc}") from exc
        print(f"Deleted. Recovery backup: {backup}")
        return 0
    except CleanupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
