from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from publish_agent.ledger import Ledger
from publish_agent.models import PublishRequest
from publish_agent.store import JsonStore

DATA_DIR = ROOT / "data"
NOW = datetime.fromisoformat("2026-11-10T09:00:00+08:00")
SUBMITTED_AT = "2026-11-10T09:00:00+08:00"


def item(
    material_id: str,
    date: str = "2026-11-12",
    time: str = "10:00",
    constraint: str = "preferred",
    channel: str = "wechat",
    note: str = "",
) -> dict:
    return {
        "material_id": material_id,
        "date": date,
        "time": time,
        "constraint": constraint,
        "channel": channel,
        "note": note,
    }


def make_request(
    items: list[dict] | None = None,
    request_id: str = "REQ-T",
    submitted_at: str | None = SUBMITTED_AT,
) -> PublishRequest:
    return PublishRequest.from_dict(
        {
            "request_id": request_id,
            "submitted_at": submitted_at,
            "items": items or [item("M00")],
        }
    )


def single_request(material_id: str, **kwargs) -> PublishRequest:
    return make_request([item(material_id, **kwargs)])


def new_store(tmp_path: Path, faults: set[str] | None = None) -> JsonStore:
    return JsonStore(DATA_DIR, tmp_path / "state", faults=faults or set())


def run_offline(
    request: PublishRequest,
    store: JsonStore,
    max_steps: int = 12,
    max_tool_calls: int = 20,
    now: datetime = NOW,
):
    from publish_agent.engines import OfflineEngine
    from publish_agent.runner import AgentRunner, Limits
    from publish_agent.tools import ToolRegistry

    ledger = Ledger()
    tools = ToolRegistry(store, request, ledger, now=now)
    engine = OfflineEngine()
    runner = AgentRunner(
        engine=engine,
        tools=tools,
        request=request,
        ledger=ledger,
        limits=Limits(max_steps=max_steps, max_tool_calls=max_tool_calls),
    )
    return runner.run()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
