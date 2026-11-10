from __future__ import annotations

import io
import json

from conftest import DATA_DIR, NOW, SUBMITTED_AT, new_store
from publish_agent.chat import ChatSession, parse_request_line
from publish_agent.cli import main
from publish_agent.engines import OfflineEngine


def _session(tmp_path):
    store = new_store(tmp_path)

    def factory(request, session_store):
        return OfflineEngine()

    return ChatSession(store, factory, NOW)


def test_parse_request_line_variants():
    assert parse_request_line("排 M01 2026-11-12 18:00") == {
        "material_id": "M01",
        "date": "2026-11-12",
        "time": "18:00",
        "constraint": "preferred",
    }
    assert parse_request_line("M00 10:00")["date"] == "2026-11-12"
    assert parse_request_line("排 M04 2026-11-14 18:00 hard")["constraint"] == "hard"
    assert parse_request_line("排 M01 18:00 硬性")["constraint"] == "hard"
    assert parse_request_line("帮我看看") is None


def test_chat_soft_conflict_then_confirm(tmp_path):
    session = _session(tmp_path)
    reply, should_exit = session.handle("排 M01 2026-11-12 18:00")
    assert should_exit is False
    assert "pending_confirmation" in reply
    assert "DRF-" in reply
    assert "published=false" in reply
    assert len(session.store.pending_drafts()) == 1

    reply, should_exit = session.handle("可以")
    assert should_exit is False
    assert "confirmed" in reply
    assert "published=false" in reply
    assert session.store.pending_drafts() == []
    assert session.store.list_drafts()[0]["confirmation_status"] == "user_confirmed"


def test_chat_multiple_pending_needs_clarification(tmp_path):
    session = _session(tmp_path)
    session.handle("排 M01 2026-11-12 18:00")
    session.handle("排 M06 2026-11-15 10:00")
    assert len(session.store.pending_drafts()) == 2
    reply, _ = session.handle("继续")
    assert "needs_clarification" in reply
    assert len(session.store.pending_drafts()) == 2


def test_chat_hard_conflict_has_no_draft(tmp_path):
    session = _session(tmp_path)
    reply, _ = session.handle("排 M04 2026-11-14 18:00 hard")
    assert "blocked_conflict" in reply
    assert "published=false" in reply
    assert session.store.list_drafts() == []
    reply, _ = session.handle("可以")
    assert "当前没有待确认草稿" in reply


def test_chat_help_state_exit(tmp_path):
    session = _session(tmp_path)
    help_reply, _ = session.handle("帮助")
    assert "排 M01" in help_reply
    state_reply, _ = session.handle("状态")
    assert "没有草稿" in state_reply
    exit_reply, should_exit = session.handle("退出")
    assert should_exit is True
    assert "未发布" in exit_reply


def test_chat_conditional_consent_not_confirmed(tmp_path):
    session = _session(tmp_path)
    session.handle("排 M01 2026-11-12 18:00")
    reply, _ = session.handle("可以，但改成20:00")
    assert "needs_clarification" in reply
    assert len(session.store.pending_drafts()) == 1


def test_chat_cli_piped_session(tmp_path, monkeypatch, capsys):
    import sys

    stdin = io.StringIO("排 M01 2026-11-12 18:00\n可以\n状态\n退出\n")
    monkeypatch.setattr(sys, "stdin", stdin)
    code = main(
        [
            "chat",
            "--data-dir",
            str(DATA_DIR),
            "--state-dir",
            str(tmp_path / "chat-state"),
            "--engine",
            "offline",
            "--now",
            SUBMITTED_AT,
        ]
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "待确认草稿 1 份" in output
    assert "confirmed" in output
    assert "published=false" in output
    assert "confirmation=user_confirmed" in output
    state = json.loads((tmp_path / "chat-state" / "drafts.json").read_text(encoding="utf-8"))
    assert len(state["drafts"]) == 1
    assert next(iter(state["drafts"].values()))["published"] is False
