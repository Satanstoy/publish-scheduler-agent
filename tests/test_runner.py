from __future__ import annotations

from conftest import new_store, single_request
from publish_agent.engines import EngineFailure, TextOnly, ToolCalls
from publish_agent.ledger import Ledger
from publish_agent.runner import AgentRunner, Limits
from publish_agent.tools import ToolCall, ToolRegistry


class ScriptedEngine:
    name = "scripted"
    model = "script"
    mode_note = "scripted test engine"
    prompt_hash = "test"

    def __init__(self, actions):
        self.actions = list(actions)
        self.nudges = []

    def next_action(self, state):
        return self.actions.pop(0)

    def record_tool_results(self, exchanges):
        return None

    def record_nudge(self, message):
        self.nudges.append(message)


def _runner(tmp_path, actions, max_steps=10, max_tool_calls=20):
    store = new_store(tmp_path)
    request = single_request("M00", time="10:00")
    ledger = Ledger()
    tools = ToolRegistry(store, request, ledger)
    engine = ScriptedEngine(actions)
    runner = AgentRunner(
        engine=engine,
        tools=tools,
        request=request,
        ledger=ledger,
        limits=Limits(max_steps=max_steps, max_tool_calls=max_tool_calls),
    )
    return runner, store


def _call(name, **arguments):
    return ToolCalls([ToolCall(name=name, arguments=arguments)])


def test_early_finish_is_rejected_and_loop_continues(tmp_path):
    actions = [
        _call("finish", summary="提前结束"),
        _call("get_material", material_id="M00"),
        _call("check_rules", material_id="M00"),
        _call("query_slots", material_id="M00"),
        _call(
            "save_draft",
            material_id="M00",
            date="2026-11-12",
            start="10:00",
            confirmation_status="not_required",
            decision_basis="空闲且无风险",
        ),
        _call("finish", summary="完成"),
    ]
    runner, store = _runner(tmp_path, actions)
    output, trace = runner.run()
    assert output["status"] == "completed"
    assert output["outcome"] == "scheduled"
    assert store.count_drafts() == 1
    tools = [step["tool"] for step in trace["steps"] if step["type"] == "tool_result"]
    assert tools[0] == "finish"
    assert "get_material" in tools
    rejected = trace["steps"][1]
    assert rejected["code"] == "INCOMPLETE_COVERAGE"
    assert rejected["next_action"] == "get_material"


def test_model_error_marks_run_failed(tmp_path):
    runner, store = _runner(tmp_path, [EngineFailure("backend down")])
    output, trace = runner.run()
    assert output["status"] == "failed"
    assert output["outcome"] == "failed"
    assert output["items"][0]["schedule_status"] == "incomplete"
    assert store.count_drafts() == 0
    assert any(step["type"] == "error" for step in trace["steps"])


def test_two_text_only_turns_stop_without_tool_call(tmp_path):
    runner, store = _runner(tmp_path, [TextOnly("hello"), TextOnly("again")])
    output, trace = runner.run()
    assert output["status"] == "incomplete"
    assert output["stop_reason"] == "model_no_tool_call"
    assert store.count_drafts() == 0
    assert len([step for step in trace["steps"] if step["type"] == "assistant_text"]) == 2
