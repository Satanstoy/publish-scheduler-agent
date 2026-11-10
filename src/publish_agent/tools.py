from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .ledger import Ledger
from .models import (
    TIMEZONE,
    PublishRequest,
    RequestItem,
    add_minutes,
    build_draft_id,
    build_request_key,
    missing_fields,
)
from .rules import check_rules
from .slots import SlotQueryError, overlaps, query_slots
from .store import InjectedSaveError, JsonStore, StoreError


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    reason: str = ""
    id: str = ""


@dataclass
class ToolResult:
    ok: bool
    code: str = "OK"
    summary: str = ""
    data: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "summary": self.summary,
            "data": self.data,
            "error": self.error,
        }


class ToolError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        data: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.data = data


def _risk_message(risks: list[dict[str, Any]]) -> str:
    return "；".join(risk["message"] for risk in risks)


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_material",
            "description": "读取素材元数据，返回素材是否存在、字段是否完整。",
            "parameters": {
                "type": "object",
                "properties": {
                    "material_id": {"type": "string", "description": "素材 ID，例如 M01"},
                    "reason": {"type": "string", "description": "一句话说明为什么调用，用于审计"},
                },
                "required": ["material_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_rules",
            "description": "检查版权、禁词、黑名单日期、时长、工作时段和临期风险。必须先 get_material。",
            "parameters": {
                "type": "object",
                "properties": {
                    "material_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["material_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_slots",
            "description": "查询请求时段是否冲突，返回同日空闲替代时段。必须先 get_material 和 check_rules。",
            "parameters": {
                "type": "object",
                "properties": {
                    "material_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["material_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_draft",
            "description": "保存排期草稿。只保存草稿，不发布。硬约束冲突时不得传替代时段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "material_id": {"type": "string"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "start": {"type": "string", "description": "HH:MM"},
                    "confirmation_status": {
                        "type": "string",
                        "enum": ["not_required", "pending_user_confirmation"],
                    },
                    "decision_basis": {
                        "type": "string",
                        "description": "一句话说明为什么选这个时段，用于审计",
                    },
                    "reason": {"type": "string"},
                },
                "required": [
                    "material_id",
                    "date",
                    "start",
                    "confirmation_status",
                    "decision_basis",
                ],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "所有素材都有终态后结束。未完成时会被拒绝并返回缺失素材。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["summary"],
                "additionalProperties": False,
            },
        },
    },
]


class ToolRegistry:
    def __init__(
        self,
        store: JsonStore,
        request: PublishRequest,
        ledger: Ledger,
        now: datetime | None = None,
        faults: set[str] | None = None,
    ):
        self.store = store
        self.request = request
        self.ledger = ledger
        self.now = now or datetime.now().astimezone()
        self.faults = set(faults if faults is not None else store.faults)
        self.schemas = TOOL_SCHEMAS
        self._handlers: dict[str, Callable[[dict[str, Any]], ToolResult]] = {
            "get_material": self._get_material,
            "check_rules": self._check_rules,
            "query_slots": self._query_slots,
            "save_draft": self._save_draft,
            "finish": self._finish,
        }

    def execute(self, name: str, arguments: dict[str, Any] | None) -> ToolResult:
        args = copy.deepcopy(arguments or {})
        if "__invalid_json__" in args:
            return self._fail(
                "INVALID_ARGUMENTS",
                "工具参数不是合法 JSON，请重新输出完整参数对象。",
            )
        args.pop("reason", None)
        handler = self._handlers.get(name)
        if handler is None:
            return self._fail("UNKNOWN_TOOL", f"未知工具：{name}")
        try:
            return handler(args)
        except ToolError as exc:
            return self._fail(exc.code, exc.message, exc.retryable, exc.data)
        except Exception as exc:  # pragma: no cover - defensive boundary
            return self._fail("INTERNAL_ERROR", f"工具内部错误：{exc}")

    def _ok(self, summary: str, data: dict[str, Any] | None = None) -> ToolResult:
        return ToolResult(ok=True, code="OK", summary=summary, data=data)

    def _fail(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        data: dict[str, Any] | None = None,
    ) -> ToolResult:
        return ToolResult(
            ok=False,
            code=code,
            summary=message,
            data=data,
            error={"code": code, "message": message, "retryable": retryable},
        )

    def _require_material_id(self, args: dict[str, Any]) -> str:
        material_id = args.get("material_id")
        if not isinstance(material_id, str) or not material_id.strip():
            raise ToolError("INVALID_ARGUMENTS", "material_id 必须是非空字符串。")
        return material_id.strip()

    def _item(self, material_id: str) -> RequestItem:
        item = self.request.item_for(material_id)
        if item is None:
            raise ToolError(
                "MATERIAL_NOT_IN_REQUEST",
                f"素材 {material_id} 不在本次请求中。",
            )
        return item

    def _get_material(self, args: dict[str, Any]) -> ToolResult:
        material_id = self._require_material_id(args)
        self._item(material_id)
        material = self.store.get_material(material_id)
        if material is None:
            self.ledger.record_material(material_id, False)
            return self._fail(
                "MATERIAL_NOT_FOUND",
                f"素材 {material_id} 不存在，请检查素材 ID。",
            )
        missing = missing_fields(material, list(self.store.rules.get("required_fields", [])))
        self.ledger.record_material(material_id, True, material, missing)
        if missing:
            summary = f"读取 {material_id}，缺少 {len(missing)} 个字段：{', '.join(missing)}"
        else:
            summary = f"读取 {material_id}，字段完整"
        return self._ok(summary, {"material": material, "missing_fields": missing})

    def _check_rules(self, args: dict[str, Any]) -> ToolResult:
        material_id = self._require_material_id(args)
        item = self._item(material_id)
        entry = self.ledger.get(material_id)
        if entry.material_found is None:
            raise ToolError(
                "PRECONDITION_FAILED",
                "请先调用 get_material 读取素材。",
                data={"required": "get_material"},
            )
        if not entry.material_found or entry.material is None:
            raise ToolError(
                "PRECONDITION_FAILED",
                "素材未成功读取，无法检查规则。",
                data={"required": "get_material"},
            )
        report = check_rules(
            entry.material,
            item,
            self.store.rules,
            self.request.submitted_at_dt,
        )
        self.ledger.record_rules(material_id, report)
        if report.risk_level == "blocked":
            summary = f"规则检查：阻断 {len([r for r in report.risks if r['severity'] == 'hard'])} 项"
        elif report.risk_level == "warning":
            summary = f"规则检查：警告 {len(report.risks)} 项，需要人工确认"
        else:
            summary = "规则检查：无风险"
        return self._ok(summary, report.to_dict())

    def _query_slots(self, args: dict[str, Any]) -> ToolResult:
        material_id = self._require_material_id(args)
        item = self._item(material_id)
        entry = self.ledger.get(material_id)
        if entry.material_found is not True or entry.material is None:
            raise ToolError("PRECONDITION_FAILED", "请先成功调用 get_material。")
        if entry.rules is None:
            raise ToolError("PRECONDITION_FAILED", "请先调用 check_rules。")
        if entry.rules.get("missing_fields"):
            raise ToolError(
                "NOT_ELIGIBLE",
                "素材字段不完整，不能查询时段。",
                data={"missing_fields": entry.rules["missing_fields"]},
            )
        if entry.rules.get("risk_level") == "blocked":
            raise ToolError("NOT_ELIGIBLE", "规则检查未通过，不能查询时段。")
        try:
            result = query_slots(
                entry.material,
                item,
                self.store.rules,
                self.store,
                self.request.submitted_at_dt,
            )
        except SlotQueryError as exc:
            raise ToolError("NOT_ELIGIBLE", str(exc)) from exc
        self.ledger.record_slots(material_id, result)
        if result["conflict"]:
            if result["alternatives"]:
                summary = (
                    f"请求时段 {item.time} 冲突，最近备选 "
                    f"{result['alternatives'][0]['start']}"
                )
            else:
                summary = f"请求时段 {item.time} 冲突，且同日没有可用替代时段"
        else:
            summary = f"请求时段 {item.time} 可用"
        return self._ok(summary, result)

    def _save_draft(self, args: dict[str, Any]) -> ToolResult:
        material_id = self._require_material_id(args)
        item = self._item(material_id)
        date = args.get("date")
        start = args.get("start")
        confirmation_status = args.get("confirmation_status")
        decision_basis = args.get("decision_basis") or ""
        if not isinstance(date, str) or not isinstance(start, str):
            raise ToolError("INVALID_ARGUMENTS", "date 和 start 必须是字符串。")
        if confirmation_status not in {"not_required", "pending_user_confirmation"}:
            raise ToolError(
                "INVALID_ARGUMENTS",
                "confirmation_status 必须是 not_required 或 pending_user_confirmation。",
            )
        if date != item.date:
            raise ToolError(
                "DATE_NOT_VERIFIED",
                "当前只支持请求当天的时段，跨天需要修改请求后重新运行。",
            )
        entry = self.ledger.get(material_id)
        if entry.material_found is not True or entry.material is None:
            raise ToolError("PRECONDITION_FAILED", "请先成功调用 get_material。")
        if entry.rules is None:
            raise ToolError("PRECONDITION_FAILED", "请先调用 check_rules。")
        if entry.slots is None:
            raise ToolError("PRECONDITION_FAILED", "请先调用 query_slots。")
        if entry.rules.get("missing_fields"):
            raise ToolError("NOT_ELIGIBLE", "素材字段不完整，不能保存草稿。")
        if entry.rules.get("risk_level") == "blocked":
            raise ToolError("NOT_ELIGIBLE", "规则检查未通过，不能保存草稿。")

        requested_start = item.time
        alternatives = [slot["start"] for slot in entry.slots.get("alternatives", [])]
        warning_risks = [
            risk
            for risk in entry.rules.get("risks", [])
            if risk.get("severity") == "warning"
        ]
        if start != requested_start:
            if item.constraint == "hard":
                raise ToolError(
                    "HARD_CONSTRAINT_VIOLATION",
                    "硬性发布时间不可更改，不能保存替代时段。请向用户报告备选时段，不落草稿。",
                )
            if start not in alternatives:
                raise ToolError(
                    "SLOT_NOT_VERIFIED",
                    "该时段不在 query_slots 返回的备选列表中，请重新查询。",
                )
            if confirmation_status != "pending_user_confirmation":
                raise ToolError(
                    "CONFIRMATION_REQUIRED",
                    "替代时段必须标记 pending_user_confirmation，等待用户确认。",
                )
        else:
            if entry.slots.get("conflict"):
                raise ToolError("SLOT_CONFLICT", "请求时段已被占用，不能按原时段保存。")
            if warning_risks and confirmation_status != "pending_user_confirmation":
                raise ToolError(
                    "CONFIRMATION_REQUIRED",
                    "存在软警告，必须标记 pending_user_confirmation。",
                )
            if not warning_risks:
                confirmation_status = "not_required"

        duration = entry.material.get("duration_minutes")
        if not isinstance(duration, int) or duration <= 0:
            raise ToolError("NOT_ELIGIBLE", "素材时长无效，不能保存草稿。")
        end = add_minutes(start, duration)
        request_key = build_request_key(
            material_id,
            str(entry.material.get("content_version", "")),
            item.date,
            item.time,
            item.constraint,
            item.channel,
        )
        for busy in self.store.busy_entries(
            item.date,
            item.channel,
            exclude_request_key=request_key,
        ):
            if overlaps(start, end, busy["start"], busy["end"]):
                raise ToolError(
                    "SLOT_CONFLICT",
                    f"时段 {start}-{end} 已被 {busy['entry_id']} 占用，请重新查询。",
                )

        existing = self.store.get_draft_by_request_key(request_key)
        if existing is not None:
            data = {
                "draft": existing,
                "save_status": "existing",
                "deduplicated": True,
            }
            result = self._ok("相同请求已存在草稿，本次未重复写入。", data)
            self.ledger.record_save(material_id, args, result.to_dict())
            return result

        pending_items: list[dict[str, str]] = []
        if start != requested_start:
            pending_items.append(
                {
                    "code": "ALTERNATIVE_SLOT",
                    "message": f"建议 {start} 替代原定 {requested_start}，需用户确认。",
                }
            )
        for risk in warning_risks:
            pending_items.append(
                {
                    "code": risk["code"],
                    "message": f"{risk['message']} 需用户确认。",
                }
            )

        draft = {
            "id": build_draft_id(request_key),
            "request_key": request_key,
            "material_id": material_id,
            "content_version": str(entry.material.get("content_version", "")),
            "channel": item.channel,
            "slot": {
                "date": item.date,
                "start": start,
                "end": end,
                "timezone": TIMEZONE,
            },
            "requested_slot": {
                "date": item.date,
                "start": requested_start,
                "end": add_minutes(requested_start, duration),
                "constraint": item.constraint,
                "timezone": TIMEZONE,
            },
            "status": "draft",
            "confirmation_status": confirmation_status,
            "risks": entry.rules.get("risks", []),
            "pending_items": pending_items,
            "decision_basis": decision_basis
            or f"请求 {requested_start}，选择 {start}。",
            "created_at": self.now.isoformat(timespec="seconds"),
            "published": False,
        }
        try:
            stored, created = self.store.create_draft(draft, request_key)
        except (InjectedSaveError, StoreError, OSError) as exc:
            result = ToolResult(
                ok=False,
                code="SAVE_FAILED",
                summary="草稿保存失败，未写入任何草稿。",
                error={
                    "code": "SAVE_FAILED",
                    "message": f"保存失败：{exc}",
                    "retryable": False,
                },
            )
            self.ledger.record_save(material_id, args, result.to_dict())
            return result
        save_status = "saved" if created else "existing"
        data = {
            "draft": stored,
            "save_status": save_status,
            "deduplicated": not created,
        }
        summary = (
            "草稿已保存（仅草稿，未发布）。"
            if created
            else "相同请求已存在草稿，本次未重复写入。"
        )
        result = self._ok(summary, data)
        self.ledger.record_save(material_id, args, result.to_dict())
        return result

    def _finish(self, args: dict[str, Any]) -> ToolResult:
        missing = self.ledger.missing_terminal(self.request)
        if missing:
            raise ToolError(
                "INCOMPLETE_COVERAGE",
                f"还有 {len(missing)} 个素材没有终态：{', '.join(missing)}。请继续处理。",
                data={"missing": missing},
            )
        summary = args.get("summary") or "全部素材已处理；仅保存草稿，未发布。"
        return self._ok(summary, {"finished": True})
