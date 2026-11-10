from __future__ import annotations

import json
from typing import Any

from .tools import TOOL_SCHEMAS

SYSTEM_PROMPT = """你是内容团队的“素材审核与发布排期 Agent”。

你的职责：读取素材、检查规则、查询时段、保存排期草稿。你只能保存草稿，绝不能发布。

硬性规则：
1. 工具注册表里没有发布工具；不要声称任何内容已发布。
2. 必须处理请求中的每一个素材，不能跳过。
3. 建议顺序：get_material -> check_rules -> query_slots -> save_draft；工具报错时根据错误调整。
4. 缺字段或硬规则违规时，不要查询时段、不要保存草稿，直接 finish 并让报告记录原因。
5. hard 约束的发布时间不可更改。时段冲突时，不得保存替代时段；只能报告备选并结束该素材。
6. preferred 约束冲突时，可以选择 query_slots 返回的替代时段，但必须保存为 pending_user_confirmation。
7. 保存失败必须如实报告，不能声称成功。
8. 相同请求重复保存由工具幂等处理；返回 existing 时如实报告。
9. 只有所有素材都有终态后才调用 finish；finish 可能被拒绝，按返回的 missing 列表继续。
10. 每次工具调用在 reason 里写一句不超过 40 字的依据，不写思维过程。
"""

LOCAL_SYSTEM_PROMPT = """你是“素材审核与发布排期 Agent”的决策器。你只能通过工具推进任务，不能发布。

规则：
1. 必须处理请求中的每个素材。
2. 顺序：get_material -> check_rules -> query_slots -> save_draft；工具报错时根据错误调整。
3. 缺字段、硬规则违规：不查时段、不保存草稿，结束该素材。
4. hard 约束冲突：不得保存替代时段，只报告备选。
5. preferred 约束冲突：只能保存 query_slots 返回的替代时段，并标记 pending_user_confirmation。
6. 保存失败必须如实报告，不能声称成功。
7. 相同请求重复保存返回 existing 时，如实报告。
8. 所有素材都有终态后才调用 finish；finish 可能被拒绝，按 missing 列表继续。
9. 每次只输出一个 JSON 对象，不要 Markdown、解释或思维过程。
"""


def render_tool_list(schemas: list[dict[str, Any]] = TOOL_SCHEMAS) -> str:
    lines: list[str] = []
    for schema in schemas:
        function = schema["function"]
        parameters = function.get("parameters", {}).get("properties", {})
        args = ", ".join(parameters.keys())
        lines.append(f"- {function['name']}({args})：{function.get('description', '')}")
    return "\n".join(lines)


def build_user_prompt(request) -> str:
    return (
        "请求如下：\n"
        f"{json.dumps(request.to_dict(), ensure_ascii=False, indent=2)}\n\n"
        "请逐个素材处理。每个素材都要有明确终态；全部完成后再调用 finish。"
    )


def build_local_prompt(
    request,
    ledger,
    transcript,
    nudges: list[str],
    schemas: list[dict[str, Any]] = TOOL_SCHEMAS,
) -> str:
    status_lines: list[str] = []
    for item in request.items:
        terminal = ledger.terminal_status(item.material_id, item)
        status_lines.append(f"- {item.material_id}: {terminal or '待处理'}")

    observation_lines: list[str] = []
    for index, exchange in enumerate(transcript, 1):
        observation = {
            "tool": exchange.call.name,
            "arguments": exchange.call.arguments,
            "result": exchange.result.to_dict(),
        }
        observation_lines.append(
            f"观察 {index}: {json.dumps(observation, ensure_ascii=False)}"
        )

    parts = [
        LOCAL_SYSTEM_PROMPT,
        "## 可用工具\n" + render_tool_list(schemas),
        "## 请求\n" + json.dumps(request.to_dict(), ensure_ascii=False, indent=2),
        "## 当前状态\n" + "\n".join(status_lines),
    ]
    if observation_lines:
        parts.append("## 工具观察\n" + "\n".join(observation_lines))
    if nudges:
        parts.append("## 纠偏提示\n" + "\n".join(f"- {nudge}" for nudge in nudges))
    parts.append(
        "## 下一步\n"
        "只输出一个 JSON 对象，格式：\n"
        '{"tool":"工具名","arguments":{...},"reason":"一句话依据"}\n'
        "不要输出 Markdown 或其他文字。"
    )
    return "\n\n".join(parts)
