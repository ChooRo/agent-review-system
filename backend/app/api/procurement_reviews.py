from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, Query, UploadFile

from app.api.deps import CurrentUser
from app.schemas.procurement_review import CollaborativeComment, EventOut, FindingOut, OperatorDisposition, PrimaryDecision, ProjectCreate, ProjectOut, ProjectUpdate, TaskCreate, TaskOut
from app.services.procurement_review import ProcurementReviewService

router = APIRouter(prefix="/projects", tags=["procurement-reviews"])
Service = Annotated[ProcurementReviewService, Depends(ProcurementReviewService)]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]

@router.post("", response_model=ProjectOut)
def create_project(payload: ProjectCreate, user: CurrentUser, service: Service, idempotency_key: IdempotencyKey = None): return service.create_project(payload.model_dump(), user, idempotency_key)
@router.get("", response_model=list[ProjectOut])
def list_projects(user: CurrentUser, service: Service): return service.list_projects(user)
@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, user: CurrentUser, service: Service): return service.get_project(project_id, user)
@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: str, payload: ProjectUpdate, user: CurrentUser, service: Service): return service.update_project(project_id, payload.model_dump(exclude_none=True), user)
@router.post("/{project_id}/procurement-review-tasks", response_model=TaskOut)
def create_task(project_id: str, payload: TaskCreate, user: CurrentUser, service: Service, idempotency_key: IdempotencyKey = None): return service.create_task(project_id, payload.model_dump(), user, idempotency_key)
@router.get("/{project_id}/procurement-review-tasks", response_model=list[TaskOut])
def list_tasks(project_id: str, user: CurrentUser, service: Service): return service.tasks_for_project(project_id, user)
@router.get("/{project_id}/procurement-review-tasks/{task_id}", response_model=TaskOut)
def get_task(project_id: str, task_id: str, user: CurrentUser, service: Service): return service.get_task(project_id, task_id, user)
@router.post("/{project_id}/procurement-review-tasks/{task_id}/document", response_model=TaskOut)
def upload_document(project_id: str, task_id: str, user: CurrentUser, service: Service, file: UploadFile = File(...)): return service.upload(project_id, task_id, file, user)
@router.post("/{project_id}/procurement-review-tasks/{task_id}/start", response_model=TaskOut)
def start_task(project_id: str, task_id: str, user: CurrentUser, service: Service, idempotency_key: IdempotencyKey = None): return service.start(project_id, task_id, user, idempotency_key)
@router.get("/{project_id}/procurement-review-tasks/{task_id}/events", response_model=list[EventOut])
def events(project_id: str, task_id: str, user: CurrentUser, service: Service, after: str | None = Query(default=None)): return service.events(project_id, task_id, user, after)
@router.get("/{project_id}/procurement-review-tasks/{task_id}/debug-traces")
def debug_traces(project_id: str, task_id: str, user: CurrentUser, service: Service): return service.debug_traces(project_id, task_id, user)
@router.get("/{project_id}/procurement-review-tasks/{task_id}/findings", response_model=list[FindingOut])
def list_findings(project_id: str, task_id: str, user: CurrentUser, service: Service): return service.findings_for_task(project_id, task_id, user)
@router.put("/{project_id}/procurement-review-tasks/{task_id}/findings/{finding_id}/operator-disposition")
def operator_disposition(project_id: str, task_id: str, finding_id: str, payload: OperatorDisposition, user: CurrentUser, service: Service): return service.operator_disposition(project_id, task_id, finding_id, payload.model_dump(), user)
@router.post("/{project_id}/procurement-review-tasks/{task_id}/operator-submit")
def operator_submit(project_id: str, task_id: str, user: CurrentUser, service: Service, idempotency_key: IdempotencyKey = None): return service.operator_submit(project_id, task_id, user, idempotency_key)
@router.put("/{project_id}/procurement-review-tasks/{task_id}/findings/{finding_id}/primary-decision")
def primary_decision(project_id: str, task_id: str, finding_id: str, payload: PrimaryDecision, user: CurrentUser, service: Service): return service.primary_decision(project_id, task_id, finding_id, payload.model_dump(exclude_none=True), user)
@router.post("/{project_id}/procurement-review-tasks/{task_id}/primary-confirm")
def primary_confirm(project_id: str, task_id: str, user: CurrentUser, service: Service, idempotency_key: IdempotencyKey = None): return service.primary_confirm(project_id, task_id, user, idempotency_key)
@router.post("/{project_id}/procurement-review-tasks/{task_id}/findings/{finding_id}/collaborative-comments")
def create_comment(project_id: str, task_id: str, finding_id: str, payload: CollaborativeComment, user: CurrentUser, service: Service): return service.collaborative_comment(project_id, task_id, finding_id, None, payload.model_dump(), user)
@router.put("/{project_id}/procurement-review-tasks/{task_id}/findings/{finding_id}/collaborative-comments/{comment_id}")
def update_comment(project_id: str, task_id: str, finding_id: str, comment_id: str, payload: CollaborativeComment, user: CurrentUser, service: Service): return service.collaborative_comment(project_id, task_id, finding_id, comment_id, payload.model_dump(), user)
