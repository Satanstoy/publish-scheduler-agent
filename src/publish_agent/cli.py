from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .config import (
    DEFAULT_MAX_STEPS,
    DEFAULT_MAX_TOOL_CALLS,
    DEFAULT_OPENCODE_MODEL,
    DEFAULT_PROVIDER,
    ConfigError,
    default_opencode_cli_path,
    load_env,
    parse_now,
    write_json,
)
from .engines import ModelError, OfflineEngine, OpenAIEngine, OpenCodeLocalEngine
from .ledger import Ledger
from .models import PublishRequest, RequestValidationError, load_request
from .runner import AgentRunner, Limits
from .store import DraftConflictError, JsonStore, StoreError
from .tools import ToolRegistry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="publish-agent",
        description="素材审核与发布排期 Agent：只保存草稿，不发布。",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="运行一次排期请求")
    run.add_argument("--request", required=True, help="请求 JSON 路径")
    run.add_argument("--data-dir", default="data", help="素材/排期/规则目录")
    run.add_argument("--state-dir", required=True, help="运行时草稿目录")
    run.add_argument(
        "--engine",
        choices=["auto", "offline", "live", "opencode-local"],
        default=None,
        help="offline=确定性 mock；live=OpenAI-compatible；opencode-local=本机 OpenCode",
    )
    run.add_argument("--output", default=None, help="输出 JSON 路径")
    run.add_argument("--trace", default=None, help="Trace JSON 路径")
    run.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    run.add_argument("--max-tool-calls", type=int, default=DEFAULT_MAX_TOOL_CALLS)
    run.add_argument("--now", default=None, help="固定当前时间，ISO 8601")
    run.add_argument(
        "--fault",
        action="append",
        choices=["save_draft"],
        default=[],
        help="注入验收故障；目前支持 save_draft",
    )
    run.add_argument("--json", action="store_true", help="把完整输出 JSON 打到 stdout")
    run.add_argument("--opencode-cli", default=None, help="本机 opencode CLI 路径")
    run.add_argument("--opencode-provider", default=None)
    run.add_argument("--opencode-model", default=None)

    confirm = subparsers.add_parser("confirm", help="确认一份草稿；仍然不发布")
    confirm.add_argument("--draft-id", required=True)
    confirm.add_argument("--state-dir", required=True)
    confirm.add_argument("--data-dir", default="data")
    confirm.add_argument("--now", default=None)
    return parser


def _engine(args, request: PublishRequest, store: JsonStore):
    selected = args.engine or os.getenv("AGENT_ENGINE", "auto")
    if selected == "auto":
        selected = "live" if os.getenv("OPENAI_API_KEY") and os.getenv("OPENAI_MODEL") else "offline"
    if selected == "offline":
        return OfflineEngine()
    if selected == "live":
        api_key = os.getenv("OPENAI_API_KEY")
        model = os.getenv("OPENAI_MODEL")
        if not api_key or not model:
            raise ConfigError(
                "live 模式需要 OPENAI_API_KEY 和 OPENAI_MODEL；无 key 请用 --engine offline。"
            )
        return OpenAIEngine(
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=model,
        )
    if selected == "opencode-local":
        cli_path = args.opencode_cli or default_opencode_cli_path()
        if not cli_path:
            raise ConfigError(
                "opencode-local 模式需要 --opencode-cli 或 OPENCODE_CLI_PATH。"
            )
        return OpenCodeLocalEngine(
            cli_path=cli_path,
            provider=args.opencode_provider or os.getenv("OPENCODE_PROVIDER", DEFAULT_PROVIDER),
            model=args.opencode_model or os.getenv("OPENCODE_MODEL", DEFAULT_OPENCODE_MODEL),
        )
    raise ConfigError(f"未知 engine：{selected}")


def _run(args) -> int:
    request = load_request(args.request)
    faults = set(args.fault or [])
    store = JsonStore(args.data_dir, args.state_dir, faults=faults)
    now = parse_now(args.now)
    ledger = Ledger()
    tools = ToolRegistry(store, request, ledger, now=now, faults=faults)
    engine = _engine(args, request, store)
    runner = AgentRunner(
        engine=engine,
        tools=tools,
        request=request,
        ledger=ledger,
        limits=Limits(max_steps=args.max_steps, max_tool_calls=args.max_tool_calls),
    )
    output, trace = runner.run()

    output_path = Path(args.output) if args.output else Path(args.state_dir) / "output.json"
    trace_path = Path(args.trace) if args.trace else Path(args.state_dir) / "trace.json"
    write_json(output_path, output)
    write_json(trace_path, trace)

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"status={output['status']} stop_reason={output['stop_reason']} published=false")
        for item in output["items"]:
            draft = item["draft"]
            draft_text = draft["id"] or "-"
            print(
                f"{item['material_id']}  review={item['review_status']}  "
                f"schedule={item['schedule_status']}  draft={draft_text} "
                f"({draft['save_status']})"
            )
        print(f"output={output_path}")
        print(f"trace={trace_path}")
    item_failure = any(
        item["schedule_status"] in {"save_failed", "incomplete"}
        or item["review_status"] in {"blocked", "needs_input"}
        for item in output["items"]
    )
    return 0 if output["status"] == "completed" and not item_failure else 1


def _confirm(args) -> int:
    store = JsonStore(args.data_dir, args.state_dir)
    draft = store.confirm_draft(args.draft_id, now=parse_now(args.now))
    print(json.dumps(draft, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return _run(args)
        if args.command == "confirm":
            return _confirm(args)
        parser.error("unknown command")
        return 2
    except (RequestValidationError, json.JSONDecodeError, OSError) as exc:
        print(f"request error: {exc}", file=sys.stderr)
        return 2
    except (ConfigError, ModelError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except (DraftConflictError, StoreError) as exc:
        print(f"store error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
