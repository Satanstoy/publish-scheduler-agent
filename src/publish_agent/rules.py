from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .models import (
    RequestItem,
    combine_local,
    get_path,
    missing_fields as find_missing_fields,
    time_to_minutes,
)


@dataclass
class RuleReport:
    risk_level: str
    risks: list[dict[str, Any]] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_level": self.risk_level,
            "risks": self.risks,
            "missing_fields": self.missing_fields,
        }


def _risk(code: str, severity: str, message: str, field: str | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "field": field,
    }


def check_rules(
    material: dict[str, Any],
    request_item: RequestItem,
    rules: dict[str, Any],
    submitted_at: datetime | None = None,
) -> RuleReport:
    risks: list[dict[str, Any]] = []
    missing = find_missing_fields(material, list(rules.get("required_fields", [])))

    for path in missing:
        risks.append(
            _risk(
                "MISSING_REQUIRED_FIELD",
                "hard",
                f"缺少必填字段：{path}",
                path,
            )
        )

    authorized = get_path(material, "metadata.authorized")
    if "metadata.authorized" not in missing and authorized is not True:
        risks.append(
            _risk(
                "COPYRIGHT_NOT_AUTHORIZED",
                "hard",
                "素材未标记为已授权，不能进入排期。",
                "metadata.authorized",
            )
        )

    text = f"{material.get('title', '')} {material.get('summary', '')}".lower()
    for term in rules.get("forbidden_terms", []):
        if str(term).lower() in text:
            risks.append(
                _risk(
                    "FORBIDDEN_TERM",
                    "hard",
                    f"标题或摘要命中禁词：{term}",
                    "title/summary",
                )
            )
            break

    if request_item.date in set(rules.get("blackout_dates", [])):
        risks.append(
            _risk(
                "BLACKOUT_DATE",
                "hard",
                f"{request_item.date} 是规则禁止发布的日期。",
                "date",
            )
        )

    duration = material.get("duration_minutes")
    if "duration_minutes" not in missing:
        if not isinstance(duration, int) or duration <= 0:
            risks.append(
                _risk(
                    "INVALID_DURATION",
                    "hard",
                    "duration_minutes 必须是正整数。",
                    "duration_minutes",
                )
            )
        else:
            max_duration = int(rules.get("max_duration_minutes", 0) or 0)
            warning_duration = int(rules.get("long_duration_warning_minutes", 0) or 0)
            if max_duration and duration > max_duration:
                risks.append(
                    _risk(
                        "DURATION_EXCEEDS_LIMIT",
                        "hard",
                        f"时长 {duration} 分钟超过上限 {max_duration} 分钟。",
                        "duration_minutes",
                    )
                )
            elif warning_duration and duration > warning_duration:
                risks.append(
                    _risk(
                        "LONG_DURATION_REVIEW",
                        "warning",
                        f"时长 {duration} 分钟超过 {warning_duration} 分钟，建议人工确认。",
                        "duration_minutes",
                    )
                )

    working_hours = rules.get("working_hours", {})
    work_start = time_to_minutes(working_hours.get("start", "00:00"))
    work_end = time_to_minutes(working_hours.get("end", "23:59"))
    requested_start = time_to_minutes(request_item.time)
    if requested_start < work_start or requested_start >= work_end:
        risks.append(
            _risk(
                "OUTSIDE_WORKING_HOURS",
                "hard",
                f"请求时间 {request_item.time} 不在工作时段 {working_hours.get('start')}–{working_hours.get('end')} 内。",
                "time",
            )
        )
    elif isinstance(duration, int) and duration > 0 and requested_start + duration > work_end:
        risks.append(
            _risk(
                "OUTSIDE_WORKING_HOURS",
                "hard",
                "素材结束时间超出工作时段。",
                "time",
            )
        )

    if submitted_at is not None:
        lead_hours = rules.get("min_lead_time_hours")
        if lead_hours is not None:
            requested_dt = combine_local(
                request_item.date,
                request_item.time,
                tzinfo=submitted_at.tzinfo,
            )
            if requested_dt < submitted_at + timedelta(hours=float(lead_hours)):
                risks.append(
                    _risk(
                        "SHORT_LEAD_TIME",
                        "warning",
                        f"距提交不足 {lead_hours} 小时，建议人工确认。",
                        "time",
                    )
                )

    if any(risk["severity"] == "hard" for risk in risks):
        level = "blocked"
    elif any(risk["severity"] == "warning" for risk in risks):
        level = "warning"
    else:
        level = "none"
    return RuleReport(risk_level=level, risks=risks, missing_fields=missing)
