"""为已持久化的法律知识单元补齐受控主题。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.review_engine.services.topics import dictionary_topics  # noqa: E402


def migrate(path: Path, backup_root: Path, dry_run: bool = False) -> tuple[int, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    units = payload.get("units", [])
    if not isinstance(units, list):
        raise ValueError(f"units is not a list: {path}")

    tagged = 0
    for unit in units:
        text = "\n".join(
            str(unit.get(field) or "")
            for field in ("document_title", "chapter", "section", "article_no", "text", "parent_context", "search_text")
        )
        topics = dictionary_topics(text)
        if topics:
            tagged += 1
        unit["topics"] = topics

    if not dry_run:
        relative = path.relative_to(ROOT / "knowledge" / "rules")
        backup = backup_root / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        payload["topic_vocabulary_version"] = "1.0.0"
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    return len(units), tagged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rules_root = ROOT / "knowledge" / "rules"
    paths = sorted(rules_root.glob("*/legal_knowledge.json"))
    backup_root = rules_root / ".topic_backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    total_units = total_tagged = 0
    for path in paths:
        units, tagged = migrate(path, backup_root, args.dry_run)
        total_units += units
        total_tagged += tagged
        print(f"{path.parent.name}: {units} units, {tagged} tagged")
    print(f"total: {total_units} units, {total_tagged} tagged")
    if not args.dry_run:
        print(f"backups: {backup_root}")


if __name__ == "__main__":
    main()
