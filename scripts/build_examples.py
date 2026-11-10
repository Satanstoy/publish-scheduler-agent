from __future__ import annotations

import json
import os
import shutil
import subprocess
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
    "10-step-limit": {
        "request": request("REQ-LIMIT", "M00", "2026-11-12", "10:00"),
        "expect": "incomplete",
        "max_steps": 1,
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
        "python3",
        "run.py",
        "run",
        "--request",
        str(input_path),
        "--data-dir",
        "data",
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
    if CASES[case].get("max_steps") is not None:
        command.extend(["--max-steps", str(CASES[case]["max_steps"])])
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
                    input_path.relative_to(ROOT),
                    state.relative_to(ROOT),
                    (case_dir / "output-run1.json").relative_to(ROOT),
                    (case_dir / "trace-run1.json").relative_to(ROOT),
                    "offline",
                )
            )
            second = run(
                base_command(
                    case,
                    input_path.relative_to(ROOT),
                    state.relative_to(ROOT),
                    (case_dir / "output-run2.json").relative_to(ROOT),
                    (case_dir / "trace-run2.json").relative_to(ROOT),
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
                    input_path.relative_to(ROOT),
                    state.relative_to(ROOT),
                    (case_dir / "output.json").relative_to(ROOT),
                    (case_dir / "trace.json").relative_to(ROOT),
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
                        "python3",
                        "run.py",
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
                run_command = " ".join(
                    base_command(
                        case,
                        input_path.relative_to(ROOT),
                        state.relative_to(ROOT),
                        (case_dir / "output.json").relative_to(ROOT),
                        (case_dir / "trace.json").relative_to(ROOT),
                        "offline",
                    )
                )
                manifest = {
                    "case": case,
                    "input": "input.json",
                    "output": "output.json",
                    "trace": "trace.json",
                    "store_before_confirm": "store-before-confirm.json",
                    "store_after_confirm": "store-after-confirm.json",
                    "confirm": "confirm.json",
                    "run_command": run_command,
                    "confirm_command": (
                        "python3 run.py confirm --state-dir "
                        f"{state.relative_to(ROOT)} --data-dir data "
                        f"--draft-id {draft_id} --now {NOW}"
                    ),
                    "expected": "pending_confirmation -> user_confirmed",
                }
                (case_dir / "manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
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
                    (case_dir / output_file).relative_to(ROOT),
                    (case_dir / trace_file).relative_to(ROOT),
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
