from typing import Literal

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    project_code: str = Field(min_length=1, max_length=100)
    handling_department: str = Field(min_length=1, max_length=100)
    # 前端使用用户 ID；保留字符串兼容旧客户端提交的用户名。
    project_owner: int | str


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    handling_department: str | None = Field(default=None, min_length=1, max_length=100)
    project_owner: str | None = Field(default=None, min_length=1, max_length=100)
    version: int = Field(ge=1)


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    collaborative_supervisor_ids: list[int] = Field(default_factory=list)


class OperatorDisposition(BaseModel):
    action: Literal["accept", "partial_accept", "reject", "edit"]
    comment: str | None = Field(default=None, max_length=2000)
    version: int = Field(ge=1)


class PrimaryDecision(BaseModel):
    decision: Literal["receive", "adjust", "reject"]
    comment: str | None = Field(default=None, max_length=2000)
    risk_level: Literal["high", "medium", "low", "pending", "unknown"] | None = None
    version: int = Field(ge=1)


class CollaborativeComment(BaseModel):
    comment: str = Field(min_length=1, max_length=2000)
    version: int | None = Field(default=None, ge=1)


class LegalApplicabilityConfirmation(BaseModel):
    decision: Literal["confirmed", "rejected", "needs_more_facts"]
    comment: str | None = Field(default=None, max_length=2000)
    version: int = Field(ge=1)


class DocumentOut(BaseModel):
    id: str
    file_name: str
    content_type: str
    size: int
    sha256: str
    version: int
    uploaded_by: int
    uploaded_at: str


class FindingOut(BaseModel):
    id: str
    task_id: str
    source_type: str
    risk_level: str
    title: str
    description: str
    suggestion: str
    source: dict
    sources: list[dict] = Field(default_factory=list)
    finding_type: str | None = None
    review_scope: str = "finding"
    evidence_status: str = "evidence_insufficient"
    evidence_validation: dict = Field(default_factory=dict)
    rule_refs: list[dict] = []
    legal_refs: list[dict] = []
    operator_disposition: dict | None = None
    primary_decision: dict | None = None
    recheck_required: bool = False
    collaborative_comments: list[dict] = []
    version: int


class EventOut(BaseModel):
    id: str
    task_id: str
    actor_id: int
    at: str
    before_status: str | None
    after_status: str
    reason: str


class TaskOut(BaseModel):
    id: str
    project_id: str
    title: str
    status: str
    document: DocumentOut | None = None
    document_versions: list[DocumentOut] = Field(default_factory=list)
    final_baseline: dict | None = None
    finding_summary: dict
    progress: float = Field(ge=0, le=100)
    progress_step: str | None = None
    batch_completed: int | None = None
    batch_total: int | None = None
    task_role: str | None = None
    module_scope: list[str] = []
    quality: dict = Field(default_factory=dict)
    legal_facts: dict = Field(default_factory=dict)
    legal_applicability: list[dict] = Field(default_factory=list)
    legal_context_freeze: list[dict] = Field(default_factory=list)
    pipeline_status: Literal["completed", "degraded"] | None = None
    degraded_steps: list[dict] = Field(default_factory=list)
    system_warnings: list[dict] = Field(default_factory=list)
    coverage_matrix: list[dict] = Field(default_factory=list)
    legal_applicability_confirmations: dict[str, dict] = Field(default_factory=dict)
    engine_run_id: str | None = None
    error: str | None = None
    version: int
    created_at: str
    updated_at: str


class ProjectOut(BaseModel):
    id: str
    name: str
    project_code: str
    handling_department: str
    project_owner: str
    status: str
    task_ids: list[str]
    version: int
    created_at: str
    updated_at: str
    task_summaries: list[TaskOut] = []
