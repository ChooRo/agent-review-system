import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.procurement_reviews import router as procurement_reviews_router
from app.api.knowledge import router as knowledge_router
from app.api.rules import router as rules_router
from app.core.config import get_settings
from app.review_engine.settings import load_settings
from app.review_engine.services.runtime import configure_logging


logger = logging.getLogger("review_api")


def create_app() -> FastAPI:
    settings = get_settings()
    config_path = Path(__file__).resolve().parents[1] / "review_config.json"
    review_config = load_settings(config_path if config_path.is_file() else None)
    log_path = configure_logging(
        review_config["runtime"]["log_level"],
        Path(__file__).resolve().parents[1] / "logs" / "app.log",
    )
    llm = review_config.get("llm", {})
    if not (llm.get("api_url") and llm.get("api_key") and llm.get("model")):
        raise RuntimeError("LLM configuration requires api_url, api_key, and model")
    app = FastAPI(title=settings.app_name, version="0.1.0")

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("request_failed method=%s path=%s", request.method, request.url.path)
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "request method=%s path=%s status=%s duration_ms=%.1f",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response
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
    app.include_router(rules_router, prefix=settings.api_prefix)

    @app.get("/health", tags=["system"])
    def health() -> dict:
        mineru_url = settings.mineru_api_url
        return {"status": "ok", "mineru": {"configured": bool(mineru_url), "api_url": mineru_url, "note": "独立 MinerU 服务未连接时审查不可用"}}

    logger.info("application_started log_path=%s", log_path)
    return app


app = create_app()
