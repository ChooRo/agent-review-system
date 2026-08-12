from typing import Literal

from pydantic import BaseModel, Field


RuleStatus = Literal["draft", "pending_confirmation", "published", "expired"]
RuleSource = Literal["manual", "ai_candidate", "legal_extraction"]
RiskLevel = Literal["mandatory", "general"]


class RuleCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    decision_criteria: str = Field(min_length=1, max_length=4000)
    risk_level: RiskLevel = "general"
    module: Literal["procurement"]
    department: str = Field(min_length=1, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=30)
    source_type: RuleSource = "manual"
    legal_document_key: str | None = Field(default=None, max_length=200)
    legal_unit_ids: list[str] = Field(default_factory=list, max_length=100)


class RuleUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    decision_criteria: str | None = Field(default=None, min_length=1, max_length=4000)
    risk_level: RiskLevel | None = None
    tags: list[str] | None = Field(default=None, max_length=30)
    legal_document_key: str | None = Field(default=None, max_length=200)
    legal_unit_ids: list[str] | None = Field(default=None, max_length=100)
    version: int = Field(ge=1)


class RuleVersionRequest(BaseModel):
    version: int = Field(ge=1)


class RuleExpire(RuleVersionRequest):
    reason: str = Field(min_length=1, max_length=2000)


class RuleOut(BaseModel):
    id: str
    title: str
    description: str
    decision_criteria: str
    risk_level: RiskLevel
    module: str
    department: str
    tags: list[str]
    status: RuleStatus
    source_type: RuleSource
    legal_document_key: str | None = None
    legal_unit_ids: list[str] = Field(default_factory=list)
    version: int
    created_at: str
    created_by: int
    updated_at: str
    updated_by: int
    published_at: str | None = None
    published_by: int | None = None
    expired_at: str | None = None
    expired_by: int | None = None
    expiry_reason: str | None = None


class RuleVersionOut(RuleOut):
    snapshot_id: str
    recorded_at: str
    event: str
