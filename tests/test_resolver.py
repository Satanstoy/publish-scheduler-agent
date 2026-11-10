from __future__ import annotations

from conftest import NOW, item, make_request, new_store, run_offline, single_request
from publish_agent.resolver import classify_message, resolve_confirmation


def _one_pending(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M01", time="18:00")
    run_offline(request, store)
    return store


def test_classify_affirmative_negative_unknown():
    assert classify_message("可以") == "affirm"
    assert classify_message("没问题，继续") == "affirm"
    assert classify_message("取消") == "decline"
    assert classify_message("可以但不要") == "unknown"
    assert classify_message("再说吧") == "unknown"
    assert classify_message("可以，但改成20:00") == "change"
    assert classify_message("20:00 可以") == "change"


def test_affirmative_confirms_only_pending_draft(tmp_path):
    store = _one_pending(tmp_path)
    result = resolve_confirmation("可以", store, now=NOW)
    assert result["action"] == "confirmed"
    assert result["published"] is False
    assert result["draft"]["confirmation_status"] == "user_confirmed"
    assert result["draft"]["status"] == "draft"
    assert store.pending_drafts() == []


def test_continue_and_ok_are_affirmative_when_unambiguous(tmp_path):
    for message in ["继续", "没问题", "OK"]:
        store = new_store(tmp_path / message)
        run_offline(single_request("M01", time="18:00"), store)
        result = resolve_confirmation(message, store, now=NOW)
        assert result["action"] == "confirmed"
        assert result["published"] is False


def test_decline_keeps_pending(tmp_path):
    store = _one_pending(tmp_path)
    result = resolve_confirmation("取消", store, now=NOW)
    assert result["action"] == "declined"
    assert result["published"] is False
    assert len(store.pending_drafts()) == 1


def test_unknown_message_asks_for_clarification(tmp_path):
    store = _one_pending(tmp_path)
    result = resolve_confirmation("再说吧", store, now=NOW)
    assert result["action"] == "needs_clarification"
    assert len(store.pending_drafts()) == 1


def test_conditional_consent_is_not_auto_confirmed(tmp_path):
    store = _one_pending(tmp_path)
    result = resolve_confirmation("可以，但改成20:00", store, now=NOW)
    assert result["action"] == "needs_clarification"
    assert len(store.pending_drafts()) == 1


def test_multiple_pending_never_confirms_all_with_one_word(tmp_path):
    store = new_store(tmp_path)
    request = make_request(
        [
            item("M01", time="18:00"),
            item("M06", date="2026-11-15", time="10:00"),
        ],
        request_id="REQ-MULTI-PENDING",
    )
    run_offline(request, store)
    assert len(store.pending_drafts()) == 2
    result = resolve_confirmation("可以", store, now=NOW)
    assert result["action"] == "needs_clarification"
    assert len(result["pending"]) == 2
    assert len(store.pending_drafts()) == 2


def test_no_pending_does_not_change_hard_constraint(tmp_path):
    store = new_store(tmp_path)
    run_offline(
        single_request("M04", date="2026-11-14", time="18:00", constraint="hard"),
        store,
    )
    assert store.count_drafts() == 0
    result = resolve_confirmation("可以", store, now=NOW)
    assert result["action"] == "no_pending"
    assert result["published"] is False
    assert store.count_drafts() == 0


def test_confirmation_conflict_keeps_pending(tmp_path):
    store = _one_pending(tmp_path)
    store.schedule_entries.append(
        {
            "id": "S-NEW-CONFIRM",
            "date": "2026-11-12",
            "start": "19:00",
            "end": "20:00",
            "channel": "wechat",
            "material_id": "M99",
            "status": "scheduled",
        }
    )
    result = resolve_confirmation("可以", store, now=NOW)
    assert result["action"] == "confirm_conflict"
    assert result["ok"] is False
    assert len(store.pending_drafts()) == 1
