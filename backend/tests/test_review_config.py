from pathlib import Path
from unittest.mock import patch

from app.core.config import get_settings
from app.review_engine.settings import load_settings


def test_review_config_paths_resolve_inside_project() -> None:
    backend = Path(__file__).resolve().parents[1]
    settings = load_settings(backend / "review_config.json")
    assert Path(settings["rules"]["knowledge_root"]) == backend.parent / "knowledge" / "rules"
    assert Path(settings["runtime"]["runs_root"]) == backend.parent / "runs"
    assert settings["mineru"]["api_url"] == "http://127.0.0.1:8001"


def test_engine_settings_allow_environment_override(tmp_path) -> None:
    config = tmp_path / "config.json"; config.write_text('{"runtime":{"runs_root":"runs"}}', encoding="utf-8")
    with patch.dict("os.environ", {"MINERU_API_URL": "http://127.0.0.1:9000"}):
        settings = load_settings(config)
    assert settings["mineru"]["api_url"] == "http://127.0.0.1:9000"
    assert Path(settings["runtime"]["runs_root"]) == tmp_path / "runs"


def test_ipv6_mineru_url_is_accepted_by_backend_settings(monkeypatch) -> None:
    monkeypatch.setenv("MINERU_API_URL", "http://[::1]:8000")
    get_settings.cache_clear()
    assert get_settings().mineru_api_url == "http://[::1]:8000"
    get_settings.cache_clear()
