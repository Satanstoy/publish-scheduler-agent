from __future__ import annotations

from types import SimpleNamespace

from conftest import single_request
from publish_agent.engines import OpenAIEngine, TextOnly, ToolCalls
from publish_agent.ledger import Ledger
from publish_agent.runner import AgentState
from publish_agent.tools import ToolResult


def _state():
    return AgentState(
        request=single_request("M00"),
        ledger=Ledger(),
        steps=0,
        tool_calls=0,
        transcript=[],
        finished=False,
        stop_reason=None,
    )


class FakeCompletions:
    def __init__(self, message):
        self.message = message
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(choices=[SimpleNamespace(message=self.message)])


class FakeClient:
    def __init__(self, message):
        self.chat = SimpleNamespace(completions=FakeCompletions(message))


def _tool_message():
    return SimpleNamespace(
        role="assistant",
        content=None,
        tool_calls=[
            SimpleNamespace(
                id="call_1",
                type="function",
                function=SimpleNamespace(
                    name="get_material",
                    arguments='{"material_id":"M00"}',
                ),
            )
        ],
        model_dump=lambda: {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_material", "arguments": '{"material_id":"M00"}'},
                }
            ],
        },
    )


def test_openai_engine_parses_tool_call():
    message = _tool_message()
    client = FakeClient(message)
    engine = OpenAIEngine(
        api_key="test",
        base_url="http://example.invalid/v1",
        model="test-model",
        schemas=[{"type": "function", "function": {"name": "get_material"}}],
        client=client,
    )
    action = engine.next_action(_state())
    assert isinstance(action, ToolCalls)
    assert action.calls[0].name == "get_material"
    assert action.calls[0].arguments == {"material_id": "M00"}
    assert client.chat.completions.last_kwargs["model"] == "test-model"
    assert client.chat.completions.last_kwargs["tools"][0]["function"]["name"] == "get_material"


def test_openai_engine_appends_tool_results():
    client = FakeClient(_tool_message())
    engine = OpenAIEngine(
        api_key="test",
        base_url="http://example.invalid/v1",
        model="test-model",
        schemas=[],
        client=client,
    )
    action = engine.next_action(_state())
    result = ToolResult(ok=True, code="OK", summary="ok", data={"x": 1})
    engine.record_tool_results([type("E", (), {"call": action.calls[0], "result": result})()])
    assert engine.messages[-2]["role"] == "assistant"
    assert engine.messages[-1]["role"] == "tool"
    assert engine.messages[-1]["tool_call_id"] == "call_1"


def test_openai_engine_content_only_returns_text():
    message = SimpleNamespace(
        role="assistant",
        content="I need more data",
        tool_calls=None,
        model_dump=lambda: {"role": "assistant", "content": "I need more data"},
    )
    client = FakeClient(message)
    engine = OpenAIEngine(
        api_key="test",
        base_url="http://example.invalid/v1",
        model="test-model",
        schemas=[],
        client=client,
    )
    action = engine.next_action(_state())
    assert isinstance(action, TextOnly)
    assert "more data" in action.text


def test_openai_engine_marks_invalid_arguments():
    message = SimpleNamespace(
        role="assistant",
        content=None,
        tool_calls=[
            SimpleNamespace(
                id="call_bad",
                type="function",
                function=SimpleNamespace(name="get_material", arguments="{bad json"),
            )
        ],
        model_dump=lambda: {"role": "assistant", "content": None, "tool_calls": []},
    )
    client = FakeClient(message)
    engine = OpenAIEngine(
        api_key="test",
        base_url="http://example.invalid/v1",
        model="test-model",
        schemas=[],
        client=client,
    )
    action = engine.next_action(_state())
    assert isinstance(action, ToolCalls)
    assert action.calls[0].name == "get_material"
    assert "__invalid_json__" in action.calls[0].arguments
