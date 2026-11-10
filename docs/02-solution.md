# 02 方案设计

## 1. 总体架构

```text
┌──────────────────────────────────────────────────────────────┐
│ CLI: run / confirm                                           │
│  - 解析参数、加载请求、选择 engine、写 output/trace          │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│ AgentRunner                                                  │
│  - 循环：engine.next_action -> tools.execute -> ledger       │
│  - 执行步数 / 工具调用上限                                   │
│  - finish 覆盖率校验                                         │
└───────────────┬──────────────────────────────┬───────────────┘
                │                              │
┌───────────────▼──────────────┐  ┌────────────▼──────────────┐
│ Engine                       │  │ ToolRegistry              │
│  - offline-mock              │  │  get_material             │
│  - live-openai (function)    │  │  check_rules              │
│  - live-opencode-local       │  │  query_slots              │
│    (JSON text protocol)      │  │  save_draft               │
└──────────────────────────────┘  │  finish                   │
                                  └────────────┬──────────────┘
                                               │
┌──────────────────────────────────────────────▼───────────────┐
│ Ledger + Store + Rules + Slots                               │
│  - 工具观察记录、终态推导                                    │
│  - 素材/规则/已有排期读取                                    │
│  - 草稿写入、判重索引、文件锁、原子替换                      │
└──────────────────────────────────────────────────────────────┘
```

核心原则：**模型负责选择动作，工具负责执行和验证，ledger 负责记录事实，报告由 ledger 生成。** 模型不能直接写报告里的保存状态，避免“模型说自己保存成功”这类虚报。

## 2. 目录

```text
src/publish_agent/
  cli.py          # argparse、run/confirm、输出与退出码
  models.py       # 请求/素材/时段/状态枚举/校验
  store.py        # JSON 存储、草稿、判重、锁、原子写、故障注入
  rules.py        # 规则引擎
  slots.py        # 冲突判断、替代时段搜索
  tools.py        # 工具注册表、参数校验、前置条件、工具结果
  ledger.py       # 每素材观察记录、终态推导、覆盖率
  report.py       # 由 ledger 生成最终输出
  runner.py       # Agent 循环、上限、finish 校验、trace 组装
  engines.py      # offline / OpenAI-compatible / OpenCode local
  prompts.py      # 系统提示、JSON 决策协议
  trace.py        # Trace 记录与 next_action 回填
  config.py       # 环境变量、默认值、路径
```

## 3. 数据与状态

### 3.1 素材

素材来自 `data/materials.json`，关键字段：

```json
{
  "id": "M01",
  "title": "城市夜跑指南",
  "summary": "三条夜跑路线和装备建议",
  "content_version": "v2",
  "status": "pending",
  "channel": "wechat",
  "duration_minutes": 60,
  "metadata": {
    "authorized": true,
    "copyright_owner": "小影内容部",
    "language": "zh-CN",
    "contains_minors": false,
    "file_type": "video"
  }
}
```

`missing_fields` 按 dotted path 计算，例如 `metadata.copyright_owner`。

### 3.2 请求

`PublishRequest` 校验：

- `items` 数量 1–5。
- `material_id` 不重复。
- `date` 为 `YYYY-MM-DD`，`time` 为 `HH:MM`。
- `constraint` 只能是 `hard` / `preferred`。
- `channel` 默认 `wechat`。
- `submitted_at` 可选，必须是 ISO 8601。

### 3.3 草稿

```json
{
  "id": "DRF-1A2B3C4D5E",
  "request_key": "RQ-...",
  "material_id": "M01",
  "content_version": "v2",
  "slot": {
    "date": "2026-11-12",
    "start": "19:00",
    "end": "20:00",
    "timezone": "Asia/Shanghai"
  },
  "requested_slot": {
    "date": "2026-11-12",
    "start": "18:00",
    "end": "19:00",
    "constraint": "preferred"
  },
  "status": "draft",
  "confirmation_status": "pending_user_confirmation",
  "risks": [],
  "pending_items": [
    {
      "code": "ALTERNATIVE_SLOT",
      "message": "建议 19:00 替代原定 18:00，需用户确认。"
    }
  ],
  "decision_basis": "18:00 与 S-001 冲突，19:00 为同日最近空闲时段。",
  "created_at": "2026-11-10T09:00:00+08:00",
  "published": false
}
```

`draft_id` 由 `request_key` 哈希得到，相同指纹重复保存返回同一个 ID。

## 4. 工具契约

所有工具返回统一信封：

```json
{
  "ok": true,
  "code": "OK",
  "summary": "读取 M01，字段完整",
  "data": {},
  "error": null
}
```

失败时 `ok=false`，`error` 包含 `code`、`message`、`retryable`。工具的参数里都有可选 `reason`，只用于 Trace 审计，不参与业务。

### 4.1 `get_material(material_id)`

- 成功：返回素材、`missing_fields`。
- 失败：`MATERIAL_NOT_FOUND`。
- ledger：记录素材快照、是否找到、缺失字段。

### 4.2 `check_rules(material_id)`

- 前置：必须先 `get_material`。
- 成功：返回 `risk_level`（`none` / `warning` / `blocked`）、`risks`、`missing_fields`。
- 失败：`PRECONDITION_FAILED`。
- ledger：记录规则结果。

### 4.3 `query_slots(material_id)`

- 前置：必须先 `get_material` 和 `check_rules`。
- 如果缺字段或硬规则违规：`NOT_ELIGIBLE`，不返回时段。
- 成功：返回请求时段、冲突列表、同日替代时段、当前指纹已有草稿、搜索窗口。
- 如果请求时段冲突，`conflict=true`，`alternatives` 按距离排序。
- ledger：记录时段查询结果。

### 4.4 `save_draft(...)`

参数：

```json
{
  "material_id": "M01",
  "date": "2026-11-12",
  "start": "19:00",
  "confirmation_status": "pending_user_confirmation",
  "decision_basis": "18:00 冲突，选择 19:00。",
  "reason": "保存替代时段草稿"
}
```

校验顺序：

1. 前置：必须已执行 `get_material`、`check_rules`、`query_slots`。
2. 素材必须存在，且不缺字段、不命中硬规则。
3. 日期必须等于请求日期；当前不支持跨天。
4. 如果 `start != requested_start`：
   - `hard` → `HARD_CONSTRAINT_VIOLATION`，拒绝保存。
   - 该时段必须在 `query_slots` 返回的 `alternatives` 中，否则 `SLOT_NOT_VERIFIED`。
   - `confirmation_status` 必须是 `pending_user_confirmation`，否则 `CONFIRMATION_REQUIRED`。
5. 如果 `start == requested_start`：
   - 查询结果不能是冲突，否则 `SLOT_CONFLICT`。
   - 如果有软警告，要求 `pending_user_confirmation`；否则归一化为 `not_required`。
6. 保存前重新检查一次冲突（防止查询后时段被占用），排除同一请求指纹的草稿。
7. 计算判重指纹；如果已存在，返回已有草稿，`save_status=existing`。
8. 写入草稿；写入失败返回 `SAVE_FAILED`。

### 4.5 `finish(summary)`

- 检查请求中的每个素材是否已有终态。
- 未完成：返回 `INCOMPLETE_COVERAGE`，附 `missing` 列表；循环继续。
- 全部完成：返回 `finished=true`，循环结束。

`finish` 是工具而不是自由文本，因此“模型想提前结束”会被结构化拒绝，并产生一条可审计的调整路径。

## 5. Agent 循环

```text
state = 初始化 request + ledger
while steps < max_steps and tool_calls < max_tool_calls:
    action = engine.next_action(state)
    if action 是文本:
        record; nudge; 连续两次文本则停止
    else:
        record model_decision
        for call in action.calls:
            if tool_calls >= max_tool_calls: stop
            result = tools.execute(call)
            ledger.record(...)
            record tool_result
            if call.name == "finish" and result.ok:
                finished = True; break
        engine.record_tool_results(...)
        if finished: break
if not finished:
    stop_reason = step_limit / tool_limit / model_error / no_tool_call
output = report.build(request, ledger, run_meta)
```

计数口径：

- `used_steps`：模型决策轮数，包括失败/被拒绝的 finish。
- `used_tool_calls`：实际执行的工具调用次数，包括 `finish`。
- 达到任一上限立即停止；未完成素材由报告标 `incomplete`。

## 6. 动态决策路径

### 6.1 正常路径

```text
get_material(M00) -> 字段完整
check_rules(M00) -> risk_level=none
query_slots(M00) -> 10:00 空闲
save_draft(M00, 10:00, not_required) -> saved
finish -> 全部完成
```

### 6.2 缺资料路径

```text
get_material(M02) -> missing_fields=[duration_minutes, metadata.copyright_owner]
check_rules(M02) -> risk_level=blocked, missing_fields=[...]
模型不调用 query_slots/save_draft，直接 finish
报告：needs_input，无草稿
```

### 6.3 硬规则路径

```text
get_material(M03) -> 字段完整
check_rules(M03) -> blocked, FORBIDDEN_TERM
模型跳过时段查询，直接 finish
报告：blocked_rule，无草稿
```

### 6.4 软冲突路径

```text
get_material(M01)
check_rules(M01) -> none
query_slots(M01) -> 18:00 冲突；alternatives=[19:00, ...]
save_draft(M01, 19:00, pending_user_confirmation) -> saved
finish
报告：pending_confirmation；suggested_slot=19:00；published=false
```

### 6.5 硬冲突路径

```text
get_material(M04)
check_rules(M04) -> none
query_slots(M04) -> 18:00 冲突；alternatives=[19:00, ...]；constraint=hard
模型不能 save_draft；直接 finish
报告：blocked_conflict；suggested_slot=19:00；无草稿；请求时段仍为 18:00
```

如果模型仍尝试 `save_draft(M04, 19:00)`，工具返回 `HARD_CONSTRAINT_VIOLATION`，模型再调整到 finish。这条路径本身也是可提交的 Trace。

### 6.6 重复提交路径

```text
第一次：... save_draft -> saved, draft_id=DRF-X
第二次：get_material -> check_rules -> query_slots（排除同指纹草稿，19:00 仍可用）
        save_draft -> existing, draft_id=DRF-X, deduplicated=true
报告：drafted/pending，save_status=existing，store 只有一份草稿
```

### 6.7 保存失败路径

```text
... query_slots -> 21:00 可用
save_draft -> SAVE_FAILED
模型不得声称成功；finish
报告：save_failed，draft.id=null，store 无草稿
```

## 7. 护栏与不变量

1. **没有发布工具。** 工具注册表只有读取、检查、查询、保存草稿、finish；代码中不存在 publish 函数。
2. **硬约束不可改。** `save_draft` 在工具层拒绝硬约束替代时段，不依赖模型自觉。
3. **缺资料/硬规则不可排。** `query_slots` 和 `save_draft` 都有前置条件，模型跳过检查会被拒绝。
4. **保存结果以 ledger 为准。** 报告由 ledger 生成；模型不提交“我保存成功了”的自由文本。
5. **幂等由 store 保证。** 判重索引在文件锁内检查；同一指纹只有一个草稿。
6. **上限由 runner 保证。** 到上限停止，报告标 `incomplete`，不伪造终态。
7. **确认不等于发布。** `confirm` 只改 `confirmation_status`，`status` 仍是 `draft`，`published=false`。
8. **工具结果结构化。** 所有错误有稳定错误码，模型可以据此调整，测试可以断言。

## 8. 幂等与自冲突

重复运行最容易出的 bug 是：第一次生成的 19:00 草稿在第二次查询时占用了 19:00，Agent 误判冲突，生成 20:00 的第二份草稿。

处理方式：

- 判重指纹不包含 `request_id`。
- `query_slots` 计算当前请求的指纹，查询忙碌时段时排除该指纹的草稿。
- `save_draft` 在锁内再次检查指纹；已存在直接返回。
- 草稿 ID 由指纹确定性生成，重复运行返回同一 ID。

这样，重复运行仍会看到 19:00 可用，调用 `save_draft` 后得到 `existing`，store 中只有一份。

## 9. 保存失败与故障注入

- `Store.create_draft` 用临时文件 + `os.replace` 原子写入；写入前获取线程锁和文件锁。
- `--fault save_draft` 在工具层注入 `InjectedSaveError`，模拟持久化失败；这是验收入口，不改变业务逻辑。
- 失败时不写任何草稿；ledger 记录 `SAVE_FAILED`；报告 `save_failed`。
- 不自动无限重试；模型可以重试一次，但故障持续时最终仍是失败。默认离线策略不重试，保证 Trace 清晰。

## 10. 确认流程

`confirm` 子命令：

1. 在 `--state-dir` 中查找草稿。
2. 如果不存在：`DRAFT_NOT_FOUND`。
3. 重新检查草稿时段是否被新的排期/草稿占用（排除自己）。
4. 如果冲突：`CONFIRM_CONFLICT`，草稿保持待确认。
5. 否则把 `confirmation_status` 改为 `user_confirmed`，记录 `confirmed_at`。
6. 输出 `status=draft`、`published=false`。

`confirm` 不接受“发布”参数，也没有发布路径。

## 11. 报告生成

`report.build` 完全基于 ledger 和 store：

- `review_status`：从规则结果和缺失字段推导。
- `schedule_status`：从草稿/冲突/保存失败推导。
- `suggested_slot`：从时段查询的备选或已保存草稿推导。
- `confirmation_status`：从草稿或阻断原因推导。
- `decision_basis`：用工具证据生成固定句式，不复述模型思维过程。
- `published=false`：硬编码，并有测试断言。

模型的作用是选择“查什么、存哪个时段、什么时候结束”，不是编造结果。这样既满足 ReAct 决策要求，又避免模型幻觉污染交付结果。

## 12. Trace

每次运行写一份 JSON Trace：

```json
{
  "schema_version": "1.0",
  "run_id": "RUN-...",
  "engine": "live-opencode-local",
  "model": "deepseek-v4.1-flash",
  "request": {},
  "limits": {},
  "steps": [
    {
      "index": 1,
      "type": "model_decision",
      "model_step": 1,
      "assistant_text": "...",
      "tool_calls": [
        {
          "id": "call_1",
          "name": "get_material",
          "arguments": {"material_id": "M01"},
          "reason": "先读取素材元数据"
        }
      ]
    },
    {
      "index": 2,
      "type": "tool_result",
      "model_step": 1,
      "tool_call_id": "call_1",
      "tool": "get_material",
      "arguments": {"material_id": "M01"},
      "ok": true,
      "code": "OK",
      "summary": "读取 M01，字段完整",
      "data": {},
      "elapsed_ms": 1,
      "next_action": "check_rules"
    }
  ],
  "outcome": {}
}
```

`next_action` 在运行结束后回填为下一条模型决策的第一个工具名；`finish` 成功时为 `end`。Trace 不记录隐藏思维过程，只记录动作、参数、结果摘要和一句话依据。

## 13. 模型接入

### 13.1 `offline-mock`

确定性的参考策略，按 ledger 状态推进：读素材 → 查规则 → 查时段 → 保存/阻断 → finish。它用于自动化测试和无可复现模型时的流程验证。输出固定标注：

> 离线 Mock 仅验证流程与护栏，不代表真实模型决策效果。

离线策略也走同一套工具和护栏，因此能证明“流程正确”，不能证明“模型决策质量”。

### 13.2 `live-openai`

标准 OpenAI-compatible Chat Completions + function calling。环境变量：

```text
OPENAI_API_KEY
OPENAI_BASE_URL
OPENAI_MODEL
```

工具 schema 直接传给模型；模型返回 tool calls；runner 执行后把 tool results 回填。适用于 OpenAI、DeepSeek、OpenRouter 等兼容接口。评审可以用自己的 key 复现。

### 13.3 `live-opencode-local`

本机 OpenCode 服务的实验性 `POST /api/experimental/generate/text` 接口，模型通过文本 JSON 协议返回动作：

```json
{"tool":"get_material","arguments":{"material_id":"M01"},"reason":"先读素材"}
```

runner 解析 JSON，执行工具，把观察结果追加到下一轮 prompt。该模式用于在当前机器上产出真实模型 Trace；接口是实验性的，不保证评审环境可用。仓库不包含任何凭证，CLI 路径通过环境变量配置。

### 13.4 为什么保留三种引擎

- 离线：可复现、可测试、无网络依赖。
- OpenAI-compatible：标准、可移植、评审可自备 key。
- 本机 OpenCode：在直连 key 过期的环境下仍能展示真实模型决策。

三者共用工具、ledger、报告和 Trace 格式，避免“mock 一套、真实一套”的分叉。

## 14. 技术取舍

| 选择 | 取舍 |
| --- | --- |
| 原生 Python + argparse，不用 LangGraph/ADK | 核心链路小，手写循环更容易解释每一步控制；框架不额外加分 |
| JSON 文件，不用数据库 | 数据量小，评审可直接查看；用文件锁和原子写补足一致性 |
| 确定性报告生成，不让模型写最终 JSON | 防止模型虚报保存成功；代价是报告措辞固定 |
| 工具层强制前置条件 | 模型跳步会被拒绝并调整；代价是 Trace 多几条错误路径 |
| 同日替代，不跨天 | 满足题目示例，规则简单；跨天会引入更多确认边界 |
| 硬冲突不落草稿 | 最保守地保护硬约束；代价是用户需要改请求后重跑 |
| 一指纹一草稿，不做 supersede | 满足防重复；内容更新后的旧草稿需要人工处理 |

## 15. 失败处理

| 失败 | 处理 |
| --- | --- |
| 请求 JSON 非法 | CLI 退出码 2，打印字段错误，不启动 Agent |
| 素材不存在 | `needs_input`，无草稿 |
| 缺字段 | `needs_input`，列出字段 |
| 硬规则 | `blocked_rule`，无草稿 |
| 硬冲突 | `blocked_conflict`，给备选，无草稿 |
| 软冲突无替代 | `blocked_conflict`，无草稿 |
| 保存失败 | `save_failed`，无草稿 |
| 模型 API 错误 | 停止，`status=failed`，未完成项标 `incomplete` |
| 模型提前 finish | 工具返回 `INCOMPLETE_COVERAGE`，继续循环 |
| 达到上限 | `status=incomplete`，未完成项标 `incomplete` |
| 草稿文件损坏 | 工具/CLI 返回 `STORE_CORRUPT`，不覆盖原文件 |

## 16. 与验收点的对应

| 验收点 | 实现位置 |
| --- | --- |
| 正常排期 | 工具链 + `examples/01-normal` |
| 素材信息缺失 | `rules.check` + `examples/02-missing-info` |
| 规则风险 | `rules.check` + `examples/03-rule-risk` |
| 时段冲突 | `slots.query` + 软/硬两个 examples |
| 重复提交 | `store.create_draft` 判重 + `examples/06-duplicate` |
| 保存失败 | `--fault save_draft` + `examples/07-save-failure` |
| 动态决策 | runner + 至少两条 Trace |
| 执行上限 | runner limits + 单元测试 |
| 不越权发布 | 无 publish 工具 + `published=false` 断言 |
| 确认边界 | `confirm` 子命令 + 测试 |
