from app.services.procurement.review import ProcurementReviewService


def test_finding_output_keeps_pending_separate_from_recognition_unknown() -> None:
    service = object.__new__(ProcurementReviewService)
    service.comments = type("Comments", (), {"read": lambda self: {"items": []}})()

    pending = service._finding_out({"id": "f1", "risk_level": "pending"})
    unknown = service._finding_out({"id": "f2", "risk_level": "unknown"})
    invalid = service._finding_out({"id": "f3", "risk_level": "unexpected"})

    assert pending["risk_level"] == "pending"
    assert unknown["risk_level"] == "unknown"
    assert invalid["risk_level"] == "pending"
