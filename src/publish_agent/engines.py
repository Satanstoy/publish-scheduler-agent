from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Callable, TYPE_CHECKING

from .prompts import (
    LOCAL_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_local_prompt,
    build_user_prompt,
)
from .tools import TOOL_SCHEMAS, ToolCall, ToolResult

if TYPE_CHECKING:
    from .runner import AgentState


class ModelError(RuntimeError):
    """Raised when the model backend cannot produce a decision."""


@dataclass
class ToolCalls:
    calls: list[ToolCall]
    assistant_text: str = ""


@dataclass
class TextOnly:
    text: str


@dataclass
class EngineFailure:
    message: str


@dataclass
class ToolExchange:
    call: ToolCall
    result: ToolResult
    elapsed_ms: int = 0


class OfflineEngine:
    name = "offline-mock"
    model = "reference-policy"
    mode_note = "离线 Mock 仅验证流程与护栏，不代表真实模型决策效果。"
    prompt_hash = "offline-reference-policy"

    def next_action(self, state: "AgentState") -> ToolCalls:
        for item in state.request.items:
            if state.ledger.terminal_status(item.material_id, item) is not None:
                continue
            entry = state.ledger.get(item.material_id)
            if entry.material_found is None:
                return self._call(
                    "get_material",
                    {"material_id": item.material_id},
                    "先读取素材元数据，确认字段是否完整。",
                )
            if entry.rules is None:
                return self._call(
                    "check_rules",
                    {"material_id": item.material_id},
                    "检查版权、禁词、日期和时长规则。",
                )
            if entry.slots is None:
                return self._call(
                    "query_slots",
                    {"material_id": item.material_id},
                    "查询请求时段是否冲突，并获取备选。",
                )
            if entry.save_result is None:
                slots = entry.slots
                if slots.get("conflict"):
                    if item.constraint == "hard" or not slots.get("alternatives"):
                        continue
                    target = slots["alternatives"][0]["start"]
                    return self._call(
                        "save_draft",
                        {
                            "material_id": item.material_id,
                            "date": item.date,
                            "start": target,
                            "confirmation_status": "pending_user_confirmation",
                            "decision_basis": (
                                f"请求 {item.time} 冲突，选择同日备选 {target}，需用户确认。"
                            ),
                        },
                        f"请求时段冲突，选择备选 {target} 并标记待确认。",
                    )
                warning = entry.rules.get("risk_level") == "warning"
                return self._call(
                    "save_draft",
                    {
                        "material_id": item.material_id,
                        "date": item.date,
                        "start": item.time,
                        "confirmation_status": (
                            "pending_user_confirmation" if warning else "not_required"
                        ),
                        "decision_basis": (
                            "规则命中软警告，按请求时段保存草稿并待确认。"
                            if warning
                            else "请求时段无冲突，规则无风险，保存草稿。"
                        ),
                    },
                    "时段可用，保存草稿。",
                )
        return self._call(
            "finish",
            {"summary": "全部素材已处理；仅保存草稿，未发布。"},
            "所有素材已有终态。",
        )

    def _call(self, name: str, arguments: dict[str, Any], reason: str) -> ToolCalls:
        return ToolCalls([ToolCall(name=name, arguments=arguments, reason=reason)])

    def record_tool_results(self, exchanges: list[ToolExchange]) -> None:
        return None

    def record_nudge(self, message: str) -> None:
        return None


class OpenAIEngine:
    name = "live-openai"
    mode_note = "真实模型（OpenAI-compatible）决策；结果仍需人工确认。"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        schemas: list[dict[str, Any]] | None = None,
        client: Any | None = None,
    ):
        self.model = model
        self.schemas = schemas or TOOL_SCHEMAS
        self.prompt_hash = sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
        if client is not None:
            self.client = client
        else:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - dependency is declared
                raise ModelError("需要安装 openai：pip install -r requirements.txt") from exc
            self.client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=90,
                max_retries=1,
            )
        self.messages: list[dict[str, Any]] = []
        self._initialized = False

    def _ensure_messages(self, request) -> None:
        if self._initialized:
            return
        self.messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(request)},
        ]
        self._initialized = True

    def next_action(self, state: "AgentState") -> ToolCalls | TextOnly | EngineFailure:
        self._ensure_messages(state.request)
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                tools=self.schemas,
                tool_choice="auto",
                temperature=0,
                max_tokens=1200,
            )
        except Exception as exc:
            return EngineFailure(f"模型调用失败：{exc}")

        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            assistant = (
                message.model_dump()
                if hasattr(message, "model_dump")
                else {
                    "role": "assistant",
                    "content": getattr(message, "content", None),
                    "tool_calls": tool_calls,
                }
            )
            self.messages.append(assistant)
            calls: list[ToolCall] = []
            for tool_call in tool_calls:
                raw = getattr(tool_call.function, "arguments", "") or "{}"
                try:
                    arguments = json.loads(raw)
                    if not isinstance(arguments, dict):
                        arguments = {"__invalid_json__": raw}
                except json.JSONDecodeError:
                    arguments = {"__invalid_json__": raw}
                reason = ""
                if isinstance(arguments, dict) and isinstance(arguments.get("reason"), str):
                    reason = arguments.pop("reason")
                calls.append(
                    ToolCall(
                        name=tool_call.function.name,
                        arguments=arguments,
                        reason=reason,
                        id=getattr(tool_call, "id", "") or "",
                    )
                )
            return ToolCalls(calls=calls, assistant_text=getattr(message, "content", None) or "")
        text = getattr(message, "content", None) or ""
        self.messages.append({"role": "assistant", "content": text})
        return TextOnly(text)

    def record_tool_results(self, exchanges: list[ToolExchange]) -> None:
        for exchange in exchanges:
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": exchange.call.id,
                    "content": json.dumps(exchange.result.to_dict(), ensure_ascii=False),
                }
            )

    def record_nudge(self, message: str) -> None:
        self.messages.append({"role": "user", "content": message})


class OpenCodeLocalEngine:
    name = "live-opencode-local"
    mode_note = "真实模型（本机 OpenCode 实验性 generate/text）；结果仍需人工确认。"

    def __init__(
        self,
        cli_path: str | None,
        provider: str,
        model: str,
        schemas: list[dict[str, Any]] | None = None,
        generate_fn: Callable[[str], str] | None = None,
    ):
        self.cli_path = cli_path
        self.provider = provider
        self.model_id = model
        self.model = f"{provider}/{model}"
        self.schemas = schemas or TOOL_SCHEMAS
        self.generate_fn = generate_fn
        self.prompt_hash = sha256(LOCAL_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
        self.nudges: list[str] = []

    def next_action(self, state: "AgentState") -> ToolCalls | TextOnly | EngineFailure:
        prompt = build_local_prompt(
            state.request,
            state.ledger,
            state.transcript,
            self.nudges,
            self.schemas,
        )
        try:
            text = self._generate(prompt)
        except Exception as exc:
            return EngineFailure(f"本地模型调用失败：{exc}")
        action = parse_local_action(text)
        if action is None:
            return TextOnly(text)
        return ToolCalls([action], assistant_text=text)

    def _generate(self, prompt: str) -> str:
        if self.generate_fn is not None:
            return self.generate_fn(prompt)
        if not self.cli_path:
            raise ModelError(
                "未配置 OPENCODE_CLI_PATH；无法调用本机 OpenCode generate/text。"
            )
        payload = {
            "prompt": prompt,
            "model": {"id": self.model_id, "providerID": self.provider},
        }
        command = [
            self.cli_path,
            "api",
            "post",
            "/api/experimental/generate/text",
            "-d",
            json.dumps(payload, ensure_ascii=False),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=180,
        )
        output = completed.stdout.strip()
        if completed.returncode != 0:
            message = output or completed.stderr.strip()
            raise ModelError(f"OpenCode 返回非零退出码：{message[:500]}")
        parsed = extract_json_object(output)
        if parsed is None:
            raise ModelError(f"OpenCode 输出不是 JSON：{output[:500]}")
        if isinstance(parsed.get("data"), dict) and isinstance(parsed["data"].get("text"), str):
            return parsed["data"]["text"]
        error = parsed.get("error") or parsed
        raise ModelError(f"OpenCode 请求失败：{json.dumps(error, ensure_ascii=False)[:500]}")

    def record_tool_results(self, exchanges: list[ToolExchange]) -> None:
        return None

    def record_nudge(self, message: str) -> None:
        self.nudges.append(message)


def parse_local_action(text: str) -> ToolCall | None:
    payload = extract_json_object(text)
    if not isinstance(payload, dict):
        return None
    tool = payload.get("tool")
    arguments = payload.get("arguments")
    if not isinstance(tool, str) or not isinstance(arguments, dict):
        return None
    reason = payload.get("reason", "")
    if not isinstance(reason, str):
        reason = ""
    nested_reason = arguments.pop("reason", "") if isinstance(arguments.get("reason"), str) else ""
    if not reason:
        reason = nested_reason
    return ToolCall(name=tool, arguments=arguments, reason=reason)


def extract_json_object(text: str) -> dict[str, Any] | None:
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            stripped = "\n".join(lines[1:])
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    start = stripped.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(stripped)):
        char = stripped[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = stripped[start : index + 1]
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None
