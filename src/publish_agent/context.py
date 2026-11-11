from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .store import JsonStore


@dataclass
class ChatMessage:
    role: str
    content: str
    timestamp: str


@dataclass
class ChatContext:
    materials: dict[str, dict[str, Any]]
    now: datetime
    pending: list[dict[str, Any]] = field(default_factory=list)
    last_run: dict[str, Any] | None = None
    history: list[ChatMessage] = field(default_factory=list)
    max_history: int = 12

    def refresh(self, store: JsonStore) -> None:
        self.pending = store.pending_drafts()
        self.materials = store.materials

    def add_user(self, text: str) -> None:
        self._add("user", text)

    def add_assistant(self, text: str) -> None:
        self._add("assistant", text)

    def _add(self, role: str, text: str) -> None:
        self.history.append(
            ChatMessage(
                role=role,
                content=text,
                timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
            )
        )
        self.history = self.history[-self.max_history :]

    def material_lines(self) -> list[str]:
        return [
            f"- {material_id}: {material.get('title', '')}"
            for material_id, material in self.materials.items()
        ]

    def pending_lines(self) -> list[str]:
        if not self.pending:
            return ["- 无"]
        lines = []
        for draft in self.pending:
            slot = draft.get("slot") or {}
            lines.append(
                f"- {draft.get('material_id')} {draft.get('id')} "
                f"{slot.get('date')} {slot.get('start')}-{slot.get('end')}"
            )
        return lines

    def history_lines(self) -> list[str]:
        if not self.history:
            return ["- 无"]
        return [
            f"- [{message.timestamp}] {message.role}: {message.content}"
            for message in self.history
        ]

    def last_run_summary(self) -> str:
        if not self.last_run:
            return "无"
        items = self.last_run.get("items", [])
        if not items:
            return f"status={self.last_run.get('status')} outcome={self.last_run.get('outcome')}"
        item = items[0]
        return (
            f"{item.get('material_id')} status={item.get('status')} "
            f"schedule={item.get('schedule_status')} "
            f"draft={((item.get('draft') or {}).get('id')) or '-'}"
        )
