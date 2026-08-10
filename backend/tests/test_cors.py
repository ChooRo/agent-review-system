from fastapi.testclient import TestClient

from app.main import create_app
from app.core.config import get_settings


def test_local_frontend_origins_are_allowed_and_other_origin_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    get_settings.cache_clear()
    client = TestClient(create_app())
    for origin in ("http://localhost:5173", "http://127.0.0.1:5173"):
        response = client.options("/api/v1/auth/login", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
    denied = client.options("/api/v1/auth/login", headers={"Origin": "http://example.test", "Access-Control-Request-Method": "POST"})
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers
    get_settings.cache_clear()
