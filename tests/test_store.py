from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from conftest import NOW, new_store, single_request
from publish_agent.models import build_request_key
from publish_agent.store import DraftConflictError, InjectedSaveError


def _draft(store, request, start: str = "19:00", end: str = "20:00"):
    item = request.item_for("M01")
    key = build_request_key(
        item.material_id,
        store.materials["M01"]["content_version"],
        item.date,
        item.time,
        item.constraint,
        item.channel,
    )
    draft = {
        "id": "DRF-STORE00001",
        "request_key": key,
        "material_id": "M01",
        "content_version": store.materials["M01"]["content_version"],
        "slot": {"date": item.date, "start": start, "end": end, "timezone": "Asia/Shanghai"},
        "requested_slot": {
            "date": item.date,
            "start": item.time,
            "end": "19:00",
            "constraint": item.constraint,
        },
        "status": "draft",
        "confirmation_status": "pending_user_confirmation",
        "risks": [],
        "pending_items": [{"code": "ALTERNATIVE_SLOT", "message": "test"}],
        "decision_basis": "test",
        "created_at": NOW.isoformat(),
        "published": False,
    }
    return draft, key


def test_create_and_duplicate_returns_existing(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M01", time="18:00")
    draft, key = _draft(store, request)
    first, created = store.create_draft(draft, key)
    second, created_again = store.create_draft(draft, key)
    assert created is True
    assert created_again is False
    assert first["id"] == second["id"]
    assert store.count_drafts() == 1
    assert json.loads((tmp_path / "state" / "drafts.json").read_text())["version"] == 1


def test_fault_injection_raises_and_writes_nothing(tmp_path):
    store = new_store(tmp_path, faults={"save_draft"})
    request = single_request("M01", time="18:00")
    draft, key = _draft(store, request)
    with pytest.raises(InjectedSaveError):
        store.create_draft(draft, key)
    assert store.count_drafts() == 0


def test_confirm_marks_user_confirmed_but_not_published(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M01", time="18:00")
    draft, key = _draft(store, request)
    store.create_draft(draft, key)
    confirmed = store.confirm_draft(draft["id"])
    assert confirmed["confirmation_status"] == "user_confirmed"
    assert confirmed["status"] == "draft"
    assert confirmed["published"] is False
    assert confirmed["confirmed_at"]
    again = store.confirm_draft(draft["id"])
    assert again["confirmation_status"] == "user_confirmed"


def test_confirm_conflict_leaves_draft_pending(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M01", time="18:00")
    draft, key = _draft(store, request)
    store.create_draft(draft, key)
    store.schedule_entries.append(
        {
            "id": "S-NEW",
            "date": "2026-11-12",
            "start": "19:00",
            "end": "20:00",
            "channel": "wechat",
            "material_id": "M99",
            "status": "scheduled",
        }
    )
    with pytest.raises(DraftConflictError):
        store.confirm_draft(draft["id"])
    assert store.get_draft(draft["id"])["confirmation_status"] == "pending_user_confirmation"


def test_thread_concurrent_same_request_creates_one_draft(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M01", time="18:00")
    draft, key = _draft(store, request)

    def save():
        return store.create_draft(draft, key)[1]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: save(), range(8)))
    assert results.count(True) == 1
    assert store.count_drafts() == 1


def test_busy_entries_excludes_same_request_key(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M01", time="18:00")
    draft, key = _draft(store, request)
    store.create_draft(draft, key)
    busy = store.busy_entries("2026-11-12", "wechat", exclude_request_key=key)
    starts = [entry["start"] for entry in busy]
    assert "19:00" not in starts
    assert "18:00" in starts
