"""Common account-role predicates."""

from typing import Any


def role_codes(user: dict[str, Any]) -> set[str]:
    return set(user.get("role_codes") or [role["code"] for role in user.get("roles", [])])


def has_role(user: dict[str, Any], role: str) -> bool:
    return role in role_codes(user)


def is_admin(user: dict[str, Any]) -> bool:
    return has_role(user, "admin")
