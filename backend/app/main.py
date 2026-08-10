from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.procurement_reviews import router as procurement_reviews_router
from app.api.knowledge import router as knowledge_router
from app.core.config import get_settings
from app.review_engine.settings import load_settings
from pathlib import Path


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router, prefix=settings.api_prefix)
    app.include_router(procurement_reviews_router, prefix=settings.api_prefix)
    app.include_router(knowledge_router, prefix=settings.api_prefix)

    @app.get("/health", tags=["system"])
    def health() -> dict:
        mineru_url = settings.mineru_api_url
        return {"status": "ok", "review_execution_mode": settings.review_execution_mode, "mineru": {"configured": bool(mineru_url), "api_url": mineru_url, "note": "独立 MinerU 服务未连接时 live 审查不可用"}}

    return app


app = create_app()
