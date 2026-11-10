# Examples

每组用例包含 `input.json`、实际 `output.json`、`trace.json` 和 `store.json`。`store.json` 是运行后的 `drafts.json` 快照，用来核对“只产生一份草稿”和“保存失败没有草稿”。

## Offline cases（可复现）

| 目录 | 场景 | 预期 | 运行结果 |
| --- | --- | --- | --- |
| `01-normal` | M00，10:00 空闲，无规则风险 | `drafted` | `outcome=scheduled`，草稿 `DRF-A54E0659E7` |
| `02-missing-info` | M02，缺 `duration_minutes` 和版权方 | `needs_input` | 无时段查询、无草稿 |
| `03-rule-risk` | M03，摘要命中禁词“赌博” | `blocked_rule` | 无草稿 |
| `04-soft-conflict` | M01，18:00 已占用，preferred | `pending_confirmation` | 建议 19:00，草稿标 pending，`published=false` |
| `05-hard-conflict` | M04，18:00 已占用，hard | `blocked_conflict` | 建议 19:00，无草稿，原要求不变 |
| `06-duplicate` | 同一 M01 请求跑两次 | 第二次 `existing` | 两次同一 `DRF-F5AC5C49C1`，`store.json` 只有一份草稿 |
| `07-save-failure` | M05，21:00 可用，注入 `save_draft` 故障 | `save_failed` | 无草稿，错误码 `SAVE_FAILED` |
| `08-soft-warning` | M06，时长 100 分钟 | `pending_confirmation` | 软警告 `LONG_DURATION_REVIEW`，草稿待确认 |
| `09-confirm` | M01 待确认草稿执行 `confirm` | `user_confirmed` | 仍是 `status=draft`，`published=false` |
| `10-step-limit` | M00，限制 1 个模型步 | `incomplete` | 只读取素材后停止，未保存草稿 |
| `11-vague-confirm` | M01 待确认草稿，用户回复“可以” | `confirmed` | 只有一份 pending 时确认，仍是草稿，`published=false` |
| `12-ambiguous-confirm` | M01 + M06 两份 pending，用户回复“继续” | `needs_clarification` | 列出两份草稿，不确认任何一份，store 仍有两份 |

复现单条用例：

```bash
python3 run.py run \
  --request examples/04-soft-conflict/input.json \
  --data-dir data \
  --state-dir .runs/repro-04 \
  --engine offline \
  --now 2026-11-10T09:00:00+08:00 \
  --output /tmp/output.json \
  --trace /tmp/trace.json
```

复现 `01`–`10` 离线用例：

```bash
python3 scripts/build_examples.py
```

`11-vague-confirm`、`12-ambiguous-confirm` 是固定的确认解析示例，运行命令见各自目录的 `manifest.json`。

正常和待确认用例退出码为 0；缺资料、规则阻断、硬冲突、保存失败退出码为 1。退出码只表示是否需要人工处理，不改变输出里的逐项状态。

## Live cases（真实模型）

`examples/live/` 下的 6 组 Trace 由本机 OpenCode 的 `POST /api/experimental/generate/text` 生成，模型为 `opencode-go/deepseek-v4.1-flash`，engine 标注 `live-opencode-local`。这是实际模型输出，不是 mock；但该接口是实验性接口，评审环境不一定可用。

| 目录 | 真实模型路径 |
| --- | --- |
| `live/01-normal` | 读取 → 规则 → 时段 → 保存 → finish |
| `live/02-missing-info` | 读取后尝试提前 finish，被 `INCOMPLETE_COVERAGE` 拒绝，补做规则检查后结束 |
| `live/03-rule-risk` | 规则阻断后跳过时段查询和保存 |
| `live/04-soft-conflict` | 18:00 冲突，选择 19:00，保存 pending 草稿 |
| `live/05-hard-conflict` | 硬冲突，只给 19:00 备选，不保存 |
| `live/07-save-failure` | 先输出非法 `confirmation_status=confirmed`，被工具拒绝后改成 `not_required`；随后保存失败，如实结束 |

复现 live 用例（需要本机 OpenCode CLI）：

```bash
export OPENCODE_CLI_PATH="/path/to/opencode-cli"
python3 run.py run \
  --request examples/live/04-soft-conflict/input.json \
  --data-dir data \
  --state-dir .runs/live-repro-04 \
  --engine opencode-local \
  --now 2026-11-10T09:00:00+08:00 \
  --output /tmp/live-output.json \
  --trace /tmp/live-trace.json
```

live 目录包含 `input.json`、`output.json`、`trace.json`、`store.json` 和 `manifest.json`。`manifest.json` 记录实际使用的 engine、model、完整命令和 `real_model_call=true`；`store.json` 用来核对软冲突是否只有一份草稿、硬冲突/缺资料/保存失败是否没有草稿。草稿 ID 由请求指纹确定，因此同一个输入重跑仍会得到同一个 ID。

## 测试结果

`examples/test-results.txt` 是 `python3 -m pytest -v -o addopts=''` 的实际输出，当前 **82 passed**。`docs/03-evaluation.md` 的测试引用指向该文件和对应测试名。

## 模糊确认

`confirm` 要求显式 `--draft-id`；`confirm-text` 是对模糊回复的受限解析层：

```bash
python3 run.py confirm-text \
  --state-dir .runs/11-vague-confirm \
  --data-dir data \
  --message "可以" \
  --now 2026-11-10T09:00:00+08:00
```

规则：

- 只有恰好一份 `pending_user_confirmation` 草稿时，“可以 / 没问题 / 继续 / 确认 / OK”才会确认这一份。
- 有两份及以上待确认草稿时，即使说“可以”也只返回 `needs_clarification`，列出草稿，不确认任何一份。
- “取消 / 不考虑”只保留 pending，不确认。
- 识别不了的消息返回 `needs_clarification`，不改状态。
- 带时间或修改意图的消息（如“可以，但改成 20:00”）不自动确认，要求修改请求后重跑。
- 硬冲突没有草稿，说“可以”不会改变硬性发布时间，返回 `no_pending`。
- 确认时如果时段已被新排期占用，返回 `confirm_conflict`，草稿保持 pending。
- 任何情况下 `published=false`。

## Live 调用记录

一条 live 运行的完整记录由 5 个文件组成：

- `manifest.json`：实际 engine、model、完整命令、`real_model_call=true`。
- `input.json`：用户请求。
- `trace.json`：整个运行的模型决策与工具调用记录，含 `request`、`steps`、`outcome`、`limits`。
- `output.json`：逐项业务结果。
- `store.json`：运行后的草稿状态。

`trace.json` 的 `steps` 中，`model_decision.assistant_text` 是模型这次实际输出的原始 JSON，`tool_calls[].arguments` 是模型选择的工具参数，`tool_result.summary` 是工具返回摘要，`next_action` 是下一次动作。`examples/live/02-missing-info` 和 `examples/live/07-save-failure` 分别记录了“提前 finish 被拒后补查”和“参数错误修正后保存失败”的真实模型调整。

## 看 Trace 时重点核对

- `steps[].tool_calls[].arguments`：模型实际传的参数。
- `steps[].summary` / `data`：工具返回摘要和结构化结果。
- `steps[].next_action`：下一步动作，能直接看出模型是否根据结果调整。
- `live/02-missing-info`：`finish` 失败后下一步变成 `check_rules`，是“观察结果后调整”的直接证据。
- `live/07-save-failure`：先被 `INVALID_ARGUMENTS` 拒绝，再修正参数；最后 `SAVE_FAILED`，没有虚报成功。
- 所有输出 `published=false`；所有 live Trace 的 `outcome` 与 output 一致。
