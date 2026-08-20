"""采购审查项目和任务授权。"""

from typing import Any

from .common import has_role, is_admin

PROCUREMENT_DEPARTMENT = "采购部门"


def can_list_all_projects(user: dict[str, Any]) -> bool:
    return is_admin(user) or has_role(user, "supervisor")


def task_member(task: dict[str, Any], user: dict[str, Any]) -> dict[str, Any] | None:
    return next((member for member in task.get("members", []) if member.get("user_id") == user.get("id")), None)


def can_access_task(task: dict[str, Any], user: dict[str, Any]) -> bool:
    return is_admin(user) or task_member(task, user) is not None


def can_access_project(project: dict[str, Any], tasks: list[dict[str, Any]], user: dict[str, Any]) -> bool:
    return is_admin(user) or project.get("created_by") == user.get("id") or any(task_member(task, user) for task in tasks if task.get("project_id") == project.get("id"))


def is_task_operator(task: dict[str, Any], user: dict[str, Any]) -> bool:
    return task.get("operator_id") == user.get("id")


def has_task_role(task: dict[str, Any], user: dict[str, Any], role: str) -> bool:
    member = task_member(task, user)
    return bool(member and member.get("task_role") == role)


def has_task_module_scope(task: dict[str, Any], user: dict[str, Any], module: str) -> bool:
    member = task_member(task, user)
    return bool(member and module in member.get("module_scope", []))


def is_primary_supervisor(task: dict[str, Any], user: dict[str, Any]) -> bool:
    member = task_member(task, user)
    return bool(member and member.get("task_role") == "primary_supervisor" and member.get("department") == PROCUREMENT_DEPARTMENT and "procurement" in member.get("module_scope", []))


def is_collaborative_supervisor(task: dict[str, Any], user: dict[str, Any]) -> bool:
    return has_task_role(task, user, "collaborative_supervisor") and has_task_module_scope(task, user, "procurement")


def can_be_primary_supervisor(user: dict[str, Any]) -> bool:
    return has_role(user, "supervisor") and user.get("department") == PROCUREMENT_DEPARTMENT


def can_be_collaborative_supervisor(user: dict[str, Any]) -> bool:
    return has_role(user, "supervisor")
