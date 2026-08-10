from pathlib import Path

from fastapi import HTTPException

from app.repositories.knowledge_repository import KnowledgeRepository


class KnowledgeService:
    def __init__(self) -> None:
        self.repository = KnowledgeRepository(Path(__file__).resolve().parents[3] / "knowledge" / "rules")

    def list_documents(self, keyword: str | None, status: str | None, _user: dict) -> list[dict]: return self.repository.list_documents(keyword, status)
    def detail(self, key: str, _user: dict) -> dict:
        value = self.repository.get_document(key)
        if not value: raise HTTPException(404, "法规知识不存在")
        return value
    def rules(self, keyword: str | None, _user: dict) -> list[dict]: return self.repository.applicable_rules(keyword)
