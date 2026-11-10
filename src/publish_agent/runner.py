from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from uuid import uuid4

from .engines import EngineFailure, TextOnly, ToolCalls, ToolExchange
from .ledger import Ledger
from .models import PublishRequest
from .report import build_report
from .tools import ToolRegistry
from .trace import TraceRecorder


@dataclass(frozen=True)
class Limits:
    max_steps: int = 12
    max_tool_calls: int = 20

    def to_dict(self) -> dict[str, int]:
        return {"max_steps": self.max_steps, "max_tool_calls": self.max_tool_calls}


@dataclass
class AgentState:
    request: PublishRequest
    ledger: Ledger
    steps: int = 0
    tool_calls: int = 0
    transcript: list[ToolExchange] = field(default_factory=list)
    finished: bool = False
    stop_reason: str | None = None
    consecutive_text: int = 0


class AgentRunner:
    def __init__(
        self,
        engine,
        tools: ToolRegistry,
        request: PublishRequest,
        ledger: Ledger,
        limits: Limits | None = None,
        run_id: str | None = None,
    ):
        self.engine = engine
        self.tools = tools
        self.request = request
        self.ledger = ledger
        self.limits = limits or Limits()
        self.run_id = run_id or f"RUN-{request.request_id}-{uuid4().hex[:8].upper()}"

    def run(self) -> tuple[dict, dict]:
        trace = TraceRecorder(
            run_id=self.run_id,
            engine=self.engine.name,
            model=self.engine.model,
            request=self.request.to_dict(),
            limits=self.limits.to_dict(),
            prompt_hash=self.engine.prompt_hash,
        )
        state = AgentState(request=self.request, ledger=self.ledger)
        stop_reason: str | None = None

        while state.steps < self.limits.max_steps:
            if state.tool_calls >= self.limits.max_tool_calls:
                stop_reason = "tool_limit"
                break
            try:
                action = self.engine.next_action(state)
            except Exception as exc:  # model/network boundary
                trace.error(f"模型调用失败：{exc}")
                stop_reason = "model_error"
                break

            if isinstance(action, EngineFailure):
                trace.error(action.message)
                stop_reason = "model_error"
                break

            if isinstance(action, TextOnly):
                state.steps += 1
                state.consecutive_text += 1
                trace.text_only(state.steps, action.text)
                if state.consecutive_text >= 2:
                    stop_reason = "model_no_tool_call"
                    break
                self.engine.record_nudge(
                    "请只输出一个 JSON 工具调用，或调用 finish；不要输出解释文字。"
                )
                continue

            if not isinstance(action, ToolCalls) or not action.calls:
                stop_reason = "model_no_tool_call"
                break

            state.steps += 1
            state.consecutive_text = 0
            calls = action.calls
            for offset, call in enumerate(calls):
                if not call.id:
                    call.id = f"call_{state.tool_calls + offset + 1}"
            trace.model_decision(state.steps, action.assistant_text, calls)

            exchanges: list[ToolExchange] = []
            limit_hit = False
            for call in calls:
                if state.tool_calls >= self.limits.max_tool_calls:
                    limit_hit = True
                    break
                started = perf_counter()
                result = self.tools.execute(call.name, call.arguments)
                elapsed_ms = max(0, int((perf_counter() - started) * 1000))
                state.tool_calls += 1
                exchange = ToolExchange(call=call, result=result, elapsed_ms=elapsed_ms)
                state.transcript.append(exchange)
                exchanges.append(exchange)
                trace.tool_result(state.steps, call, result, elapsed_ms)
                if call.name == "finish" and result.ok:
                    state.finished = True
                    break

            try:
                self.engine.record_tool_results(exchanges)
            except Exception as exc:  # keep the run auditable even if the engine fails
                trace.error(f"模型会话回填失败：{exc}")
                stop_reason = "model_error"
                break

            if state.finished:
                stop_reason = "finished"
                break
            if limit_hit:
                stop_reason = "tool_limit"
                break
        else:
            stop_reason = "step_limit"

        if stop_reason is None:
            stop_reason = "step_limit"

        if state.finished:
            status = "completed"
        elif stop_reason == "model_error":
            status = "failed"
        else:
            status = "incomplete"

        run_meta = {
            "run_id": self.run_id,
            "engine": self.engine.name,
            "model": self.engine.model,
            "mode_note": self.engine.mode_note,
            "status": status,
            "stop_reason": stop_reason,
            "limits": {
                "max_steps": self.limits.max_steps,
                "max_tool_calls": self.limits.max_tool_calls,
                "used_steps": state.steps,
                "used_tool_calls": state.tool_calls,
            },
        }
        output = build_report(self.request, self.ledger, run_meta)
        trace.finalize(output)
        return output, trace.to_dict()
