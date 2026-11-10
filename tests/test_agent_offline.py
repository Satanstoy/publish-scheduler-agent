from __future__ import annotations

from conftest import item, make_request, new_store, run_offline, single_request


def _tool_sequence(trace: dict) -> list[str]:
    return [step["tool"] for step in trace["steps"] if step["type"] == "tool_result"]


def test_normal_schedule(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M00", time="10:00")
    output, trace = run_offline(request, store)
    item = output["items"][0]
    assert output["status"] == "completed"
    assert output["outcome"] == "scheduled"
    assert output["published"] is False
    assert item["review_status"] == "passed"
    assert item["schedule_status"] == "drafted"
    assert item["suggested_slot"]["start"] == "10:00"
    assert item["confirmation_status"] == "not_required"
    assert item["pending_items"] == []
    assert item["draft"]["save_status"] == "saved"
    assert item["draft"]["id"].startswith("DRF-")
    assert item["draft"]["published"] is False
    assert store.count_drafts() == 1
    assert _tool_sequence(trace) == [
        "get_material",
        "check_rules",
        "query_slots",
        "save_draft",
        "finish",
    ]


def test_missing_material_info(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M02", time="14:00")
    output, trace = run_offline(request, store)
    item = output["items"][0]
    assert output["outcome"] == "needs_attention"
    assert item["review_status"] == "needs_input"
    assert item["schedule_status"] == "not_scheduled"
    assert item["draft"]["save_status"] == "not_attempted"
    assert any(risk["code"] == "MISSING_REQUIRED_FIELD" for risk in item["risks"])
    assert set(item["missing_fields"]) == {"duration_minutes", "metadata.copyright_owner"}
    assert "query_slots" not in _tool_sequence(trace)
    assert "save_draft" not in _tool_sequence(trace)
    assert store.count_drafts() == 0


def test_rule_risk_blocks(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M03", time="15:00")
    output, trace = run_offline(request, store)
    item = output["items"][0]
    assert output["outcome"] == "needs_attention"
    assert item["review_status"] == "blocked"
    assert item["schedule_status"] == "not_scheduled"
    assert any(risk["code"] == "FORBIDDEN_TERM" for risk in item["risks"])
    assert item["draft"]["save_status"] == "not_attempted"
    assert "query_slots" not in _tool_sequence(trace)
    assert "save_draft" not in _tool_sequence(trace)
    assert store.count_drafts() == 0


def test_soft_conflict_saves_pending_alternative(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M01", time="18:00")
    output, trace = run_offline(request, store)
    item = output["items"][0]
    assert output["outcome"] == "needs_confirmation"
    assert item["review_status"] == "passed"
    assert item["schedule_status"] == "pending_confirmation"
    assert item["suggested_slot"]["start"] == "19:00"
    assert item["confirmation_status"] == "pending_user_confirmation"
    assert item["draft"]["save_status"] == "saved"
    assert item["draft"]["id"].startswith("DRF-")
    assert item["published"] is False
    assert any(p["code"] == "ALTERNATIVE_SLOT" for p in item["pending_items"])
    assert "19:00" in item["decision_basis"]
    assert "publish" not in " ".join(_tool_sequence(trace)).lower()
    assert store.count_drafts() == 1


def test_hard_conflict_does_not_save_draft(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M04", date="2026-11-14", time="18:00", constraint="hard")
    output, trace = run_offline(request, store)
    item = output["items"][0]
    assert output["outcome"] == "needs_attention"
    assert item["schedule_status"] == "blocked_conflict"
    assert item["suggested_slot"]["start"] == "19:00"
    assert item["requested_slot"]["start"] == "18:00"
    assert item["confirmation_status"] == "blocked"
    assert item["draft"]["save_status"] == "not_attempted"
    assert item["published"] is False
    assert "query_slots" in _tool_sequence(trace)
    assert "save_draft" not in _tool_sequence(trace)
    assert store.count_drafts() == 0


def test_duplicate_submission_creates_one_draft(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M01", time="18:00")
    first, _ = run_offline(request, store)
    second, trace = run_offline(request, store)
    first_draft = first["items"][0]["draft"]
    second_draft = second["items"][0]["draft"]
    assert first_draft["save_status"] == "saved"
    assert second_draft["save_status"] == "existing"
    assert second_draft["deduplicated"] is True
    assert second_draft["id"] == first_draft["id"]
    assert store.count_drafts() == 1
    assert "save_draft" in _tool_sequence(trace)


def test_duplicate_ignores_request_id(tmp_path):
    store = new_store(tmp_path)
    first_request = single_request("M01", time="18:00")
    second_request = make_request([item("M01", time="18:00")], request_id="REQ-DIFFERENT")
    first, _ = run_offline(first_request, store)
    second, _ = run_offline(second_request, store)
    assert second["items"][0]["draft"]["id"] == first["items"][0]["draft"]["id"]
    assert second["items"][0]["draft"]["save_status"] == "existing"
    assert store.count_drafts() == 1


def test_save_failure_is_reported_without_draft(tmp_path):
    store = new_store(tmp_path, faults={"save_draft"})
    request = single_request("M05", time="21:00")
    output, trace = run_offline(request, store)
    item = output["items"][0]
    assert output["outcome"] == "needs_attention"
    assert item["schedule_status"] == "save_failed"
    assert item["draft"]["save_status"] == "failed"
    assert item["draft"]["id"] is None
    assert item["error"]["code"] == "SAVE_FAILED"
    assert "失败" in item["decision_basis"]
    assert item["published"] is False
    assert store.count_drafts() == 0
    assert "save_draft" in _tool_sequence(trace)


def test_soft_rule_warning_requires_confirmation(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M06", date="2026-11-15", time="10:00")
    output, _ = run_offline(request, store)
    item = output["items"][0]
    assert output["outcome"] == "needs_confirmation"
    assert item["review_status"] == "warning"
    assert item["schedule_status"] == "pending_confirmation"
    assert item["confirmation_status"] == "pending_user_confirmation"
    assert any(risk["code"] == "LONG_DURATION_REVIEW" for risk in item["risks"])
    assert item["draft"]["save_status"] == "saved"
    assert store.count_drafts() == 1


def test_step_limit_returns_incomplete(tmp_path):
    store = new_store(tmp_path)
    output, trace = run_offline(single_request("M00"), store, max_steps=1)
    assert output["status"] == "incomplete"
    assert output["outcome"] == "incomplete"
    assert output["stop_reason"] == "step_limit"
    assert output["limits"]["used_steps"] == 1
    assert output["items"][0]["schedule_status"] == "incomplete"
    assert output["items"][0]["published"] is False
    assert output["items"][0]["draft"]["save_status"] == "not_attempted"
    assert store.count_drafts() == 0
    assert trace["outcome"]["status"] == "incomplete"


def test_tool_limit_returns_incomplete(tmp_path):
    store = new_store(tmp_path)
    output, _ = run_offline(single_request("M00"), store, max_tool_calls=2)
    assert output["status"] == "incomplete"
    assert output["outcome"] == "incomplete"
    assert output["stop_reason"] == "tool_limit"
    assert output["limits"]["used_tool_calls"] == 2
    assert output["items"][0]["schedule_status"] == "incomplete"


def test_batch_two_materials(tmp_path):
    store = new_store(tmp_path)
    request = make_request(
        [
            item("M00", time="10:00"),
            item("M01", time="18:00"),
        ],
        request_id="REQ-BATCH",
    )
    output, trace = run_offline(request, store)
    assert output["status"] == "completed"
    assert output["outcome"] == "needs_confirmation"
    assert [entry["material_id"] for entry in output["items"]] == ["M00", "M01"]
    assert output["items"][0]["schedule_status"] == "drafted"
    assert output["items"][1]["schedule_status"] == "pending_confirmation"
    assert store.count_drafts() == 2
    assert output["limits"]["used_steps"] <= output["limits"]["max_steps"]


def test_no_publish_tool_and_no_published_output(tmp_path):
    from publish_agent.tools import ToolRegistry
    from publish_agent.ledger import Ledger

    store = new_store(tmp_path)
    request = single_request("M00")
    tools = ToolRegistry(store, request, Ledger())
    names = {schema["function"]["name"] for schema in tools.schemas}
    assert "publish" not in names
    output, _ = run_offline(request, store)
    assert output["published"] is False
    assert output["items"][0]["published"] is False
