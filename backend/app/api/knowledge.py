"""Read-only legal knowledge API; executable rules are not published yet."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser
from app.services.knowledge import KnowledgeService

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
Service = Annotated[KnowledgeService, Depends(KnowledgeService)]

@router.get("")
def list_knowledge(user: CurrentUser, service: Service, keyword: str | None = Query(default=None), status: str | None = Query(default=None)): return service.list_documents(keyword, status, user)

@router.get("/rules")
def list_rules(user: CurrentUser, service: Service, keyword: str | None = Query(default=None)): return service.rules(keyword, user)

@router.get("/{document_key}")
def get_knowledge(document_key: str, user: CurrentUser, service: Service): return service.detail(document_key, user)
