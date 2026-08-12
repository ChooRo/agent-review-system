"""Executable-rule authorization."""

from typing import Any

from .common import is_admin


def can_maintain_rules(user: dict[str, Any]) -> bool:
    return is_admin(user)


def can_view_rule(user: dict[str, Any], rule: dict[str, Any]) -> bool:
    return is_admin(user) or rule.get("status") == "published"
