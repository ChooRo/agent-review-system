import json
from pathlib import Path

from app.repositories.review_repository import ReviewRepository
from scripts import delete_projects as cleanup


def make_store(tmp_path: Path, status: str = "draft") -> tuple[Path, Path, dict]:
    data_dir, uploads_dir = tmp_path / "data", tmp_path / "uploads"
    target = {"id": "prj_target", "project_code": "TEST-DELETE", "name": "target", "status": "draft", "task_ids": ["prt_target"]}
    keep = {"id": "prj_keep", "project_code": "KEEP", "name": "keep", "status": "active", "task_ids": ["prt_keep"]}
    state = {
        "projects": [target, keep],
        "tasks": [{"id": "prt_target", "project_id": "prj_target", "status": status}, {"id": "prt_keep", "project_id": "prj_keep", "status": "draft"}],
        "findings": [{"id": "fdg_target", "task_id": "prt_target"}],
        "comments": [{"id": "cmt_target", "finding_id": "fdg_target", "task_id": "prt_target"}],
        "events": [{"id": "evt_target", "task_id": "prt_target"}],
        "audit": [{"id": "aud_target", "target_id": "prt_target", "details": {}}, {"id": "aud_nested", "target_id": "prj_keep", "details": {"finding_id": "fdg_target"}}],
        "idempotency": [{"key": "target", "response": {"project_id": "prj_target", "finding_ids": ["fdg_target"]}}, {"key": "keep", "response": {"project_id": "prj_keep"}}],
    }
    ReviewRepository(data_dir).commit(state)
    source = uploads_dir / "prj_target"; source.mkdir(parents=True); (source / "test.pdf").write_bytes(b"test")
    return data_dir, uploads_dir, state


def patch_paths(monkeypatch, data_dir: Path, uploads_dir: Path) -> None:
    monkeypatch.setattr(cleanup, "configured_paths", lambda: (data_dir, uploads_dir))


def test_dry_run_and_missing_ids_do_not_modify_store(tmp_path, monkeypatch, capsys) -> None:
    data_dir, uploads_dir, _ = make_store(tmp_path); patch_paths(monkeypatch, data_dir, uploads_dir)
    before = (data_dir / "review_data.json").read_bytes()
    assert cleanup.main(["--project-id", "prj_target"]) == 0
    assert "DRY-RUN" in capsys.readouterr().out and (data_dir / "review_data.json").read_bytes() == before
    assert cleanup.main(["--project-id", "prj_target", "--project-id", "prj_missing", "--confirm"]) == 1
    assert (data_dir / "review_data.json").read_bytes() == before and (uploads_dir / "prj_target").exists()


def test_running_task_rejects_whole_batch(tmp_path, monkeypatch) -> None:
    data_dir, uploads_dir, _ = make_store(tmp_path, status="reviewing"); patch_paths(monkeypatch, data_dir, uploads_dir)
    before = (data_dir / "review_data.json").read_bytes()
    assert cleanup.main(["--project-id", "prj_target", "--confirm"]) == 1
    assert (data_dir / "review_data.json").read_bytes() == before


def test_confirm_cascades_backs_up_moves_uploads_and_preserves_other_project(tmp_path, monkeypatch) -> None:
    data_dir, uploads_dir, original = make_store(tmp_path); patch_paths(monkeypatch, data_dir, uploads_dir)
    assert cleanup.main(["--project-id", "prj_target", "--confirm"]) == 0
    state = json.loads((data_dir / "review_data.json").read_text(encoding="utf-8"))
    assert state["projects"] == [original["projects"][1]]
    assert all(not cleanup.contains_id(row, {"prj_target", "prt_target", "fdg_target", "cmt_target", "evt_target"}) for rows in state.values() if isinstance(rows, list) for row in rows)
    backup = next((data_dir / "backups").glob("delete_projects_*"))
    assert (backup / "review_data.json").is_file()
    assert (backup / "uploads" / "prj_target" / "test.pdf").is_file()
    assert not (uploads_dir / "prj_target").exists()


def test_force_allows_explicit_running_test_cleanup(tmp_path, monkeypatch) -> None:
    data_dir, uploads_dir, _ = make_store(tmp_path, status="queued"); patch_paths(monkeypatch, data_dir, uploads_dir)
    assert cleanup.main(["--project-id", "prj_target", "--confirm", "--force"]) == 0
    assert not any(row["id"] == "prj_target" for row in ReviewRepository(data_dir).load()["projects"])
