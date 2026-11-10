from __future__ import annotations

import json

from conftest import DATA_DIR, SUBMITTED_AT, read_json, write_json
from publish_agent.cli import main


def _request_file(tmp_path, material_id="M00", date="2026-11-12", time="10:00", constraint="preferred"):
    path = tmp_path / "request.json"
    write_json(
        path,
        {
            "request_id": "REQ-CLI",
            "submitted_at": SUBMITTED_AT,
            "items": [
                {
                    "material_id": material_id,
                    "date": date,
                    "time": time,
                    "constraint": constraint,
                }
            ],
        },
    )
    return path


def _run_args(tmp_path, request_path, *extra):
    return [
        "run",
        "--request",
        str(request_path),
        "--data-dir",
        str(DATA_DIR),
        "--state-dir",
        str(tmp_path / "state"),
        "--engine",
        "offline",
        "--now",
        SUBMITTED_AT,
        "--output",
        str(tmp_path / "output.json"),
        "--trace",
        str(tmp_path / "trace.json"),
        *extra,
    ]


def test_cli_run_writes_output_and_trace(tmp_path):
    request_path = _request_file(tmp_path)
    assert main(_run_args(tmp_path, request_path)) == 0
    output = read_json(tmp_path / "output.json")
    trace = read_json(tmp_path / "trace.json")
    assert output["status"] == "completed"
    assert output["items"][0]["draft"]["save_status"] == "saved"
    assert trace["engine"] == "offline-mock"
    assert trace["steps"]


def test_cli_duplicate_twice(tmp_path):
    request_path = _request_file(tmp_path, material_id="M01", time="18:00")
    args = _run_args(tmp_path, request_path)
    assert main(args) == 0
    first = read_json(tmp_path / "output.json")
    assert main(args) == 0
    second = read_json(tmp_path / "output.json")
    assert first["items"][0]["draft"]["id"] == second["items"][0]["draft"]["id"]
    assert second["items"][0]["draft"]["save_status"] == "existing"
    assert second["items"][0]["draft"]["deduplicated"] is True


def test_cli_confirm_keeps_draft_unpublished(tmp_path, capsys):
    request_path = _request_file(tmp_path, material_id="M01", time="18:00")
    assert main(_run_args(tmp_path, request_path)) == 0
    output = read_json(tmp_path / "output.json")
    draft_id = output["items"][0]["draft"]["id"]
    capsys.readouterr()
    code = main(
        [
            "confirm",
            "--state-dir",
            str(tmp_path / "state"),
            "--data-dir",
            str(DATA_DIR),
            "--draft-id",
            draft_id,
            "--now",
            SUBMITTED_AT,
        ]
    )
    assert code == 0
    confirmed = json.loads(capsys.readouterr().out)
    assert confirmed["confirmation_status"] == "user_confirmed"
    assert confirmed["status"] == "draft"
    assert confirmed["published"] is False


def test_cli_confirm_text_affirmative(tmp_path, capsys):
    request_path = _request_file(tmp_path, material_id="M01", time="18:00")
    assert main(_run_args(tmp_path, request_path)) == 0
    capsys.readouterr()
    code = main(
        [
            "confirm-text",
            "--state-dir",
            str(tmp_path / "state"),
            "--data-dir",
            str(DATA_DIR),
            "--message",
            "可以",
            "--now",
            SUBMITTED_AT,
        ]
    )
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "confirmed"
    assert result["draft"]["status"] == "draft"
    assert result["published"] is False


def test_cli_save_failure_exit_code(tmp_path):
    request_path = _request_file(tmp_path, material_id="M05", time="21:00")
    code = main(_run_args(tmp_path, request_path, "--fault", "save_draft"))
    assert code == 1
    output = read_json(tmp_path / "output.json")
    assert output["items"][0]["schedule_status"] == "save_failed"
    assert output["items"][0]["published"] is False


def test_cli_invalid_request_exit_2(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    code = main(
        [
            "run",
            "--request",
            str(bad),
            "--data-dir",
            str(DATA_DIR),
            "--state-dir",
            str(tmp_path / "state"),
            "--engine",
            "offline",
        ]
    )
    assert code == 2
    assert "request" in capsys.readouterr().err.lower()
