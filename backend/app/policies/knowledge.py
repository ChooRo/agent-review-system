"""法律证据文档授权。"""

from typing import Any

from .common import is_admin


def can_maintain_knowledge(user: dict[str, Any]) -> bool:
    return is_admin(user)


def can_view_knowledge_document(user: dict[str, Any], document: dict[str, Any]) -> bool:
    return is_admin(user) or document.get("status") == "effective"


def visible_document_status(user: dict[str, Any], requested_status: str | None) -> str | None:
    return requested_status if is_admin(user) else "effective"
