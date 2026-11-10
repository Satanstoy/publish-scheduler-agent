from __future__ import annotations

from conftest import NOW, single_request
from publish_agent.engines import (
    OpenCodeLocalEngine,
    ToolCall,
    ToolCalls,
    ToolExchange,
    build_local_prompt,
    parse_local_action,
)
from publish_agent.ledger import Ledger
from publish_agent.runner import AgentState
from publish_agent.tools import ToolResult


def test_parse_plain_json_action():
    action = parse_local_action(
        '{"tool":"get_material","arguments":{"material_id":"M00"},"reason":"先读素材"}'
    )
    assert action == ToolCall(
        name="get_material",
        arguments={"material_id": "M00"},
        reason="先读素材",
    )


def test_parse_fenced_json_action():
    text = """```json
{"tool":"finish","arguments":{"summary":"done"},"reason":"全部完成"}
```"""
    action = parse_local_action(text)
    assert action is not None
    assert action.name == "finish"
    assert action.arguments == {"summary": "done"}


def test_parse_invalid_returns_none():
    assert parse_local_action("I will call get_material") is None
    assert parse_local_action('{"arguments":{}}') is None
    assert parse_local_action('{"tool":"x","arguments":[]}') is None


def test_prompt_contains_request_and_observation():
    request = single_request("M00", time="10:00")
    ledger = Ledger()
    ledger.record_material("M00", True, {"id": "M00", "title": "秋日通勤穿搭"}, [])
    exchange = ToolExchange(
        call=ToolCall(name="get_material", arguments={"material_id": "M00"}),
        result=ToolResult(
            ok=True,
            code="OK",
            summary="读取 M00，字段完整",
            data={"material": {"id": "M00"}, "missing_fields": []},
        ),
        elapsed_ms=1,
    )
    prompt = build_local_prompt(request, ledger, [exchange], [])
    assert "REQ-T" in prompt
    assert "M00" in prompt
    assert "读取 M00，字段完整" in prompt
    assert "get_material" in prompt


def test_local_engine_uses_generate_function():
    request = single_request("M00", time="10:00")
    engine = OpenCodeLocalEngine(
        cli_path="/bin/false",
        provider="opencode-go",
        model="deepseek-v4.1-flash",
        schemas=[],
        generate_fn=lambda prompt: '{"tool":"get_material","arguments":{"material_id":"M00"},"reason":"test"}',
    )
    state = AgentState(
        request=request,
        ledger=Ledger(),
        steps=0,
        tool_calls=0,
        transcript=[],
        finished=False,
        stop_reason=None,
    )
    action = engine.next_action(state)
    assert isinstance(action, ToolCalls)
    assert action.calls[0].name == "get_material"
    assert action.calls[0].reason == "test"
