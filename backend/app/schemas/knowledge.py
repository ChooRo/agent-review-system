from typing import Literal

from pydantic import BaseModel, Field


class KnowledgeDocumentOut(BaseModel):
    document_key: str
    title: str
    canonical_title: str | None = None
    legal_level: str | None = None
    document_number: str | None = None
    issuer: str | None = None
    effective_date: str | None = None
    expiry_date: str | None = None
    status: Literal["unknown", "effective", "repealed"]
    document_version: str
    department: str | None = None
    applicable_scope: str
    metadata_version: int
    updated_at: str | None = None
    updated_by: str | None = None
    summary: str | None = Field(default=None, max_length=160)
    unit_count: int
    article_count: int
    quality_status: str | None = None
    extraction_status: str | None = None


class KnowledgeDocumentUpdate(BaseModel):
    metadata_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    issuer: str | None = Field(default=None, max_length=300)
    document_version: str | None = Field(default=None, min_length=1, max_length=100)
    department: str | None = Field(default=None, min_length=1, max_length=100)
    applicable_scope: str | None = Field(default=None, max_length=1000)
    effective_date: str | None = Field(default=None, max_length=32)
    expiry_date: str | None = Field(default=None, max_length=32)
    status: Literal["unknown", "effective", "repealed"] | None = None
    canonical_title: str | None = Field(default=None, min_length=1, max_length=300)
    legal_level: Literal["law", "administrative_regulation", "department_rule", "local_regulation", "internal_policy", "other"] | None = None
    document_number: str | None = Field(default=None, max_length=100)
    adoption_date: str | None = Field(default=None, max_length=32)
    promulgation_date: str | None = Field(default=None, max_length=32)
    original_effective_date: str | None = Field(default=None, max_length=32)
    revision_date: str | None = Field(default=None, max_length=32)
    current_version_effective_date: str | None = Field(default=None, max_length=32)
    applicability: dict | None = None
