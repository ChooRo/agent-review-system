"""Legal evidence API plus the published executable-rule compatibility view."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from app.api.deps import CurrentUser
from app.schemas.knowledge import KnowledgeDocumentOut, KnowledgeDocumentUpdate
from app.schemas.rule import RuleOut
from app.services.knowledge import KnowledgeService

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
Service = Annotated[KnowledgeService, Depends(KnowledgeService)]


@router.get("")
def list_knowledge(user: CurrentUser, service: Service, keyword: str | None = Query(default=None), status: str | None = Query(default=None)):
    return service.list_documents(keyword, status, user)


@router.post("/documents", response_model=KnowledgeDocumentOut)
def upload_knowledge_document(
    user: CurrentUser,
    service: Service,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    issuer: str | None = Form(default=None),
    department: str | None = Form(default=None),
    document_version: str | None = Form(default=None),
    applicable_scope: str | None = Form(default=None),
    effective_date: str | None = Form(default=None),
    expiry_date: str | None = Form(default=None),
):
    return service.upload(file, {"title": title, "issuer": issuer, "department": department, "document_version": document_version, "applicable_scope": applicable_scope, "effective_date": effective_date, "expiry_date": expiry_date}, user)


@router.patch("/documents/{document_key}", response_model=KnowledgeDocumentOut)
def update_knowledge_document(document_key: str, payload: KnowledgeDocumentUpdate, user: CurrentUser, service: Service):
    return service.update(document_key, payload.model_dump(exclude_unset=True), user)


@router.post("/documents/{document_key}/extract-metadata")
def extract_knowledge_metadata(document_key: str, user: CurrentUser, service: Service):
    """Run evidence-bound AI extraction for the locally selected legal units."""
    return service.extract_metadata(document_key, user)


@router.get("/rules", response_model=list[RuleOut])
def list_rules(user: CurrentUser, service: Service, keyword: str | None = Query(default=None)):
    return service.rules(keyword, user)


@router.get("/{document_key}")
def get_knowledge(document_key: str, user: CurrentUser, service: Service):
    return service.detail(document_key, user)
