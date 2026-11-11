from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from .context import ChatContext

HARD_WORDS = ("硬性", "必须", "一定要", "不能改", "不可改", "hard", "锁定")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
TIME_RE = re.compile(r"(\d{1,2})\s*(?:点|:|：)\s*(\d{1,2})?")
MATERIAL_RE = re.compile(r"\bM\d+\b", re.IGNORECASE)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _material_matches(message: str, context: ChatContext) -> list[str]:
    direct = MATERIAL_RE.search(message)
    if direct:
        candidate = direct.group(0).upper()
        if candidate in context.materials:
            return [candidate]
    normalized = _normalize(message)
    matches = []
    for material_id, material in context.materials.items():
        title = _normalize(str(material.get("title", "")))
        if title and title in normalized:
            matches.append((len(title), material_id))
    matches.sort(reverse=True)
    return [material_id for _, material_id in matches]


def _date_from_message(message: str, context: ChatContext) -> str | None:
    explicit = DATE_RE.search(message)
    if explicit:
        try:
            datetime.strptime(explicit.group(0), "%Y-%m-%d")
            return explicit.group(0)
        except ValueError:
            return None
    today = context.now.date()
    if "今天" in message or "今日" in message:
        return today.isoformat()
    if "明天" in message or "明日" in message:
        return (today + timedelta(days=1)).isoformat()
    if "后天" in message:
        return (today + timedelta(days=2)).isoformat()
    return None


def _time_from_message(message: str) -> str | None:
    match = TIME_RE.search(message)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if minute > 59 or hour > 23:
        return None
    if ":" in match.group(0) or "：" in match.group(0):
        return f"{hour:02d}:{minute:02d}"
    period = ""
    for word in ("晚上", "今晚", "夜里", "下午", "中午", "上午", "早上", "凌晨"):
        if word in message:
            period = word
            break
    if period in {"晚上", "今晚", "夜里", "下午"} and hour < 12:
        hour += 12
    elif period == "中午" and hour < 11:
        hour += 12
    elif period == "凌晨" and hour == 12:
        hour = 0
    if hour > 23:
        return None
    return f"{hour:02d}:{minute:02d}"


def _constraint_from_message(message: str) -> str:
    lowered = message.lower()
    return "hard" if any(word in lowered for word in HARD_WORDS) else "preferred"


def parse_request(message: str, context: ChatContext) -> dict[str, Any]:
    matches = _material_matches(message, context)
    if not matches:
        return {
            "intent": "unknown",
            "message": "没识别出素材。请说标题或 ID，例如“秋日通勤穿搭 / M00”。",
        }
    if len(matches) > 1:
        return {
            "intent": "unknown",
            "message": "匹配到多个素材："
            + "、".join(
                f"{mid} {context.materials[mid].get('title', '')}" for mid in matches
            )
            + "。请说明具体是哪一个。",
        }
    material_id = matches[0]
    day = _date_from_message(message, context)
    clock = _time_from_message(message)
    if day is None:
        day = context.now.date().isoformat()
    if clock is None:
        return {
            "intent": "unknown",
            "message": "没识别出发布时间。请补充“今天晚上 7 点”或“2026-11-12 18:00”。",
        }
    return {
        "intent": "schedule",
        "material_id": material_id,
        "date": day,
        "time": clock,
        "constraint": _constraint_from_message(message),
        "reason": "请求解析：素材标题/ID + 相对日期 + 时间",
    }
