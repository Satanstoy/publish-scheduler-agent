from __future__ import annotations

from typing import Any

from .ledger import Ledger
from .models import TIMEZONE, PublishRequest, add_minutes


def _default_draft() -> dict[str, Any]:
    return {
        "id": None,
        "save_status": "not_attempted",
        "deduplicated": False,
        "status": "not_saved",
        "published": False,
    }


def _requested_slot(item, material: dict[str, Any]) -> dict[str, Any]:
    duration = material.get("duration_minutes")
    end = add_minutes(item.time, duration) if isinstance(duration, int) and duration > 0 else None
    return {
        "date": item.date,
        "start": item.time,
        "end": end,
        "constraint": item.constraint,
        "channel": item.channel,
        "timezone": TIMEZONE,
    }


def build_report(
    request: PublishRequest,
    ledger: Ledger,
    run_meta: dict[str, Any],
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    proposed: list[dict[str, Any]] = []
    confirmation_required: list[str] = []
    counts = {
        "drafted": 0,
        "pending_confirmation": 0,
        "blocked_rule": 0,
        "blocked_conflict": 0,
        "needs_input": 0,
        "save_failed": 0,
        "incomplete": 0,
    }

    for item in request.items:
        entry = ledger.get(item.material_id)
        material = entry.material or {}
        risks = list(entry.rules.get("risks", [])) if entry.rules else []
        missing = list(entry.missing_fields)
        if entry.rules and entry.rules.get("missing_fields"):
            missing = list(entry.rules["missing_fields"])
        if entry.material_found is False:
            risks.append(
                {
                    "code": "MATERIAL_NOT_FOUND",
                    "severity": "hard",
                    "message": f"素材 {item.material_id} 不存在。",
                    "field": "material_id",
                }
            )
        slots = entry.slots
        slot_conflict = (
            {
                "has_conflict": bool(slots.get("conflict")),
                "conflicts": slots.get("conflicts", []),
            }
            if slots
            else None
        )
        terminal = ledger.terminal_status(item.material_id, item)
        review_status = "unknown"
        schedule_status = "incomplete"
        confirmation_status = "not_applicable"
        suggested_slot = None
        pending_items: list[dict[str, str]] = []
        draft = _default_draft()
        error = None
        basis = ""

        if terminal == "needs_input":
            review_status = "needs_input"
            schedule_status = "not_scheduled"
            if entry.material_found is False:
                pending_items = [
                    {
                        "code": "MATERIAL_NOT_FOUND",
                        "message": f"素材 {item.material_id} 不存在，请检查素材 ID。",
                    }
                ]
                basis = f"素材 {item.material_id} 不存在，无法读取元数据，未保存草稿。"
            else:
                pending_items = [
                    {
                        "code": "MISSING_INPUT",
                        "message": f"缺少字段：{'、'.join(missing)}，需补充后重新运行。",
                    }
                ]
                basis = f"缺少字段：{'、'.join(missing)}，无法判断时长和版权，未保存草稿。"
        elif terminal == "blocked_rule":
            review_status = "blocked"
            schedule_status = "not_scheduled"
            confirmation_status = "blocked"
            codes = "、".join(
                risk["code"] for risk in risks if risk.get("severity") == "hard"
            )
            pending_items = [
                {
                    "code": "RULE_BLOCKED",
                    "message": "素材命中硬规则，需修改素材或规则后重新提交。",
                }
            ]
            basis = f"命中硬规则 {codes}，不得进入排期，未保存草稿。"
        elif terminal == "blocked_conflict":
            review_status = (
                "warning"
                if any(risk.get("severity") == "warning" for risk in risks)
                else "passed"
            )
            schedule_status = "blocked_conflict"
            confirmation_status = "blocked"
            alternatives = slots.get("alternatives", []) if slots else []
            suggested_slot = alternatives[0] if alternatives else None
            if item.constraint == "hard":
                pending_items = [
                    {
                        "code": "HARD_CONSTRAINT_CONFLICT",
                        "message": (
                            f"硬性要求 {item.time} 与已有排期冲突，不能自动改时段；"
                            f"建议 {suggested_slot['start'] if suggested_slot else '无'}，"
                            "需用户决定是否调整要求。"
                        ),
                    }
                ]
                basis = (
                    f"硬性要求 {item.time} 与已有排期冲突，硬约束不可自动改时段；"
                    f"建议 {suggested_slot['start'] if suggested_slot else '无可用时段'}，"
                    "未保存草稿。"
                )
            else:
                pending_items = [
                    {
                        "code": "NO_AVAILABLE_SLOT",
                        "message": f"请求时段 {item.time} 冲突，同日没有可用替代时段。",
                    }
                ]
                basis = f"请求时段 {item.time} 冲突，同日无可用替代，未保存草稿。"
        elif terminal == "save_failed":
            review_status = (
                "warning"
                if any(risk.get("severity") == "warning" for risk in risks)
                else "passed"
            )
            schedule_status = "save_failed"
            save_args = entry.save_args or {}
            start = save_args.get("start")
            if isinstance(start, str):
                duration = material.get("duration_minutes")
                if isinstance(duration, int) and duration > 0:
                    suggested_slot = {
                        "date": save_args.get("date", item.date),
                        "start": start,
                        "end": add_minutes(start, duration),
                        "timezone": TIMEZONE,
                    }
            draft = {
                "id": None,
                "save_status": "failed",
                "deduplicated": False,
                "status": "not_saved",
                "published": False,
            }
            error = (entry.save_result or {}).get("error")
            pending_items = [
                {
                    "code": "SAVE_FAILED",
                    "message": "草稿保存失败，未写入任何草稿，未发布；请检查存储后重试。",
                }
            ]
            basis = "时段可用，但保存失败（SAVE_FAILED）；未保存草稿，未发布。"
        elif terminal in {"drafted", "pending_confirmation"}:
            data = (entry.save_result or {}).get("data") or {}
            saved = data.get("draft") or {}
            save_status = data.get("save_status", "saved")
            review_status = (
                "warning"
                if any(risk.get("severity") == "warning" for risk in risks)
                else "passed"
            )
            schedule_status = terminal
            suggested_slot = saved.get("slot")
            confirmation_status = saved.get("confirmation_status", "not_required")
            pending_items = list(saved.get("pending_items", []))
            draft = {
                "id": saved.get("id"),
                "save_status": save_status,
                "deduplicated": bool(data.get("deduplicated", False)),
                "status": saved.get("status", "draft"),
                "published": False,
            }
            if save_status == "existing":
                basis = (
                    f"相同请求指纹已存在草稿 {saved.get('id')}，本次未重复写入。"
                )
            elif saved.get("slot", {}).get("start") != item.time:
                basis = (
                    f"请求时段 {item.time} 冲突，"
                    f"{saved.get('slot', {}).get('start')} 为同日替代时段；"
                    "软约束，保存草稿并标记待确认。"
                )
            elif review_status == "warning":
                basis = "规则命中软警告，已保存草稿并标记待确认。"
            else:
                basis = f"请求时段 {item.time} 空闲，规则无风险，已保存草稿。"
        else:
            review_status = "unknown"
            schedule_status = "incomplete"
            pending_items = [
                {
                    "code": "INCOMPLETE",
                    "message": "未在执行步数或工具调用上限内完成，未保存草稿。",
                }
            ]
            limits = run_meta.get("limits", {})
            basis = (
                f"在 {limits.get('max_steps')} 步 / "
                f"{limits.get('max_tool_calls')} 次调用上限内未完成。"
            )

        if review_status == "needs_input":
            counts["needs_input"] += 1
        elif schedule_status in counts:
            counts[schedule_status] += 1
        elif review_status == "blocked":
            counts["blocked_rule"] += 1
        else:
            counts["incomplete"] += 1

        item_result = {
            "material_id": item.material_id,
            "title": material.get("title"),
            "review_status": review_status,
            "schedule_status": schedule_status,
            "risks": risks,
            "missing_fields": missing,
            "requested_slot": _requested_slot(item, material),
            "suggested_slot": suggested_slot,
            "slot_conflict": slot_conflict,
            "confirmation_status": confirmation_status,
            "pending_items": pending_items,
            "draft": draft,
            "error": error,
            "decision_basis": basis,
            "published": False,
        }
        items.append(item_result)

        if draft["id"] and draft["save_status"] in {"saved", "existing"}:
            proposed.append(
                {
                    "material_id": item.material_id,
                    "draft_id": draft["id"],
                    "slot": suggested_slot,
                    "confirmation_status": confirmation_status,
                }
            )
        if confirmation_status == "pending_user_confirmation":
            confirmation_required.append(item.material_id)

    proposed.sort(key=lambda entry: (entry["slot"]["date"], entry["slot"]["start"]))
    summary = {"total": len(items), **counts}
    if run_meta["status"] == "failed":
        outcome = "failed"
    elif run_meta["status"] == "incomplete":
        outcome = "incomplete"
    elif any(
        item["schedule_status"] in {"save_failed", "blocked_conflict"}
        or item["review_status"] in {"blocked", "needs_input"}
        for item in items
    ):
        outcome = "needs_attention"
    elif any(item["confirmation_status"] == "pending_user_confirmation" for item in items):
        outcome = "needs_confirmation"
    else:
        outcome = "scheduled"
    return {
        "schema_version": "1.0",
        "run_id": run_meta["run_id"],
        "request_id": request.request_id,
        "engine": run_meta["engine"],
        "model": run_meta["model"],
        "mode_note": run_meta["mode_note"],
        "status": run_meta["status"],
        "outcome": outcome,
        "stop_reason": run_meta["stop_reason"],
        "published": False,
        "limits": run_meta["limits"],
        "summary": summary,
        "items": items,
        "proposed_schedule": proposed,
        "confirmation_required": confirmation_required,
    }
