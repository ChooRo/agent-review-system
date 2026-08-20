"""Repository 分发：当前唯一后端为 Postgres。

JSON 版实现（ReviewRepository/RuleRepository）已归档至
backend/archive/json-backend/；如未来恢复双后端，按 STORAGE_BACKEND 分发。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.repositories.postgres.review_repository import PostgresReviewRepository
from app.repositories.postgres.rule_repository import PostgresRuleRepository


def get_review_repository(root: Path) -> Any:
    return PostgresReviewRepository(root)


def get_rule_repository(data_dir: Path) -> Any:
    return PostgresRuleRepository(data_dir)
