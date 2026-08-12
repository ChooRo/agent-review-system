from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser
from app.schemas.rule import RuleCreate, RuleExpire, RuleOut, RuleUpdate, RuleVersionOut, RuleVersionRequest
from app.services.rules import RuleService

router = APIRouter(prefix="/rules", tags=["rules"])
Service = Annotated[RuleService, Depends(RuleService)]


@router.get("", response_model=list[RuleOut])
def list_rules(user: CurrentUser, service: Service, keyword: str | None = None, status: str | None = None, module: str | None = None, department: str | None = None):
    return service.list(user, keyword, status, module, department)


@router.post("", response_model=RuleOut)
def create_rule(payload: RuleCreate, user: CurrentUser, service: Service): return service.create(payload.model_dump(), user)


@router.get("/{rule_id}/versions", response_model=list[RuleVersionOut])
def list_versions(rule_id: str, user: CurrentUser, service: Service): return service.versions(rule_id, user)


@router.get("/{rule_id}", response_model=RuleOut)
def get_rule(rule_id: str, user: CurrentUser, service: Service): return service.detail(rule_id, user)


@router.patch("/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: str, payload: RuleUpdate, user: CurrentUser, service: Service): return service.update(rule_id, payload.model_dump(exclude_none=True), user)


@router.post("/{rule_id}/confirm", response_model=RuleOut)
def confirm_rule(rule_id: str, payload: RuleVersionRequest | None, user: CurrentUser, service: Service): return service.confirm(rule_id, payload.model_dump() if payload else {}, user)


@router.post("/{rule_id}/expire", response_model=RuleOut)
def expire_rule(rule_id: str, payload: RuleExpire, user: CurrentUser, service: Service): return service.expire(rule_id, payload.model_dump(), user)


@router.post("/{rule_id}/reactivate", response_model=RuleOut)
def reactivate_rule(rule_id: str, payload: RuleVersionRequest, user: CurrentUser, service: Service): return service.reactivate(rule_id, payload.model_dump(), user)
