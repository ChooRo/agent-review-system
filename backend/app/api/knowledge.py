"""法律证据 API 及已发布的可执行规则兼容视图。"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from app.api.deps import CurrentUser
from app.schemas.knowledge import KnowledgeDocumentOut, KnowledgeDocumentUpdate
from app.schemas.rule import RuleOut
from app.services.legal.knowledge import KnowledgeService
from app.policies import knowledge as knowledge_policy

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
Service = Annotated[KnowledgeService, Depends(KnowledgeService)]


@router.get("")
def list_knowledge(user: CurrentUser, service: Service, keyword: str | None = Query(default=None), status: str | None = Query(default=None)):
    """查询法律知识库文档，并按用户权限过滤结果。"""
    return service.list_documents(keyword, status, user)


@router.post("/documents")
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
    """上传法律文档并创建异步解析任务。"""
    return service.upload(file, {"title": title, "issuer": issuer, "department": department, "document_version": document_version, "applicable_scope": applicable_scope, "effective_date": effective_date, "expiry_date": expiry_date}, user)


@router.get("/documents/tasks/{task_id}")
def get_knowledge_upload_task(task_id: str, user: CurrentUser, service: Service):
    """查询法律文档上传和解析任务的当前状态。"""
    if not knowledge_policy.can_maintain_knowledge(user):
        raise HTTPException(403, "only administrators can view legal upload tasks")
    task = service.task(task_id)
    if not task:
        raise HTTPException(404, "legal upload task not found")
    return task


@router.post("/documents/tasks/{task_id}/retry")
def retry_knowledge_upload_task(task_id: str, user: CurrentUser, service: Service):
    """重新执行失败的法律文档上传任务。"""
    if not knowledge_policy.can_maintain_knowledge(user):
        raise HTTPException(403, "only administrators can retry legal upload tasks")
    task = service.retry_task(task_id)
    if not task:
        raise HTTPException(409, "legal upload task cannot be retried")
    return task


@router.patch("/documents/{document_key}", response_model=KnowledgeDocumentOut)
def update_knowledge_document(document_key: str, payload: KnowledgeDocumentUpdate, user: CurrentUser, service: Service):
    """更新法律知识文档的元数据。"""
    return service.update(document_key, payload.model_dump(exclude_unset=True), user)


@router.post("/documents/{document_key}/extract-metadata")
def extract_knowledge_metadata(document_key: str, user: CurrentUser, service: Service):
    """对本地选定的法律单元执行绑定证据的 AI 提取。"""
    return service.extract_metadata(document_key, user)


@router.get("/rules", response_model=list[RuleOut])
def list_rules(user: CurrentUser, service: Service, keyword: str | None = Query(default=None)):
    """查询当前用户可见的法律适用规则。"""
    return service.rules(keyword, user)


@router.get("/{document_key}")
def get_knowledge(document_key: str, user: CurrentUser, service: Service):
    """获取单个法律知识文档的详细信息。"""
    return service.detail(document_key, user)
