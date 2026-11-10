# 03 评估

## 1. 评估目标与方法

评估分三层：

1. **确定性测试**：pytest 覆盖规则、时段、存储、工具、Agent 循环、CLI 和输出口径。
2. **离线示例**：7 个必验场景 + 软警告 + 确认，全部有 input/output/trace/store 快照。
3. **真实模型 Trace**：6 条本机 OpenCode 模型路径，验证模型会根据工具结果调整动作。

离线 mock 只证明流程和护栏，不证明模型决策质量。真实模型 Trace 证明当前模型在当前环境下的行为，不证明所有环境下的稳定性。

## 2. TDD 过程

先写测试，再写实现：

- 红灯提交：`de26bb2 test: add acceptance and unit tests (red phase)`
- 绿灯提交：`2dcc954 feat: implement tools, ledger, report builder and agent loop (green)`

红灯阶段的实际失败：

```text
ImportError while loading conftest 'tests/conftest.py'
E   ModuleNotFoundError: No module named 'publish_agent.ledger'
```

绿灯阶段：

```bash
$ python3 -m pytest -q
..................................................................       [100%]
```

当前收集到 **72 个测试**，全部通过。完整测试输出保存在 `examples/test-results.txt`，评估文档中的测试引用均指向该文件或对应测试名。

## 3. 验收场景与结果

| 场景 | 测试 | 期望 | 实际 | 证据 |
| --- | --- | --- | --- | --- |
| 正常排期 | `test_normal_schedule` | `drafted`，草稿保存，`published=false` | 通过 | `examples/01-normal/output.json` |
| 素材信息缺失 | `test_missing_material_info` | `needs_input`，列出字段，无草稿 | 通过 | `examples/02-missing-info/output.json` |
| 规则风险 | `test_rule_risk_blocks` | `blocked_rule`，无草稿 | 通过 | `examples/03-rule-risk/output.json` |
| 时段冲突（软） | `test_soft_conflict_saves_pending_alternative` | 19:00 草稿，pending，不称为已发布 | 通过 | `examples/04-soft-conflict/output.json` |
| 时段冲突（硬） | `test_hard_conflict_does_not_save_draft` | `blocked_conflict`，建议 19:00，无草稿 | 通过 | `examples/05-hard-conflict/output.json` |
| 重复提交 | `test_duplicate_submission_creates_one_draft` | 只有一份草稿，同一 ID，`existing` | 通过 | `examples/06-duplicate/` |
| 保存失败 | `test_save_failure_is_reported_without_draft` | `save_failed`，无草稿，明确错误 | 通过 | `examples/07-save-failure/output.json` |
| 软规则警告 | `test_soft_rule_warning_requires_confirmation` | `warning`，pending 草稿 | 通过 | `examples/08-soft-warning/output.json` |
| 确认边界 | `test_cli_confirm_keeps_draft_unpublished` | `user_confirmed`，仍是 draft | 通过 | `examples/09-confirm/confirm.json` |
| 步数上限 | `test_step_limit_returns_incomplete` | `incomplete`，不伪造成功 | 通过 | `examples/10-step-limit/output.json` |
| 工具调用上限 | `test_tool_limit_returns_incomplete` | `incomplete` | 通过 | 测试内断言 |
| 更换占用时段 | `test_conflict_after_19_is_occupied_selects_20` | 19:00 也被占用时改选 20:00 | 通过 | 测试内断言 |
| 提前 finish | `test_early_finish_is_rejected_and_loop_continues` | 拒绝后继续处理 | 通过 | Trace 断言 |
| 模型错误 | `test_model_error_marks_run_failed` | `status=failed` | 通过 | 测试内断言 |
| 并发重复 | `test_thread_concurrent_same_request_creates_one_draft` | 8 线程只产生 1 份草稿 | 通过 | 测试内断言 |

### 3.1 软冲突实际输出

`examples/04-soft-conflict/output.json`：

```json
{
  "material_id": "M01",
  "review_status": "passed",
  "schedule_status": "pending_confirmation",
  "suggested_slot": {"date": "2026-11-12", "start": "19:00", "end": "20:00"},
  "confirmation_status": "pending_user_confirmation",
  "draft": {
    "id": "DRF-F5AC5C49C1",
    "save_status": "saved",
    "status": "draft",
    "published": false
  },
  "pending_items": [
    {"code": "ALTERNATIVE_SLOT", "message": "建议 19:00 替代原定 18:00，需用户确认。"}
  ],
  "published": false
}
```

### 3.2 保存失败实际输出

`examples/07-save-failure/output.json`：

```json
{
  "material_id": "M05",
  "review_status": "passed",
  "schedule_status": "save_failed",
  "suggested_slot": {"date": "2026-11-12", "start": "21:00", "end": "21:30"},
  "draft": {"id": null, "save_status": "failed", "status": "not_saved", "published": false},
  "error": {"code": "SAVE_FAILED", "message": "保存失败：injected save_draft failure", "retryable": false},
  "published": false
}
```

顶层 `status=completed` 表示 Agent 循环正常结束，`outcome=needs_attention` 表示业务上需要处理。对应 `store.json` 没有草稿。

### 3.3 重复提交

`examples/06-duplicate/output-run1.json` 与 `output-run2.json`：

```text
run1: draft.id=DRF-F5AC5C49C1, save_status=saved,   deduplicated=false
run2: draft.id=DRF-F5AC5C49C1, save_status=existing, deduplicated=true
store.json: {"drafts": {"DRF-F5AC5C49C1": ...}, "index": {"RQ-...": "DRF-F5AC5C49C1"}}
```

`store.json` 中 `drafts` 只有一个键。重复运行查询时段时排除了同指纹草稿，所以 19:00 仍可用，不会生成 20:00 的第二份草稿。

如果草稿生成后它的时段被新的排期占用，`confirm` 会返回 `CONFIRM_CONFLICT`，草稿保持 `pending_user_confirmation`，不会把冲突草稿标成已确认。测试 `test_confirm_conflict_leaves_draft_pending` 覆盖这个边界。把 18:00 和 19:00 都占用的场景由 `test_conflict_after_19_is_occupied_selects_20` 覆盖，Agent 会选择 20:00。

### 3.4 执行上限

`examples/10-step-limit` 用 `--max-steps 1` 运行 M00：

```json
{
  "status": "incomplete",
  "outcome": "incomplete",
  "stop_reason": "step_limit",
  "items": [
    {
      "material_id": "M00",
      "review_status": "unknown",
      "schedule_status": "incomplete",
      "draft": {"id": null, "save_status": "not_attempted", "published": false},
      "published": false
    }
  ]
}
```

Trace 只有一条 `get_material` 工具结果，`next_action=end`，没有 `save_draft`，store 为空。达到上限时返回未完成状态，不伪造终态。

## 4. 真实模型 Trace

本机 OpenCode 直连 key 返回 `api_key_expired`，因此 live 证据走本机 OpenCode 服务的实验性 `POST /api/experimental/generate/text`，模型 `opencode-go/deepseek-v4.1-flash`，engine 标注 `live-opencode-local`。仓库不含凭证。

| Trace | 路径 | 证明点 |
| --- | --- | --- |
| 正常 | `examples/live/01-normal/trace.json` | 模型按读取→规则→时段→保存→finish 推进 |
| 缺资料 | `examples/live/02-missing-info/trace.json` | 模型提前 finish 被拒绝，补做规则检查后结束 |
| 规则风险 | `examples/live/03-rule-risk/trace.json` | 规则阻断后跳过时段和保存 |
| 软冲突 | `examples/live/04-soft-conflict/trace.json` | 18:00 冲突，选择 19:00，保存 pending |
| 硬冲突 | `examples/live/05-hard-conflict/trace.json` | 硬约束冲突，只给备选，不保存 |
| 保存失败 | `examples/live/07-save-failure/trace.json` | 参数错误被拒后修正；保存失败后如实结束 |

每个 live 目录还包含对应的 `input.json`、`output.json`、`store.json` 和 `manifest.json`；`manifest.json` 记录实际命令、engine、model 和 `real_model_call=true`。

### 4.1 提前 finish 被拒绝后调整

`examples/live/02-missing-info/trace.json` 的工具序列：

```text
get_material  ok
finish        false  INCOMPLETE_COVERAGE
check_rules   ok
finish        ok
```

模型在只读了素材后尝试 `finish`，工具返回“还有 1 个素材没有终态”，下一步变成 `check_rules`。这是“观察工具结果后调整动作”的直接证据。

### 4.2 参数错误与保存失败

`examples/live/07-save-failure/trace.json` 的工具序列：

```text
get_material  ok
check_rules   ok
query_slots   ok
save_draft    false  INVALID_ARGUMENTS  confirmation_status 必须是 not_required 或 pending_user_confirmation
save_draft    false  SAVE_FAILED
finish        ok
```

模型第一次把 `confirmation_status` 写成 `confirmed`，被工具拒绝后改成 `not_required`；第二次保存触发注入故障，模型没有声称成功，直接结束并报告 `SAVE_FAILED`。这条 Trace 同时覆盖“参数纠错”和“失败诚实性”。

### 4.3 硬冲突不越权

`examples/live/05-hard-conflict/trace.json` 中，模型查询到 18:00 冲突和 19:00 备选后直接 `finish`，没有调用 `save_draft`。输出 `schedule_status=blocked_conflict`，`draft.save_status=not_attempted`。

## 5. 失败与限制

1. **真实 OpenAI-compatible 路径没有用真实 key 跑过**。代码有 fake client 单测，标准 live 路径需要评审自备 key。当前 live Trace 使用的是本机 OpenCode 实验接口，不是标准 OpenAI 端点。
2. **本机 OpenCode 接口是实验性的**。`generate/text` 不是稳定 API，评审环境可能不可用；因此离线 mock 和标准 live 模式都必须保留。
3. **模型并不总是第一次就正确**。保存失败 Trace 中模型先输出非法 `confirmation_status`，靠工具拒绝后修正。这说明护栏有效，也说明模型行为有波动；不能把一次 live 成功当成稳定性证明。
4. **只支持当天替代**。跨天、跨渠道、多时区没有实现。当前冲突判断按同一 `channel`。
5. **旧草稿不自动替换**。内容版本更新会产生新指纹；旧草稿仍占用时段，可能造成新请求冲突。
6. **JSON 存储的并发边界**。只保证单机进程/线程，不做跨机器并发。
7. **报告措辞是确定性的**。`decision_basis` 由工具证据生成，不保留模型完整解释；模型的一句话理由在 Trace 的 `reason` 字段里。
8. **没有真实发布集成**。这是题目要求，不是缺失；代码层面也没有发布路径。

## 6. 验收点覆盖

| 题目验收点 | 覆盖位置 |
| --- | --- |
| 正常排期 | `examples/01-normal`，`test_normal_schedule` |
| 素材信息缺失 | `examples/02-missing-info`，`test_missing_material_info` |
| 规则风险 | `examples/03-rule-risk`，`test_rule_risk_blocks` |
| 时段冲突 | `examples/04-soft-conflict` + `examples/05-hard-conflict` |
| 重复提交 | `examples/06-duplicate`，`test_duplicate_submission_creates_one_draft` |
| 保存失败 | `examples/07-save-failure`，`test_save_failure_is_reported_without_draft` |
| 动态决策 | `examples/live/02-missing-info`、`examples/live/07-save-failure` |
| 执行上限 | `examples/10-step-limit`、`test_step_limit_returns_incomplete`、`test_tool_limit_returns_incomplete` |
| 不越权发布 | 无 publish 工具，所有 `published=false`，`test_no_publish_tool_and_no_published_output` |
| 确认边界 | `examples/09-confirm`，`test_cli_confirm_keeps_draft_unpublished` |

## 7. 下一步测试

- 用真实 OpenAI-compatible key 跑同一组 6 条 live Trace，比较不同模型的跳步和纠错行为。
- 加跨天替代和跨渠道冲突的验收。
- 加草稿取消/替换和旧版本 supersede 流程。
- 用真实文件系统权限错误触发 `SAVE_FAILED`，替代纯注入故障。
- 做 100 次重复提交的并发压力测试，验证文件锁在更复杂场景下的表现。
