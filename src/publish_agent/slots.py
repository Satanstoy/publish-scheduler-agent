from __future__ import annotations

from datetime import timedelta
from typing import Any

from .models import (
    TIMEZONE,
    RequestItem,
    add_minutes,
    build_request_key,
    minutes_to_time,
    time_to_minutes,
)


class SlotQueryError(ValueError):
    """Raised when slots cannot be queried with the available material data."""


def overlaps(start_a: str, end_a: str, start_b: str, end_b: str) -> bool:
    a1, a2 = time_to_minutes(start_a), time_to_minutes(end_a)
    b1, b2 = time_to_minutes(start_b), time_to_minutes(end_b)
    return max(a1, b1) < min(a2, b2)


def query_slots(
    material: dict[str, Any],
    request_item: RequestItem,
    rules: dict[str, Any],
    store,
    submitted_at=None,
) -> dict[str, Any]:
    duration = material.get("duration_minutes")
    if not isinstance(duration, int) or duration <= 0:
        raise SlotQueryError("duration_minutes is required for slot queries")

    request_key = build_request_key(
        request_item.material_id,
        str(material.get("content_version", "")),
        request_item.date,
        request_item.time,
        request_item.constraint,
        request_item.channel,
    )
    requested_start = time_to_minutes(request_item.time)
    requested_end = add_minutes(request_item.time, duration)
    busy = store.busy_entries(
        request_item.date,
        request_item.channel,
        exclude_request_key=request_key,
    )
    conflicts = [
        entry
        for entry in busy
        if overlaps(request_item.time, requested_end, entry["start"], entry["end"])
    ]
    existing_draft = store.get_draft_by_request_key(request_key)

    working_hours = rules.get("working_hours", {})
    work_start = time_to_minutes(working_hours.get("start", "00:00"))
    work_end = time_to_minutes(working_hours.get("end", "23:59"))
    step = int(rules.get("slot_step_minutes", 30) or 30)

    earliest = work_start
    lead_hours = rules.get("min_lead_time_hours")
    if submitted_at is not None and lead_hours is not None:
        earliest_dt = submitted_at + timedelta(hours=float(lead_hours))
        if earliest_dt.date().isoformat() == request_item.date:
            earliest = max(earliest, earliest_dt.hour * 60 + earliest_dt.minute)
        elif earliest_dt.date().isoformat() > request_item.date:
            earliest = work_end

    alternatives: list[dict[str, Any]] = []
    if conflicts:
        last_start = work_end - duration
        for candidate in range(earliest, last_start + 1, step):
            candidate_end = candidate + duration
            if candidate_end > work_end:
                continue
            if any(
                _overlaps_minutes(candidate, candidate_end, entry["start"], entry["end"])
                for entry in busy
            ):
                continue
            alternatives.append(
                {
                    "date": request_item.date,
                    "start": minutes_to_time(candidate),
                    "end": minutes_to_time(candidate_end),
                    "timezone": TIMEZONE,
                    "_start": candidate,
                }
            )
        alternatives.sort(
            key=lambda slot: (
                0 if slot["_start"] >= requested_start else 1,
                abs(slot["_start"] - requested_start),
                slot["_start"],
            )
        )
        alternatives = alternatives[:5]
        for slot in alternatives:
            slot.pop("_start", None)

    return {
        "request_key": request_key,
        "requested": {
            "date": request_item.date,
            "start": request_item.time,
            "end": requested_end,
            "constraint": request_item.constraint,
            "channel": request_item.channel,
            "timezone": TIMEZONE,
        },
        "conflict": bool(conflicts),
        "conflicts": conflicts,
        "alternatives": alternatives,
        "existing_draft": existing_draft,
        "search_window": {
            "date": request_item.date,
            "work_start": minutes_to_time(work_start),
            "work_end": minutes_to_time(work_end),
            "earliest_start": minutes_to_time(earliest),
            "step_minutes": step,
            "timezone": TIMEZONE,
        },
    }


def _overlaps_minutes(start_a: int, end_a: int, start_b: str, end_b: str) -> bool:
    return max(start_a, time_to_minutes(start_b)) < min(end_a, time_to_minutes(end_b))
