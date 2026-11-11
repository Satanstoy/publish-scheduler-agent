from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_MAX_STEPS = 12
DEFAULT_MAX_TOOL_CALLS = 20
DEFAULT_PROVIDER = "opencode-go"
DEFAULT_OPENCODE_MODEL = "deepseek-v4.1-flash"


class ConfigError(RuntimeError):
    pass


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def parse_now(value: str | None) -> datetime:
    if not value:
        return datetime.now().astimezone()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed


def default_chat_engine() -> str | None:
    """Interactive chat prefers the standard URL/key/model path, then local evidence."""
    if os.getenv("OPENAI_API_KEY") and os.getenv("OPENAI_MODEL"):
        return "live"
    if default_opencode_cli_path():
        return "opencode-local"
    return None


def default_opencode_cli_path() -> str | None:
    configured = os.getenv("OPENCODE_CLI_PATH")
    if configured:
        return configured
    found = shutil.which("opencode")
    if found:
        return found
    base = Path.home() / "Library" / "Application Support" / "ai.opencode.desktop" / "cli"
    if base.exists():
        candidates = sorted(base.glob("*/opencode-cli"))
        if candidates:
            return str(candidates[-1])
    return None


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".json-", suffix=".tmp", dir=target.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
