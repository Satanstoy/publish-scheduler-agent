from __future__ import annotations

from typing import Any

from .tools import ToolCall, ToolResult


class TraceRecorder:
    def __init__(
        self,
        run_id: str,
        engine: str,
        model: str,
        request: dict[str, Any],
        limits: dict[str, int],
        prompt_hash: str = "",
    ):
        self.run_id = run_id
        self.engine = engine
        self.model = model
        self.request = request
        self.limits = limits
        self.prompt_hash = prompt_hash
        self.steps: list[dict[str, Any]] = []
        self.data: dict[str, Any] | None = None

    def _append(self, step: dict[str, Any]) -> None:
        step["index"] = len(self.steps) + 1
        self.steps.append(step)

    def model_decision(
        self,
        model_step: int,
        assistant_text: str,
        calls: list[ToolCall],
    ) -> None:
        self._append(
            {
                "type": "model_decision",
                "model_step": model_step,
                "assistant_text": assistant_text,
                "tool_calls": [
                    {
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "reason": call.reason,
                    }
                    for call in calls
                ],
            }
        )

    def tool_result(
        self,
        model_step: int,
        call: ToolCall,
        result: ToolResult,
        elapsed_ms: int,
    ) -> None:
        self._append(
            {
                "type": "tool_result",
                "model_step": model_step,
                "tool_call_id": call.id,
                "tool": call.name,
                "arguments": call.arguments,
                "ok": result.ok,
                "code": result.code,
                "summary": result.summary,
                "data": result.data,
                "error": result.error,
                "elapsed_ms": elapsed_ms,
                "next_action": None,
            }
        )

    def text_only(self, model_step: int, text: str) -> None:
        self._append(
            {
                "type": "assistant_text",
                "model_step": model_step,
                "text": text,
            }
        )

    def error(self, message: str, stage: str = "model") -> None:
        self._append(
            {
                "type": "error",
                "stage": stage,
                "message": message,
            }
        )

    def _fill_next_actions(self) -> None:
        for position, step in enumerate(self.steps):
            if step.get("type") != "tool_result":
                continue
            if step.get("tool") == "finish" and step.get("ok"):
                step["next_action"] = "end"
                continue
            next_action = "end"
            for later in self.steps[position + 1 :]:
                if later.get("type") == "tool_result":
                    next_action = later.get("tool", "end")
                    break
                if later.get("type") == "model_decision":
                    calls = later.get("tool_calls") or []
                    next_action = calls[0]["name"] if calls else "end"
                    break
                if later.get("type") == "assistant_text":
                    next_action = "nudge"
                    break
            step["next_action"] = next_action

    def finalize(self, output: dict[str, Any]) -> None:
        self._fill_next_actions()
        self.data = {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "engine": self.engine,
            "model": self.model,
            "request": self.request,
            "limits": self.limits,
            "system_prompt_sha256": self.prompt_hash,
            "steps": self.steps,
            "outcome": {
                "status": output["status"],
                "stop_reason": output["stop_reason"],
                "published": output["published"],
                "summary": output["summary"],
                "proposed_schedule": output["proposed_schedule"],
            },
        }

    def to_dict(self) -> dict[str, Any]:
        if self.data is None:
            raise RuntimeError("trace has not been finalized")
        return self.data
