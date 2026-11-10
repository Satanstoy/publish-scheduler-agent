from __future__ import annotations

from conftest import NOW, new_store, single_request
from publish_agent.ledger import Ledger
from publish_agent.tools import ToolRegistry


def _tools(store, request, faults=None):
    return ToolRegistry(store, request, Ledger(), now=NOW, faults=faults)


def _prepare(tools, material_id):
    first = tools.execute("get_material", {"material_id": material_id})
    second = tools.execute("check_rules", {"material_id": material_id})
    third = tools.execute("query_slots", {"material_id": material_id})
    return first, second, third


def _save(tools, material_id, date, start, confirmation="not_required"):
    return tools.execute(
        "save_draft",
        {
            "material_id": material_id,
            "date": date,
            "start": start,
            "confirmation_status": confirmation,
            "decision_basis": "test",
        },
    )


def test_check_rules_requires_material_read(tmp_path):
    store = new_store(tmp_path)
    tools = _tools(store, single_request("M00"))
    result = tools.execute("check_rules", {"material_id": "M00"})
    assert result.ok is False
    assert result.code == "PRECONDITION_FAILED"


def test_query_slots_requires_rules(tmp_path):
    store = new_store(tmp_path)
    tools = _tools(store, single_request("M00"))
    tools.execute("get_material", {"material_id": "M00"})
    result = tools.execute("query_slots", {"material_id": "M00"})
    assert result.ok is False
    assert result.code == "PRECONDITION_FAILED"


def test_missing_material_info_is_not_eligible_for_slots(tmp_path):
    store = new_store(tmp_path)
    tools = _tools(store, single_request("M02", time="14:00"))
    _, rules, slots = _prepare(tools, "M02")
    assert rules.data["risk_level"] == "blocked"
    assert slots.ok is False
    assert slots.code == "NOT_ELIGIBLE"


def test_rule_block_is_not_eligible_for_slots(tmp_path):
    store = new_store(tmp_path)
    tools = _tools(store, single_request("M03", time="15:00"))
    _, rules, slots = _prepare(tools, "M03")
    assert rules.data["risk_level"] == "blocked"
    assert slots.ok is False
    assert slots.code == "NOT_ELIGIBLE"


def test_normal_save_succeeds(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M00", time="10:00")
    tools = _tools(store, request)
    _prepare(tools, "M00")
    result = _save(tools, "M00", "2026-11-12", "10:00")
    assert result.ok is True
    assert result.data["save_status"] == "saved"
    assert result.data["draft"]["confirmation_status"] == "not_required"
    assert result.data["draft"]["published"] is False


def test_hard_constraint_rejects_alternative(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M04", date="2026-11-14", time="18:00", constraint="hard")
    tools = _tools(store, request)
    _, _, slots = _prepare(tools, "M04")
    assert slots.data["alternatives"][0]["start"] == "19:00"
    result = _save(tools, "M04", "2026-11-14", "19:00", "pending_user_confirmation")
    assert result.ok is False
    assert result.code == "HARD_CONSTRAINT_VIOLATION"
    assert store.count_drafts() == 0


def test_soft_conflict_requires_pending_confirmation(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M01", time="18:00")
    tools = _tools(store, request)
    _prepare(tools, "M01")
    rejected = _save(tools, "M01", "2026-11-12", "19:00", "not_required")
    assert rejected.ok is False
    assert rejected.code == "CONFIRMATION_REQUIRED"
    accepted = _save(tools, "M01", "2026-11-12", "19:00", "pending_user_confirmation")
    assert accepted.ok is True
    assert accepted.data["draft"]["slot"]["start"] == "19:00"
    assert accepted.data["draft"]["confirmation_status"] == "pending_user_confirmation"
    assert accepted.data["draft"]["published"] is False


def test_soft_conflict_rejects_unverified_slot(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M01", time="18:00")
    tools = _tools(store, request)
    _prepare(tools, "M01")
    result = _save(tools, "M01", "2026-11-12", "19:15", "pending_user_confirmation")
    assert result.ok is False
    assert result.code == "SLOT_NOT_VERIFIED"


def test_duplicate_save_returns_existing(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M01", time="18:00")
    tools = _tools(store, request)
    _prepare(tools, "M01")
    first = _save(tools, "M01", "2026-11-12", "19:00", "pending_user_confirmation")
    second = _save(tools, "M01", "2026-11-12", "19:00", "pending_user_confirmation")
    assert first.data["save_status"] == "saved"
    assert second.ok is True
    assert second.data["save_status"] == "existing"
    assert second.data["deduplicated"] is True
    assert second.data["draft"]["id"] == first.data["draft"]["id"]
    assert store.count_drafts() == 1


def test_save_failure_returns_save_failed(tmp_path):
    store = new_store(tmp_path, faults={"save_draft"})
    request = single_request("M05", time="21:00")
    tools = _tools(store, request, faults={"save_draft"})
    _prepare(tools, "M05")
    result = _save(tools, "M05", "2026-11-12", "21:00")
    assert result.ok is False
    assert result.code == "SAVE_FAILED"
    assert store.count_drafts() == 0


def test_finish_rejects_unprocessed_items(tmp_path):
    store = new_store(tmp_path)
    tools = _tools(store, single_request("M00"))
    result = tools.execute("finish", {"summary": "done"})
    assert result.ok is False
    assert result.code == "INCOMPLETE_COVERAGE"
    assert result.data["missing"] == ["M00"]


def test_finish_accepts_after_terminal_state(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M00", time="10:00")
    tools = _tools(store, request)
    _prepare(tools, "M00")
    _save(tools, "M00", "2026-11-12", "10:00")
    result = tools.execute("finish", {"summary": "done"})
    assert result.ok is True
    assert result.data["finished"] is True


def test_unknown_tool_is_structured_error(tmp_path):
    store = new_store(tmp_path)
    tools = _tools(store, single_request("M00"))
    result = tools.execute("publish", {"material_id": "M00"})
    assert result.ok is False
    assert result.code == "UNKNOWN_TOOL"
