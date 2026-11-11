from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable

from .config import write_json
from .context import ChatContext
from .intent import parse_request
from .ledger import Ledger
from .models import PublishRequest
from .resolver import classify_message, resolve_confirmation
from .runner import AgentRunner, Limits
from .store import JsonStore
from .tools import ToolRegistry

HELP_TEXT = """直接说人话即可：
  给我安排一下秋日通勤穿搭今天晚上 7 点的排期
  把 M04 排在 2026-11-14 18:00，硬性时间
  M00 明天下午 3 点
  可以 / 没问题 / 继续 / 确认       # 确认唯一 pending 草稿
  取消                             # 不确认
  状态                             # 查看全部草稿
  退出

说明：排期决策由模型完成；确认层是确定性的，多份 pending 时不会用一句“可以”全部确认。"""


class ChatSession:
    def __init__(
        self,
        store: JsonStore,
        engine_factory: Callable[[PublishRequest, JsonStore], Any],
        now: datetime,
        max_steps: int = 12,
        max_tool_calls: int = 20,
        faults: set[str] | None = None,
    ):
        self.store = store
        self.engine_factory = engine_factory
        self.now = now
        self.limits = Limits(max_steps=max_steps, max_tool_calls=max_tool_calls)
        self.faults = set(faults or [])
        self.counter = 0
        self.last_output: dict[str, Any] | None = None
        self.last_trace: dict[str, Any] | None = None
        self.last_intent: dict[str, Any] | None = None
        self.context = ChatContext(
            materials=store.materials,
            now=now,
            pending=store.pending_drafts(),
        )

    def handle(self, line: str) -> tuple[str, bool]:
        text = line.strip()
        if not text:
            return "", False
        self.context.add_user(text)
        reply, should_exit = self._handle(text)
        self.context.add_assistant(reply)
        return reply, should_exit

    def _handle(self, text: str) -> tuple[str, bool]:
        lowered = text.lower()
        if lowered in {"退出", "quit", "exit", "q"}:
            return "会话结束。草稿仍然只保存在本地，未发布。", True
        if lowered in {"帮助", "help", "?", "？"}:
            return HELP_TEXT, False
        if lowered in {"状态", "草稿", "status", "list"}:
            return self._format_state(), False

        pending = self.store.pending_drafts()
        parsed = parse_request(text, self.context)
        if parsed.get("intent") == "schedule":
            return self._run(parsed), False
        if parsed.get("intent") == "status":
            return self._format_state(), False
        if parsed.get("intent") == "help":
            return HELP_TEXT, False

        message_intent = classify_message(text)
        if pending and message_intent != "unknown":
            result = resolve_confirmation(text, self.store, now=self.now)
            self.context.refresh(self.store)
            return self._format_confirmation(result), False
        if not pending and message_intent in {"affirm", "decline"}:
            return (
                "当前没有待确认草稿。直接说排期请求即可，例如："
                "给我安排秋日通勤穿搭今天晚上 7 点的排期。",
                False,
            )
        if pending:
            result = resolve_confirmation(text, self.store, now=self.now)
            self.context.refresh(self.store)
            return self._format_confirmation(result), False
        return (
            f"{parsed.get('message', '没理解这句话。')}\n"
            "可以直接说：给我安排秋日通勤穿搭今天晚上 7 点的排期。",
            False,
        )

    def _run(self, parsed: dict[str, Any]) -> str:
        self.counter += 1
        self.last_intent = parsed
        request = PublishRequest.from_dict(
            {
                "request_id": f"CHAT-{self.counter:04d}",
                "submitted_at": self.now.isoformat(timespec="seconds"),
                "items": [
                    {
                        "material_id": parsed["material_id"],
                        "date": parsed["date"],
                        "time": parsed["time"],
                        "constraint": parsed["constraint"],
                        "channel": "wechat",
                        "note": "interactive chat",
                    }
                ],
            }
        )
        ledger = Ledger()
        tools = ToolRegistry(
            self.store,
            request,
            ledger,
            now=self.now,
            faults=self.faults,
        )
        engine = self.engine_factory(request, self.store)
        runner = AgentRunner(
            engine=engine,
            tools=tools,
            request=request,
            ledger=ledger,
            limits=self.limits,
        )
        output, trace = runner.run()
        self.last_output = output
        self.last_trace = trace
        self.context.last_run = output
        self.context.refresh(self.store)
        write_json(self.store.state_dir / "output.json", output)
        write_json(self.store.state_dir / "trace.json", trace)
        write_json(self.store.state_dir / "intent.json", parsed)
        return self._format_run(request, output, trace)

    def _format_run(self, request: PublishRequest, output: dict, trace: dict) -> str:
        intent = self.last_intent or {}
        lines = [
            "",
            f"[意图] {intent.get('material_id')} {intent.get('date')} "
            f"{intent.get('time')} constraint={intent.get('constraint')} "
            f"# {intent.get('reason', '')}",
            f"[运行] request={request.request_id} engine={output['engine']} "
            f"status={output['status']} outcome={output['outcome']}",
            "[工具调用]",
        ]
        for step in trace.get("steps", []):
            if step.get("type") == "model_decision":
                for call in step.get("tool_calls", []):
                    args = json.dumps(call.get("arguments", {}), ensure_ascii=False)
                    reason = call.get("reason") or ""
                    lines.append(f"  → {call['name']}({args})  # {reason}")
            elif step.get("type") == "tool_result":
                lines.append(
                    f"    {step.get('tool')} -> {step.get('code')}: {step.get('summary')}"
                )
        lines.append("[结果]")
        for item in output.get("items", []):
            lines.append(
                f"  {item['material_id']} status={item['status']} "
                f"review={item['review_status']} schedule={item['schedule_status']}"
            )
            suggested = item.get("suggested_slot")
            if suggested:
                lines.append(
                    f"    建议 {suggested.get('date')} "
                    f"{suggested.get('start')}-{suggested.get('end')}"
                )
            draft = item.get("draft") or {}
            lines.append(
                f"    草稿 {draft.get('id') or '-'} "
                f"save={draft.get('save_status')} "
                f"confirmation={item.get('confirmation_status')}"
            )
            for pending in item.get("pending_items", []):
                lines.append(f"    待确认: {pending.get('message')}")
            lines.append(f"    published={str(item.get('published')).lower()}")
        pending_count = len(self.store.pending_drafts())
        if pending_count:
            lines.append(
                f"待确认草稿 {pending_count} 份。输入 `可以` / `没问题` / `继续` 确认唯一草稿，"
                "或输入 `取消` 保留 pending。"
            )
        else:
            lines.append("没有待确认草稿。")
        lines.append(
            f"输出: {self.store.state_dir / 'output.json'}  "
            f"Trace: {self.store.state_dir / 'trace.json'}"
        )
        return "\n".join(lines)

    def _format_state(self) -> str:
        self.context.refresh(self.store)
        drafts = self.store.list_drafts()
        if not drafts:
            return "当前没有草稿。"
        lines = [f"共 {len(drafts)} 份草稿："]
        for draft in drafts:
            slot = draft.get("slot") or {}
            lines.append(
                f"  {draft.get('id')}  {draft.get('material_id')}  "
                f"{slot.get('date')} {slot.get('start')}-{slot.get('end')}  "
                f"confirmation={draft.get('confirmation_status')}  "
                f"status={draft.get('status')}  published=false"
            )
        pending = sum(
            1 for draft in drafts if draft.get("confirmation_status") == "pending_user_confirmation"
        )
        lines.append(f"其中待确认 {pending} 份。")
        return "\n".join(lines)

    def _format_confirmation(self, result: dict[str, Any]) -> str:
        self.context.refresh(self.store)
        lines = [f"[确认] {result['action']}：{result['message']}"]
        draft = result.get("draft")
        if draft:
            lines.append(
                f"  草稿 {draft.get('id')} status={draft.get('status')} "
                f"confirmation={draft.get('confirmation_status')} "
                f"published={str(draft.get('published')).lower()}"
            )
        if result.get("pending"):
            lines.append("  待确认：")
            for item in result["pending"]:
                slot = item.get("slot") or {}
                lines.append(
                    f"    {item.get('material_id')} {item.get('draft_id')} "
                    f"{slot.get('date')} {slot.get('start')}-{slot.get('end')}"
                )
        return "\n".join(lines)


def run_chat(session: ChatSession) -> None:
    print("素材审核与发布排期 Agent（交互模式）", flush=True)
    print(
        "素材: "
        + "、".join(
            f"{mid} {material.get('title', '')}"
            for mid, material in session.store.materials.items()
        ),
        flush=True,
    )
    print("输入 `帮助` 查看命令，`退出` 结束。", flush=True)
    while True:
        try:
            line = input("用户> ")
        except EOFError:
            print("\n会话结束。", flush=True)
            return
        except KeyboardInterrupt:
            print("\n会话结束。", flush=True)
            return
        reply, should_exit = session.handle(line)
        if reply:
            print(reply, flush=True)
        if should_exit:
            return
