from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"

SIMPLE_OFFLINE = [
    "01-normal",
    "02-missing-info",
    "03-rule-risk",
    "04-soft-conflict",
    "05-hard-conflict",
    "07-save-failure",
    "08-soft-warning",
    "10-step-limit",
]
LIVE_CASES = [
    "01-normal",
    "02-missing-info",
    "03-rule-risk",
    "04-soft-conflict",
    "05-hard-conflict",
    "07-save-failure",
]


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_trace_complete(trace: dict) -> None:
    assert trace["steps"], "trace has no steps"
    results = [step for step in trace["steps"] if step["type"] == "tool_result"]
    assert results, "trace has no tool results"
    assert all(step.get("next_action") is not None for step in results)


def test_offline_examples_have_input_output_trace_store_manifest():
    for case in SIMPLE_OFFLINE:
        directory = EXAMPLES / case
        for name in ["input.json", "output.json", "trace.json", "store.json", "manifest.json"]:
            assert (directory / name).exists(), f"{case} missing {name}"
        output = _read(directory / "output.json")
        assert output["published"] is False
        _assert_trace_complete(_read(directory / "trace.json"))

    duplicate = EXAMPLES / "06-duplicate"
    for name in [
        "input.json",
        "output-run1.json",
        "output-run2.json",
        "trace-run1.json",
        "trace-run2.json",
        "store.json",
        "manifest.json",
    ]:
        assert (duplicate / name).exists(), f"06-duplicate missing {name}"
    assert _read(duplicate / "output-run1.json")["published"] is False
    assert _read(duplicate / "output-run2.json")["published"] is False
    _assert_trace_complete(_read(duplicate / "trace-run1.json"))
    _assert_trace_complete(_read(duplicate / "trace-run2.json"))

    confirm = EXAMPLES / "09-confirm"
    for name in [
        "input.json",
        "output.json",
        "trace.json",
        "store-before-confirm.json",
        "store-after-confirm.json",
        "confirm.json",
        "manifest.json",
    ]:
        assert (confirm / name).exists(), f"09-confirm missing {name}"
    confirmed = _read(confirm / "confirm.json")
    assert confirmed["confirmation_status"] == "user_confirmed"
    assert confirmed["published"] is False


def test_live_examples_are_real_model_runs_with_complete_artifacts():
    for case in LIVE_CASES:
        directory = EXAMPLES / "live" / case
        for name in ["input.json", "output.json", "trace.json", "store.json", "manifest.json"]:
            assert (directory / name).exists(), f"live/{case} missing {name}"
        manifest = _read(directory / "manifest.json")
        assert manifest["real_model_call"] is True
        assert manifest["engine"] == "live-opencode-local"
        assert manifest["model"].startswith("opencode-go/")
        trace = _read(directory / "trace.json")
        assert trace["engine"] == "live-opencode-local"
        assert any(
            step["type"] == "model_decision" and step.get("tool_calls")
            for step in trace["steps"]
        )
        _assert_trace_complete(trace)
        assert _read(directory / "output.json")["published"] is False


def test_test_results_file_exists():
    assert (EXAMPLES / "test-results.txt").exists()
