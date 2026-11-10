from __future__ import annotations

from conftest import NOW, new_store, single_request
from publish_agent.models import build_request_key
from publish_agent.slots import query_slots


def _insert_draft(store, request, start: str, request_key: str | None = None):
    req_item = request.item_for("M01")
    key = request_key or build_request_key(
        req_item.material_id,
        store.materials["M01"]["content_version"],
        req_item.date,
        req_item.time,
        req_item.constraint,
        req_item.channel,
    )
    draft = {
        "id": "DRF-TEST000001",
        "request_key": key,
        "material_id": "M01",
        "content_version": store.materials["M01"]["content_version"],
        "slot": {
            "date": req_item.date,
            "start": start,
            "end": "20:00",
            "timezone": "Asia/Shanghai",
        },
        "requested_slot": {
            "date": req_item.date,
            "start": req_item.time,
            "end": "19:00",
            "constraint": req_item.constraint,
        },
        "status": "draft",
        "confirmation_status": "pending_user_confirmation",
        "risks": [],
        "pending_items": [],
        "decision_basis": "test",
        "created_at": NOW.isoformat(),
        "published": False,
    }
    stored, created = store.create_draft(draft, key)
    assert created
    return stored, key


def test_no_conflict_returns_requested_slot(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M00", time="10:00")
    result = query_slots(
        store.materials["M00"],
        request.item_for("M00"),
        store.rules,
        store,
        NOW,
    )
    assert result["conflict"] is False
    assert result["conflicts"] == []
    assert result["alternatives"] == []
    assert result["requested"]["start"] == "10:00"


def test_conflict_suggests_nearest_alternative(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M01", time="18:00")
    result = query_slots(
        store.materials["M01"],
        request.item_for("M01"),
        store.rules,
        store,
        NOW,
    )
    assert result["conflict"] is True
    assert result["conflicts"][0]["entry_id"] == "S-001"
    assert result["alternatives"][0]["start"] == "19:00"
    assert result["alternatives"][0]["end"] == "20:00"


def test_alternative_respects_duration(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M05", time="18:00")
    result = query_slots(
        store.materials["M05"],
        request.item_for("M05"),
        store.rules,
        store,
        NOW,
    )
    assert result["conflict"] is True
    assert result["alternatives"][0]["start"] == "19:00"
    assert result["alternatives"][0]["end"] == "19:30"


def test_same_request_draft_is_excluded_from_conflicts(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M01", time="18:00")
    _, key = _insert_draft(store, request, start="19:00")
    result = query_slots(
        store.materials["M01"],
        request.item_for("M01"),
        store.rules,
        store,
        NOW,
    )
    assert result["existing_draft"]["request_key"] == key
    assert result["alternatives"][0]["start"] == "19:00"


def test_no_alternative_when_window_is_full(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M00", time="18:00")
    rules = dict(store.rules)
    rules["working_hours"] = {"start": "18:00", "end": "19:00"}
    store.schedule_entries = [
        {
            "id": "S-FULL",
            "date": "2026-11-12",
            "start": "18:00",
            "end": "19:00",
            "channel": "wechat",
            "material_id": "M90",
            "status": "scheduled",
        }
    ]
    result = query_slots(store.materials["M00"], request.item_for("M00"), rules, store, NOW)
    assert result["conflict"] is True
    assert result["alternatives"] == []
