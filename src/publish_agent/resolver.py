from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .store import DraftConflictError, JsonStore

AFFIRMATIVE = {
    "可以",
    "没问题",
    "继续",
    "确认",
    "确定",
    "好",
    "行",
    "同意",
    "是的",
    "yes",
    "ok",
    "okay",
    "confirm",
    "sure",
}
NEGATIVE = {
    "不",
    "不行",
    "不要",
    "取消",
    "算了",
    "否",
    "别",
    "no",
    "cancel",
    "stop",
}
PUNCTUATION = "，。！？!?、,.;；:： \t\r\n"
CHANGE_HINTS = ("改", "换", "调整", "提前", "推迟", "延后", "换到", "改到")


def classify_message(message: str) -> str:
    raw = (message or "").strip().lower()
    if not raw:
        return "unknown"
    if re.search(r"\d{1,2}:\d{2}", raw) or any(hint in raw for hint in CHANGE_HINTS):
        return "change"
    text = raw
    for char in PUNCTUATION:
        text = text.replace(char, "")
    if not text:
        return "unknown"
    affirmative = any(word in text for word in AFFIRMATIVE)
    negative = any(word in text for word in NEGATIVE)
    if affirmative and not negative:
        return "affirm"
    if negative and not affirmative:
        return "decline"
    return "unknown"


def _pending_summary(pending: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "draft_id": draft.get("id"),
            "material_id": draft.get("material_id"),
            "slot": draft.get("slot"),
            "confirmation_status": draft.get("confirmation_status"),
        }
        for draft in pending
    ]


def resolve_confirmation(
    message: str,
    store: JsonStore,
    now: datetime | None = None,
) -> dict[str, Any]:
    pending = store.pending_drafts()
    intent = classify_message(message)
    base: dict[str, Any] = {
        "ok": True,
        "action": "no_pending",
        "message": "",
        "intent": intent,
        "pending": _pending_summary(pending),
        "draft": None,
        "published": False,
    }

    if not pending:
        base["message"] = (
            "当前没有待确认草稿。‘可以’不会改变硬性发布时间；"
            "硬冲突需要修改请求后重新运行。"
        )
        return base

    if len(pending) > 1:
        base["action"] = "needs_clarification"
        ids = "、".join(
            f"{draft.get('material_id')}（{draft.get('id')}，"
            f"{(draft.get('slot') or {}).get('start')}）"
            for draft in pending
        )
        base["message"] = (
            f"当前有 {len(pending)} 份待确认草稿：{ids}。"
            "请指明要确认哪一份，不能用一句‘可以/继续’确认全部。"
        )
        return base

    draft = pending[0]
    if intent == "change":
        base["action"] = "needs_clarification"
        base["message"] = (
            "检测到修改时段或条件的意图。confirm-text 只处理确认/拒绝，"
            "请修改请求后重新运行；当前草稿保持 pending，未发布。"
        )
        return base

    if intent == "decline":
        base["action"] = "declined"
        base["message"] = "已保留 pending 状态，没有确认，也没有发布。"
        return base

    if intent != "affirm":
        base["action"] = "needs_clarification"
        base["message"] = (
            "没有识别出明确同意。请回复‘确认’或‘取消’，"
            "或使用 confirm --draft-id 指定唯一草稿。"
        )
        return base

    try:
        confirmed = store.confirm_draft(str(draft["id"]), now=now)
    except DraftConflictError as exc:
        base["ok"] = False
        base["action"] = "confirm_conflict"
        base["message"] = (
            f"草稿 {draft['id']} 的时段已被占用，不能确认；"
            f"草稿保持 pending。详情：{exc}"
        )
        return base

    base["action"] = "confirmed"
    base["message"] = (
        f"已将‘{message.strip()}’解释为确认唯一待确认草稿 {draft['id']}；"
        "仍然是草稿，未发布。"
    )
    base["pending"] = []
    base["draft"] = confirmed
    return base
