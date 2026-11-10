# 06 Code Review

## 1. 审查范围

审查 `src/publish_agent/` 全部模块、`tests/`、`data/`、`examples/` 和文档口径。重点检查：

- 是否存在发布路径或 `published=true`。
- 硬约束是否在工具层拦截。
- 幂等是否在并发下成立。
- 保存失败是否可能被报告成成功。
- 执行上限是否真的能停止循环。
- Trace 是否记录了动作、参数、结果摘要和下一步。
- 是否有密钥、隐私数据或运行态文件被提交。
- 文档中的状态口径是否和代码一致。

## 2. 发现与修正

| # | 发现 | 严重性 | 修正 |
| --- | --- | --- | --- |
| 1 | 报告 item 漏了 `schedule_status`，验收测试直接 `KeyError` | 高 | 补字段，所有输出统一含 `review_status` 和 `schedule_status` |
| 2 | 保存失败时顶层 `status=completed`、CLI 退出码 0，容易被读成成功 | 高 | 增加 `outcome`；保存失败为 `needs_attention`，CLI 返回 1 |
| 3 | OpenAI fake client 的 `model_dump` 不接受 `exclude_none`，测试暴露接口耦合 | 中 | 改为无参 `model_dump()`，兼容 fake 和真实 SDK |
| 4 | CLI 测试 helper 的 `**extra` 写法导致 `--fault` 无法传入 | 中 | 改成 `*extra`，保存失败 CLI 用例可执行 |
| 5 | `parse_local_action` 把 `reason` 同时留在 arguments 和顶层字段，Trace 重复 | 低 | 统一弹出到 `reason`，arguments 只保留业务参数 |
| 6 | `build_local_prompt` 忽略 `OpenCodeLocalEngine` 传入的 schemas | 低 | 增加 `schemas` 参数，prompt 与引擎配置一致 |
| 7 | `confirm` 冲突时只打印 store error，缺稳定错误码 | 低 | 输出结构化 `CONFIRM_CONFLICT`，`published=false` |
| 8 | 文档曾把 `status` 和业务结果混在一起 | 中 | 拆分 `status`（是否跑完）和 `outcome`（业务是否需要处理） |

以上均由测试、Trace 或实际运行暴露，修正后重新跑全套测试。

## 3. 关键不变量核对

### 3.1 不发布

- `TOOL_SCHEMAS` 中没有 `publish`。
- `ToolRegistry` 没有 publish handler。
- `draft.status` 只写 `draft`；`confirm` 也不改。
- 所有报告层 `published=false` 硬编码。
- 测试 `test_no_publish_tool_and_no_published_output` 断言工具名和输出。

### 3.2 硬约束

- `save_draft` 在 `start != requested_start` 且 `constraint == "hard"` 时返回 `HARD_CONSTRAINT_VIOLATION`。
- 硬冲突的离线路径不调用 save；live Trace 也没有调用 save。
- 报告保留 `requested_slot.start=18:00`，`suggested_slot.start=19:00`，`draft.save_status=not_attempted`。

### 3.3 幂等

- 判重指纹不含 `request_id`。
- `query_slots` 的 `exclude_request_key` 排除同指纹草稿。
- `create_draft` 在文件锁内二次检查。
- 草稿 ID 由指纹确定性生成。
- 并发测试 8 线程只产生 1 份草稿。

### 3.4 保存失败

- `create_draft` 原子写；写失败回滚内存，不留下半份草稿。
- 注入故障在写之前抛出。
- `SAVE_FAILED` 记录到 ledger，报告 `draft.id=null`。
- CLI 退出码 1，`outcome=needs_attention`。

### 3.5 上限

- 每轮循环前检查 `max_steps`。
- 每次工具调用前检查 `max_tool_calls`。
- 到上限 `stop_reason` 明确，报告未完成素材 `schedule_status=incomplete`。
- 测试覆盖步数上限和调用上限。

### 3.6 密钥与隐私

- `.env` 在 `.gitignore`。
- `.env.example` 只有变量名。
- live Trace 不包含 CLI 路径、token 或请求头。
- 示例数据是合成素材，不含真实用户信息。

## 4. 仍存在的风险

1. **JSON 文件锁只覆盖单机**。多机并发不保证；生产应换数据库或分布式锁。
2. **没有真实发布集成**。这是题目边界，不是遗漏。
3. **模板化报告可能掩盖模型差异**。`decision_basis` 是确定性生成；模型差异主要在 Trace 的 action/reason 里。
4. **`confirm` 的冲突检查没有自动改期**。它只拒绝并保留 pending，用户需要重新运行。
5. **批量 5 条默认上限偏紧**。正常 5 条需要约 21 次调用；默认 20 会触发 incomplete。用户可用 `--max-tool-calls` 调高。
6. **真实 OpenAI-compatible 路径未用真实 key 验证**。只有 fake client 单测。

## 5. 复审命令

```bash
python3 -m compileall -q src tests
python3 -m pytest -q
python3 scripts/build_examples.py
python3 run.py run --request examples/07-save-failure/input.json \
  --data-dir data --state-dir .runs/review-fail --engine offline \
  --fault save_draft --output /tmp/review-fail.json --trace /tmp/review-fail-trace.json
jq '{status,outcome,item:.items[0]}' /tmp/review-fail.json
```

预期：`outcome=needs_attention`，`schedule_status=save_failed`，`draft.id=null`，`published=false`。
