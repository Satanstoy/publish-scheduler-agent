from __future__ import annotations

import pytest

from publish_agent.models import (
    PublishRequest,
    RequestValidationError,
    add_minutes,
    build_draft_id,
    build_request_key,
    missing_fields,
)


def test_request_roundtrip_and_item_lookup():
    request = PublishRequest.from_dict(
        {
            "request_id": "REQ-1",
            "submitted_at": "2026-11-10T09:00:00+08:00",
            "items": [
                {
                    "material_id": "M01",
                    "date": "2026-11-12",
                    "time": "18:00",
                    "constraint": "preferred",
                }
            ],
        }
    )
    assert request.request_id == "REQ-1"
    assert request.item_for("M01").constraint == "preferred"
    assert request.to_dict()["items"][0]["channel"] == "wechat"


def test_request_rejects_duplicate_material_ids():
    with pytest.raises(RequestValidationError, match="duplicate"):
        PublishRequest.from_dict(
            {
                "request_id": "REQ-1",
                "items": [
                    {"material_id": "M01", "date": "2026-11-12", "time": "10:00", "constraint": "preferred"},
                    {"material_id": "M01", "date": "2026-11-12", "time": "11:00", "constraint": "preferred"},
                ],
            }
        )


def test_request_rejects_more_than_five_items():
    items = [
        {"material_id": f"M{i}", "date": "2026-11-12", "time": "10:00", "constraint": "preferred"}
        for i in range(6)
    ]
    with pytest.raises(RequestValidationError, match="at most 5"):
        PublishRequest.from_dict({"request_id": "REQ-1", "items": items})


def test_request_rejects_invalid_constraint():
    with pytest.raises(RequestValidationError, match="constraint"):
        PublishRequest.from_dict(
            {
                "request_id": "REQ-1",
                "items": [
                    {"material_id": "M01", "date": "2026-11-12", "time": "10:00", "constraint": "flexible"}
                ],
            }
        )


def test_missing_fields_uses_dotted_paths():
    material = {
        "title": "x",
        "metadata": {"authorized": True, "language": "zh-CN"},
    }
    fields = missing_fields(
        material,
        ["title", "duration_minutes", "metadata.authorized", "metadata.copyright_owner"],
    )
    assert fields == ["duration_minutes", "metadata.copyright_owner"]


def test_request_key_changes_with_content_version_and_ignores_request_id():
    base = build_request_key("M01", "v1", "2026-11-12", "18:00", "preferred", "wechat")
    same = build_request_key("M01", "v1", "2026-11-12", "18:00", "preferred", "wechat")
    changed = build_request_key("M01", "v2", "2026-11-12", "18:00", "preferred", "wechat")
    assert base == same
    assert base != changed
    assert base.startswith("RQ-")
    assert build_draft_id(base).startswith("DRF-")
    assert build_draft_id(base) == build_draft_id(base)


def test_add_minutes_handles_cross_hour():
    assert add_minutes("18:00", 60) == "19:00"
    assert add_minutes("21:30", 30) == "22:00"
    assert add_minutes("09:45", 30) == "10:15"
