from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from . import __version__
from .chat import ChatSession, run_chat
from .config import (
    DEFAULT_MAX_STEPS,
    DEFAULT_MAX_TOOL_CALLS,
    DEFAULT_OPENCODE_MODEL,
    DEFAULT_PROVIDER,
    ConfigError,
    default_chat_engine,
    default_opencode_cli_path,
    load_env,
    parse_now,
    write_json,
)
from .engines import ModelError, OfflineEngine, OpenAIEngine, OpenCodeLocalEngine
from .ledger import Ledger
from .models import PublishRequest, RequestValidationError, load_request
from .resolver import resolve_confirmation
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
    run.add_argument("--base-url", default=None, help="OpenAI-compatible base URL")
    run.add_argument("--model", default=None, help="模型 ID")
    run.add_argument("--api-key", default=None, help="建议用 OPENAI_API_KEY，避免 shell history")
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

    confirm_text = subparsers.add_parser(
        "confirm-text",
        help="用自然语言确认/拒绝唯一待确认草稿；多份时追问",
    )
    confirm_text.add_argument("--message", required=True)
    confirm_text.add_argument("--state-dir", required=True)
    confirm_text.add_argument("--data-dir", default="data")
    confirm_text.add_argument("--now", default=None)

    chat = subparsers.add_parser(
        "chat",
        help="交互式 bash agent；默认优先真实模型（opencode-local/live）",
    )
    chat.add_argument("--data-dir", default="data")
    chat.add_argument("--state-dir", required=True)
    chat.add_argument(
        "--engine",
        choices=["auto", "offline", "live", "opencode-local"],
        default=None,
    )
    chat.add_argument("--now", default=None)
    chat.add_argument("--base-url", default=None, help="OpenAI-compatible base URL")
    chat.add_argument("--model", default=None, help="模型 ID")
    chat.add_argument("--api-key", default=None, help="建议用 OPENAI_API_KEY，避免 shell history")
    chat.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    chat.add_argument("--max-tool-calls", type=int, default=DEFAULT_MAX_TOOL_CALLS)
    chat.add_argument(
        "--fault",
        action="append",
        choices=["save_draft"],
        default=[],
    )
    chat.add_argument("--opencode-cli", default=None)
    chat.add_argument("--opencode-provider", default=None)
    chat.add_argument("--opencode-model", default=None)
    return parser


def _engine(args, request: PublishRequest, store: JsonStore):
    selected = args.engine or os.getenv("AGENT_ENGINE", "auto")
    if selected == "auto":
        selected = "live" if os.getenv("OPENAI_API_KEY") and os.getenv("OPENAI_MODEL") else "offline"
    if selected == "offline":
        return OfflineEngine()
    if selected == "live":
        api_key = getattr(args, "api_key", None) or os.getenv("OPENAI_API_KEY")
        model = getattr(args, "model", None) or os.getenv("OPENAI_MODEL")
        base_url = getattr(args, "base_url", None) or os.getenv(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        )
        if not api_key or not model:
            raise ConfigError(
                "live 模式需要 base_url + api_key + model；"
                "请设置 OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL，"
                "或使用 --base-url/--api-key/--model。"
            )
        headers = (
            {"x-opencode-session": f"ses_{uuid4()}"}
            if "opencode.ai" in base_url
            else None
        )
        return OpenAIEngine(
            api_key=api_key,
            base_url=base_url,
            model=model,
            default_headers=headers,
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
        print(
            f"status={output['status']} outcome={output['outcome']} "
            f"stop_reason={output['stop_reason']} published=false"
        )
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
    return 0 if output["outcome"] in {"scheduled", "needs_confirmation"} else 1


def _confirm(args) -> int:
    store = JsonStore(args.data_dir, args.state_dir)
    try:
        draft = store.confirm_draft(args.draft_id, now=parse_now(args.now))
    except DraftConflictError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "CONFIRM_CONFLICT",
                    "message": str(exc),
                    "published": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(draft, ensure_ascii=False, indent=2))
    return 0


def _confirm_text(args) -> int:
    store = JsonStore(args.data_dir, args.state_dir)
    result = resolve_confirmation(args.message, store, now=parse_now(args.now))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def _chat(args) -> int:
    faults = set(args.fault or [])
    store = JsonStore(args.data_dir, args.state_dir, faults=faults)
    now = parse_now(args.now)
    if args.engine in {None, "auto"} and not os.getenv("AGENT_ENGINE"):
        has_key = bool(args.api_key or os.getenv("OPENAI_API_KEY"))
        has_model = bool(args.model or os.getenv("OPENAI_MODEL"))
        if has_key and has_model:
            args.engine = "live"
        else:
            selected = default_chat_engine()
            if selected is None:
                raise ConfigError(
                    "chat 默认要求真实模型：设置 OPENAI_BASE_URL/OPENAI_API_KEY/OPENAI_MODEL，"
                    "或 OPENCODE_CLI_PATH；只验证流程请显式使用 --engine offline。"
                )
            args.engine = selected

    def engine_factory(request: PublishRequest, session_store: JsonStore):
        return _engine(args, request, session_store)

    session = ChatSession(
        store=store,
        engine_factory=engine_factory,
        now=now,
        max_steps=args.max_steps,
        max_tool_calls=args.max_tool_calls,
        faults=faults,
    )
    run_chat(session)
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
        if args.command == "confirm-text":
            return _confirm_text(args)
        if args.command == "chat":
            return _chat(args)
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
