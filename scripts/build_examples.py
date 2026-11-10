from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
RUNS = ROOT / ".runs"
NOW = "2026-11-10T09:00:00+08:00"
SUBMITTED = "2026-11-10T09:00:00+08:00"


def request(request_id: str, material_id: str, date: str, time: str, constraint: str = "preferred"):
    return {
        "request_id": request_id,
        "submitted_at": SUBMITTED,
        "items": [
            {
                "material_id": material_id,
                "date": date,
                "time": time,
                "constraint": constraint,
                "channel": "wechat",
            }
        ],
    }


CASES = {
    "01-normal": {
        "request": request("REQ-NORMAL", "M00", "2026-11-12", "10:00"),
        "expect": "drafted",
    },
    "02-missing-info": {
        "request": request("REQ-MISSING", "M02", "2026-11-12", "14:00"),
        "expect": "needs_input",
    },
    "03-rule-risk": {
        "request": request("REQ-RULE", "M03", "2026-11-12", "15:00"),
        "expect": "blocked_rule",
    },
    "04-soft-conflict": {
        "request": request("REQ-SOFT", "M01", "2026-11-12", "18:00"),
        "expect": "pending_confirmation",
    },
    "05-hard-conflict": {
        "request": request("REQ-HARD", "M04", "2026-11-14", "18:00", "hard"),
        "expect": "blocked_conflict",
    },
    "06-duplicate": {
        "request": request("REQ-DUP", "M01", "2026-11-12", "18:00"),
        "expect": "existing",
    },
    "07-save-failure": {
        "request": request("REQ-FAIL", "M05", "2026-11-12", "21:00"),
        "expect": "save_failed",
        "fault": True,
    },
    "08-soft-warning": {
        "request": request("REQ-WARN", "M06", "2026-11-15", "10:00"),
        "expect": "pending_confirmation",
    },
    "09-confirm": {
        "request": request("REQ-CONFIRM", "M01", "2026-11-12", "18:00"),
        "expect": "user_confirmed",
        "confirm": True,
    },
}


def run(command: list[str], check: bool = False) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, env=env)
    if check and completed.returncode != 0:
        raise SystemExit(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
    return completed


def base_command(case: str, input_path: Path, state: Path, output: Path, trace: Path, engine: str):
    command = [
        sys.executable,
        "-m",
        "publish_agent",
        "run",
        "--request",
        str(input_path),
        "--data-dir",
        str(ROOT / "data"),
        "--state-dir",
        str(state),
        "--engine",
        engine,
        "--now",
        NOW,
        "--output",
        str(output),
        "--trace",
        str(trace),
    ]
    if CASES[case].get("fault"):
        command.extend(["--fault", "save_draft"])
    return command


def snapshot_store(state: Path, target: Path) -> None:
    source = state / "drafts.json"
    if source.exists():
        shutil.copyfile(source, target)
    else:
        target.write_text(
            json.dumps({"version": 1, "drafts": {}, "index": {}}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def main() -> int:
    EXAMPLES.mkdir(exist_ok=True)
    RUNS.mkdir(exist_ok=True)
    for case, spec in CASES.items():
        case_dir = EXAMPLES / case
        case_dir.mkdir(exist_ok=True)
        input_path = case_dir / "input.json"
        input_path.write_text(
            json.dumps(spec["request"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        state = RUNS / case
        if state.exists():
            shutil.rmtree(state)
        state.mkdir(parents=True)

        if case == "06-duplicate":
            first = run(
                base_command(
                    case,
                    input_path,
                    state,
                    case_dir / "output-run1.json",
                    case_dir / "trace-run1.json",
                    "offline",
                )
            )
            second = run(
                base_command(
                    case,
                    input_path,
                    state,
                    case_dir / "output-run2.json",
                    case_dir / "trace-run2.json",
                    "offline",
                )
            )
            trace_file = "trace-run2.json"
            output_file = "output-run2.json"
            print(f"{case}: first={first.returncode} second={second.returncode}")
        else:
            completed = run(
                base_command(
                    case,
                    input_path,
                    state,
                    case_dir / "output.json",
                    case_dir / "trace.json",
                    "offline",
                )
            )
            output_file = "output.json"
            trace_file = "trace.json"
            print(f"{case}: exit={completed.returncode}")
            if spec.get("confirm"):
                output = json.loads((case_dir / "output.json").read_text(encoding="utf-8"))
                draft_id = output["items"][0]["draft"]["id"]
                snapshot_store(state, case_dir / "store-before-confirm.json")
                confirmed = run(
                    [
                        sys.executable,
                        "-m",
                        "publish_agent",
                        "confirm",
                        "--state-dir",
                        str(state),
                        "--data-dir",
                        str(ROOT / "data"),
                        "--draft-id",
                        draft_id,
                        "--now",
                        NOW,
                    ],
                    check=True,
                )
                (case_dir / "confirm.json").write_text(confirmed.stdout, encoding="utf-8")
                snapshot_store(state, case_dir / "store-after-confirm.json")
                continue

        snapshot_store(state, case_dir / "store.json")
        manifest = {
            "case": case,
            "input": "input.json",
            "output": output_file,
            "trace": trace_file,
            "store": "store.json",
            "command": " ".join(
                base_command(
                    case,
                    input_path.relative_to(ROOT),
                    state.relative_to(ROOT),
                    Path(output_file),
                    Path(trace_file),
                    "offline",
                )
            ),
            "expected": spec["expect"],
        }
        (case_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
