from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from app.core.config import get_settings
from app.policies import rules as rules_policy
from app.repositories.rule_repository import RuleRepository


class RuleService:
    def __init__(self) -> None:
        self.repository = RuleRepository(Path(get_settings().data_dir))

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _snapshot(state: dict, rule: dict, event: str) -> None:
        state["versions"].append({**deepcopy(rule), "snapshot_id": f"rvs_{uuid4().hex}", "recorded_at": RuleService._now(), "event": event})

    @staticmethod
    def _audit(state: dict, user: dict, action: str, rule: dict) -> None:
        state["audit"].append({"id": f"rad_{uuid4().hex}", "rule_id": rule["id"], "version": rule["version"], "action": action, "actor_id": user["id"], "at": RuleService._now()})

    @staticmethod
    def _conflict() -> None:
        raise HTTPException(409, "rule version or state conflict")

    def list(self, user: dict, keyword: str | None, status: str | None, module: str | None, department: str | None) -> list[dict]:
        items = self.repository.list_current() if rules_policy.can_maintain_rules(user) else self.repository.published(module)
        def matches(rule: dict) -> bool:
            searchable = " ".join([rule["title"], rule["description"], rule["decision_criteria"], *rule.get("tags", [])]).lower()
            return (not keyword or keyword.lower() in searchable) and (not status or rule["status"] == status) and (not module or rule["module"] == module) and (not department or rule["department"] == department)
        return [rule for rule in items if matches(rule)]

    def detail(self, rule_id: str, user: dict) -> dict:
        current = self.repository.current(rule_id)
        if not current:
            raise HTTPException(404, "rule not found")
        if not rules_policy.can_view_rule(user, current):
            published = next((item for item in self.repository.published(current["module"]) if item["id"] == rule_id), None)
            if not published:
                raise HTTPException(404, "rule not found")
            return published
        return current

    def create(self, payload: dict, user: dict) -> dict:
        if not rules_policy.can_maintain_rules(user):
            raise HTTPException(403, "only administrators can maintain rules")
        now = self._now()
        status = "pending_confirmation"
        rule = {**payload, "id": f"rul_{uuid4().hex}", "status": status, "version": 1, "created_at": now, "created_by": user["id"], "updated_at": now, "updated_by": user["id"], "published_at": now if status == "published" else None, "published_by": user["id"] if status == "published" else None, "expired_at": None, "expired_by": None, "expiry_reason": None}
        def mutate(state: dict) -> dict:
            state["rules"].append(rule); self._snapshot(state, rule, "created"); self._audit(state, user, "created", rule); return deepcopy(rule)
        return self.repository.transaction(mutate)

    def update(self, rule_id: str, payload: dict, user: dict) -> dict:
        def mutate(state: dict) -> dict:
            rule = next((item for item in state["rules"] if item["id"] == rule_id), None)
            if not rule: raise HTTPException(404, "rule not found")
            if payload.pop("version") != rule["version"]: self._conflict()
            if not rules_policy.can_maintain_rules(user): raise HTTPException(403, "only administrators can maintain rules")
            if rule["status"] == "expired": self._conflict()
            rule.update({key: value for key, value in payload.items() if value is not None})
            rule.update({"version": rule["version"] + 1, "status": "pending_confirmation", "updated_at": self._now(), "updated_by": user["id"]})
            self._snapshot(state, rule, "updated_pending_confirmation"); self._audit(state, user, "updated", rule); return deepcopy(rule)
        return self.repository.transaction(mutate)

    def confirm(self, rule_id: str, payload: dict, user: dict) -> dict:
        def mutate(state: dict) -> dict:
            rule = next((item for item in state["rules"] if item["id"] == rule_id), None)
            if not rule: raise HTTPException(404, "rule not found")
            if payload.get("version") is not None and payload["version"] != rule["version"]: self._conflict()
            if not rules_policy.can_maintain_rules(user): raise HTTPException(403, "only administrators can confirm rules")
            if rule["status"] != "pending_confirmation": self._conflict()
            rule.update({"status": "published", "published_at": self._now(), "published_by": user["id"], "updated_at": self._now(), "updated_by": user["id"]})
            self._snapshot(state, rule, "confirmed"); self._audit(state, user, "confirmed", rule); return deepcopy(rule)
        return self.repository.transaction(mutate)

    def expire(self, rule_id: str, payload: dict, user: dict) -> dict:
        def mutate(state: dict) -> dict:
            rule = next((item for item in state["rules"] if item["id"] == rule_id), None)
            if not rule: raise HTTPException(404, "rule not found")
            if payload["version"] != rule["version"] or rule["status"] != "published": self._conflict()
            if not rules_policy.can_maintain_rules(user): raise HTTPException(403, "only administrators can expire rules")
            rule.update({"version": rule["version"] + 1, "status": "expired", "expiry_reason": payload["reason"], "expired_at": self._now(), "expired_by": user["id"], "updated_at": self._now(), "updated_by": user["id"]})
            self._snapshot(state, rule, "expired"); self._audit(state, user, "expired", rule); return deepcopy(rule)
        return self.repository.transaction(mutate)

    def reactivate(self, rule_id: str, payload: dict, user: dict) -> dict:
        def mutate(state: dict) -> dict:
            rule = next((item for item in state["rules"] if item["id"] == rule_id), None)
            if not rule: raise HTTPException(404, "rule not found")
            if payload["version"] != rule["version"] or rule["status"] != "expired": self._conflict()
            if not rules_policy.can_maintain_rules(user): raise HTTPException(403, "only administrators can reactivate rules")
            rule.update({"version": rule["version"] + 1, "status": "pending_confirmation", "expiry_reason": None, "expired_at": None, "expired_by": None, "updated_at": self._now(), "updated_by": user["id"]})
            self._snapshot(state, rule, "reactivated_pending_confirmation"); self._audit(state, user, "reactivated", rule); return deepcopy(rule)
        return self.repository.transaction(mutate)

    def versions(self, rule_id: str, user: dict) -> list[dict]:
        self.detail(rule_id, user)
        versions = self.repository.versions(rule_id)
        if not rules_policy.can_maintain_rules(user):
            return [item for item in versions if item["status"] == "published"]
        return versions
