from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .models import PublishRequest, RequestItem


@dataclass
class MaterialLedger:
    material_id: str
    material_found: bool | None = None
    material: dict[str, Any] | None = None
    missing_fields: list[str] = field(default_factory=list)
    rules: dict[str, Any] | None = None
    slots: dict[str, Any] | None = None
    save_attempts: list[dict[str, Any]] = field(default_factory=list)
    save_args: dict[str, Any] | None = None
    save_result: dict[str, Any] | None = None


class Ledger:
    def __init__(self) -> None:
        self._entries: dict[str, MaterialLedger] = {}

    def get(self, material_id: str) -> MaterialLedger:
        if material_id not in self._entries:
            self._entries[material_id] = MaterialLedger(material_id=material_id)
        return self._entries[material_id]

    def record_material(
        self,
        material_id: str,
        found: bool,
        material: dict[str, Any] | None = None,
        missing_fields: list[str] | None = None,
    ) -> None:
        entry = self.get(material_id)
        entry.material_found = found
        entry.material = copy.deepcopy(material) if material is not None else None
        entry.missing_fields = list(missing_fields or [])

    def record_rules(self, material_id: str, report) -> None:
        self.get(material_id).rules = report.to_dict()

    def record_slots(self, material_id: str, result: dict[str, Any]) -> None:
        self.get(material_id).slots = copy.deepcopy(result)

    def record_save(self, material_id: str, args: dict[str, Any], result: dict[str, Any]) -> None:
        entry = self.get(material_id)
        entry.save_args = copy.deepcopy(args)
        entry.save_result = copy.deepcopy(result)
        entry.save_attempts.append(
            {
                "arguments": copy.deepcopy(args),
                "result": copy.deepcopy(result),
            }
        )

    def terminal_status(self, material_id: str, item: RequestItem) -> str | None:
        entry = self.get(material_id)
        if entry.material_found is False:
            return "needs_input"
        if entry.save_result is not None:
            if entry.save_result.get("ok"):
                draft = (entry.save_result.get("data") or {}).get("draft") or {}
                if draft.get("confirmation_status") == "not_required":
                    return "drafted"
                return "pending_confirmation"
            if entry.save_result.get("code") == "SAVE_FAILED":
                return "save_failed"
        if entry.rules is not None:
            if entry.rules.get("missing_fields"):
                return "needs_input"
            if entry.rules.get("risk_level") == "blocked":
                return "blocked_rule"
        if entry.slots is not None and entry.slots.get("conflict"):
            if item.constraint == "hard" or not entry.slots.get("alternatives"):
                return "blocked_conflict"
        return None

    def missing_terminal(self, request: PublishRequest) -> list[str]:
        missing: list[str] = []
        for item in request.items:
            if self.terminal_status(item.material_id, item) is None:
                missing.append(item.material_id)
        return missing

    def all_terminal(self, request: PublishRequest) -> bool:
        return not self.missing_terminal(request)
