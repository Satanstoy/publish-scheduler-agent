from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, time as time_cls, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

TIMEZONE = "Asia/Shanghai"
CONSTRAINTS = {"hard", "preferred"}
_MISSING = object()


class RequestValidationError(ValueError):
    """Raised when a publishing request is malformed."""


@dataclass(frozen=True)
class RequestItem:
    material_id: str
    date: str
    time: str
    constraint: str = "preferred"
    channel: str = "wechat"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "material_id": self.material_id,
            "date": self.date,
            "time": self.time,
            "constraint": self.constraint,
            "channel": self.channel,
            "note": self.note,
        }


@dataclass(frozen=True)
class PublishRequest:
    request_id: str
    items: tuple[RequestItem, ...]
    submitted_at: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> "PublishRequest":
        if not isinstance(data, dict):
            raise RequestValidationError("request must be a JSON object")
        request_id = str(data.get("request_id") or "").strip()
        if not request_id:
            raise RequestValidationError("request_id is required")
        raw_items = data.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise RequestValidationError("items must be a non-empty list")
        if len(raw_items) > 5:
            raise RequestValidationError("items must contain at most 5 materials")
        items: list[RequestItem] = []
        seen: set[str] = set()
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise RequestValidationError("each item must be an object")
            material_id = str(raw.get("material_id") or "").strip()
            if not material_id:
                raise RequestValidationError("material_id is required")
            if material_id in seen:
                raise RequestValidationError(f"duplicate material_id: {material_id}")
            seen.add(material_id)
            raw_date = str(raw.get("date") or "").strip()
            raw_time = str(raw.get("time") or "").strip()
            validate_date(raw_date)
            validate_time(raw_time)
            constraint = str(raw.get("constraint") or "preferred")
            if constraint not in CONSTRAINTS:
                raise RequestValidationError(
                    f"constraint must be one of {sorted(CONSTRAINTS)}, got {constraint!r}"
                )
            items.append(
                RequestItem(
                    material_id=material_id,
                    date=raw_date,
                    time=raw_time,
                    constraint=constraint,
                    channel=str(raw.get("channel") or "wechat"),
                    note=str(raw.get("note") or ""),
                )
            )
        submitted_at = data.get("submitted_at")
        if submitted_at is not None:
            submitted_at = str(submitted_at)
            try:
                datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise RequestValidationError("submitted_at must be ISO 8601") from exc
        return cls(request_id=request_id, items=tuple(items), submitted_at=submitted_at)

    def item_for(self, material_id: str) -> RequestItem | None:
        for item in self.items:
            if item.material_id == material_id:
                return item
        return None

    @property
    def submitted_at_dt(self) -> datetime | None:
        if self.submitted_at is None:
            return None
        return datetime.fromisoformat(self.submitted_at.replace("Z", "+00:00"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "submitted_at": self.submitted_at,
            "items": [item.to_dict() for item in self.items],
        }


def load_request(path: str | Path) -> PublishRequest:
    raw = Path(path).read_text(encoding="utf-8")
    return PublishRequest.from_dict(json.loads(raw))


def validate_date(value: str) -> None:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise RequestValidationError(f"date must be YYYY-MM-DD, got {value!r}") from exc


def validate_time(value: str) -> None:
    if not re.fullmatch(r"\d{2}:\d{2}", value):
        raise RequestValidationError(f"time must be HH:MM, got {value!r}")
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise RequestValidationError(f"time must be HH:MM, got {value!r}") from exc


def parse_date(value: str) -> date_cls:
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_time(value: str) -> time_cls:
    validate_time(value)
    return datetime.strptime(value, "%H:%M").time()


def time_to_minutes(value: str) -> int:
    parsed = parse_time(value)
    return parsed.hour * 60 + parsed.minute


def minutes_to_time(value: int) -> str:
    value %= 24 * 60
    return f"{value // 60:02d}:{value % 60:02d}"


def add_minutes(value: str, minutes: int) -> str:
    return minutes_to_time(time_to_minutes(value) + minutes)


def combine_local(day: str, clock: str, tzinfo=None) -> datetime:
    return datetime.combine(parse_date(day), parse_time(clock), tzinfo=tzinfo)


def get_path(obj: Any, path: str) -> Any:
    current = obj
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def missing_fields(material: dict[str, Any], required_fields: list[str]) -> list[str]:
    result: list[str] = []
    for path in required_fields:
        value = get_path(material, path)
        if value is _MISSING or value is None:
            result.append(path)
        elif isinstance(value, str) and not value.strip():
            result.append(path)
    return result


def build_request_key(
    material_id: str,
    content_version: str,
    day: str,
    clock: str,
    constraint: str,
    channel: str,
) -> str:
    raw = "|".join(
        [
            material_id.strip(),
            str(content_version).strip(),
            day.strip(),
            clock.strip(),
            constraint.strip(),
            channel.strip(),
        ]
    )
    return "RQ-" + sha256(raw.encode("utf-8")).hexdigest()[:16].upper()


def build_draft_id(request_key: str) -> str:
    return "DRF-" + sha256(request_key.encode("utf-8")).hexdigest()[:10].upper()


def slot_dict(day: str, start: str, duration_minutes: int) -> dict[str, Any]:
    return {
        "date": day,
        "start": start,
        "end": add_minutes(start, duration_minutes),
        "timezone": TIMEZONE,
    }


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
