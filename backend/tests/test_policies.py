from app.policies import common, knowledge, review, rules


ADMIN = {"id": 1, "role_codes": ["admin"]}
OPERATOR = {"id": 2, "role_codes": ["operator"]}
PRIMARY = {"id": 3, "role_codes": ["supervisor"], "department": "采购部门"}
COLLABORATOR = {"id": 4, "roles": [{"code": "supervisor"}], "department": "法规部门"}
TASK = {
    "id": "task-1", "project_id": "project-1", "operator_id": 2,
    "members": [
        {"user_id": 2, "task_role": "operator", "department": "业务部门", "module_scope": ["procurement"]},
        {"user_id": 3, "task_role": "primary_supervisor", "department": "采购部门", "module_scope": ["procurement"]},
        {"user_id": 4, "task_role": "collaborative_supervisor", "department": "法规部门", "module_scope": ["procurement"]},
    ],
}


def test_common_knowledge_and_rule_policies() -> None:
    assert common.has_role(COLLABORATOR, "supervisor") and common.is_admin(ADMIN)
    assert knowledge.can_maintain_knowledge(ADMIN) and not knowledge.can_maintain_knowledge(PRIMARY)
    assert knowledge.can_view_knowledge_document(OPERATOR, {"status": "effective"})
    assert not knowledge.can_view_knowledge_document(PRIMARY, {"status": "unknown"})
    assert knowledge.visible_document_status(PRIMARY, "unknown") == "effective"
    assert rules.can_maintain_rules(ADMIN) and not rules.can_maintain_rules(PRIMARY)
    assert rules.can_view_rule(COLLABORATOR, {"status": "published"})
    assert not rules.can_view_rule(OPERATOR, {"status": "pending_confirmation"})


def test_review_membership_roles_and_module_scope() -> None:
    project = {"id": "project-1", "created_by": 2}
    assert review.can_access_project(project, [TASK], PRIMARY)
    assert review.can_access_task(TASK, COLLABORATOR) and not review.can_access_task(TASK, {"id": 9, "role_codes": ["supervisor"]})
    assert review.is_task_operator(TASK, OPERATOR)
    assert review.is_primary_supervisor(TASK, PRIMARY)
    assert review.is_collaborative_supervisor(TASK, COLLABORATOR)
    assert review.has_task_module_scope(TASK, COLLABORATOR, "procurement")
    assert review.can_be_primary_supervisor(PRIMARY) and not review.can_be_primary_supervisor(COLLABORATOR)
