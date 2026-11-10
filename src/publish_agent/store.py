from __future__ import annotations

import copy
import fcntl
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .models import time_to_minutes


class StoreError(RuntimeError):
    """Raised when the JSON store cannot be read or written."""


class InjectedSaveError(StoreError):
    """Deterministic fault used by acceptance tests."""


class DraftConflictError(StoreError):
    """Raised when a draft cannot be confirmed because its slot is now busy."""


class JsonStore:
    def __init__(self, data_dir: str | Path, state_dir: str | Path, faults: set[str] | None = None):
        self.data_dir = Path(data_dir)
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.faults = set(faults or [])
        self.materials = {
            material["id"]: material
            for material in self._load("materials.json").get("materials", [])
        }
        schedule = self._load("schedule.json")
        self.schedule_entries = list(schedule.get("entries", []))
        self.rules = self._load("rules.json")
        self.drafts_path = self.state_dir / "drafts.json"
        self.lock_path = self.state_dir / ".drafts.lock"
        self._thread_lock = threading.RLock()
        self._drafts = self._load_drafts()

    def _load(self, name: str) -> dict[str, Any]:
        path = self.data_dir / name
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise StoreError(f"missing data file: {path}") from exc
        except json.JSONDecodeError as exc:
            raise StoreError(f"invalid JSON in {path}: {exc}") from exc

    def _empty_drafts(self) -> dict[str, Any]:
        return {"version": 1, "drafts": {}, "index": {}}

    def _load_drafts(self) -> dict[str, Any]:
        if not self.drafts_path.exists():
            return self._empty_drafts()
        try:
            data = json.loads(self.drafts_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StoreError(f"corrupt drafts file: {self.drafts_path}") from exc
        if not isinstance(data, dict):
            raise StoreError("drafts file must contain an object")
        data.setdefault("version", 1)
        data.setdefault("drafts", {})
        data.setdefault("index", {})
        return data

    def _write_drafts(self, data: dict[str, Any]) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=".drafts-", suffix=".tmp", dir=self.state_dir)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.drafts_path)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._thread_lock:
            with open(self.lock_path, "a+", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    self._drafts = self._load_drafts()
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def get_material(self, material_id: str) -> dict[str, Any] | None:
        return self.materials.get(material_id)

    def count_drafts(self) -> int:
        with self._locked():
            return len(self._drafts["drafts"])

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        with self._locked():
            draft = self._drafts["drafts"].get(draft_id)
            return copy.deepcopy(draft) if draft else None

    def get_draft_by_request_key(self, request_key: str) -> dict[str, Any] | None:
        with self._locked():
            draft_id = self._drafts["index"].get(request_key)
            if not draft_id:
                return None
            draft = self._drafts["drafts"].get(draft_id)
            return copy.deepcopy(draft) if draft else None

    def _busy_entries_unlocked(
        self,
        day: str,
        channel: str,
        exclude_request_key: str | None = None,
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for entry in self.schedule_entries:
            if entry.get("status") in {"cancelled", "canceled"}:
                continue
            if entry.get("date") != day or entry.get("channel", "wechat") != channel:
                continue
            entries.append(
                {
                    "entry_id": entry.get("id"),
                    "material_id": entry.get("material_id"),
                    "start": entry.get("start"),
                    "end": entry.get("end"),
                    "source": "schedule",
                    "channel": entry.get("channel", "wechat"),
                    "request_key": None,
                }
            )
        for draft in self._drafts["drafts"].values():
            if draft.get("status") != "draft":
                continue
            slot = draft.get("slot") or {}
            if slot.get("date") != day:
                continue
            draft_channel = draft.get("channel") or self.materials.get(
                draft.get("material_id", ""), {}
            ).get("channel", "wechat")
            if draft_channel != channel:
                continue
            if exclude_request_key and draft.get("request_key") == exclude_request_key:
                continue
            entries.append(
                {
                    "entry_id": draft.get("id"),
                    "material_id": draft.get("material_id"),
                    "start": slot.get("start"),
                    "end": slot.get("end"),
                    "source": "draft",
                    "channel": channel,
                    "request_key": draft.get("request_key"),
                }
            )
        return entries

    def busy_entries(
        self,
        day: str,
        channel: str,
        exclude_request_key: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._locked():
            return self._busy_entries_unlocked(day, channel, exclude_request_key)

    def create_draft(self, draft: dict[str, Any], request_key: str) -> tuple[dict[str, Any], bool]:
        with self._locked():
            existing_id = self._drafts["index"].get(request_key)
            if existing_id:
                existing = self._drafts["drafts"].get(existing_id)
                if existing:
                    return copy.deepcopy(existing), False
            if "save_draft" in self.faults:
                raise InjectedSaveError("injected save_draft failure")
            draft_id = draft["id"]
            self._drafts["drafts"][draft_id] = copy.deepcopy(draft)
            self._drafts["index"][request_key] = draft_id
            try:
                self._write_drafts(self._drafts)
            except OSError as exc:
                # Roll back the in-memory change; the on-disk file is unchanged.
                self._drafts["drafts"].pop(draft_id, None)
                self._drafts["index"].pop(request_key, None)
                raise StoreError(f"failed to write drafts: {exc}") from exc
            return copy.deepcopy(draft), True

    def confirm_draft(self, draft_id: str, now: datetime | None = None) -> dict[str, Any]:
        with self._locked():
            draft = self._drafts["drafts"].get(draft_id)
            if draft is None:
                raise StoreError(f"draft not found: {draft_id}")
            if draft.get("confirmation_status") == "user_confirmed":
                return copy.deepcopy(draft)
            slot = draft.get("slot") or {}
            day = slot.get("date")
            channel = draft.get(
                "channel",
                self.materials.get(draft.get("material_id", ""), {}).get("channel", "wechat"),
            )
            for entry in self._busy_entries_unlocked(
                day,
                channel,
                exclude_request_key=draft.get("request_key"),
            ):
                if _overlaps(slot.get("start"), slot.get("end"), entry.get("start"), entry.get("end")):
                    raise DraftConflictError(
                        f"draft {draft_id} conflicts with {entry.get('entry_id')} "
                        f"({entry.get('start')}-{entry.get('end')})"
                    )
            draft["confirmation_status"] = "user_confirmed"
            draft["confirmed_at"] = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
            draft["published"] = False
            try:
                self._write_drafts(self._drafts)
            except OSError as exc:
                raise StoreError(f"failed to write drafts: {exc}") from exc
            return copy.deepcopy(draft)


def _overlaps(start_a: str | None, end_a: str | None, start_b: str | None, end_b: str | None) -> bool:
    if not all([start_a, end_a, start_b, end_b]):
        return False
    a1, a2 = time_to_minutes(start_a), time_to_minutes(end_a)
    b1, b2 = time_to_minutes(start_b), time_to_minutes(end_b)
    return max(a1, b1) < min(a2, b2)
