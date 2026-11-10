# 04 AI 使用记录

## 0. 参考的公开示例

参考了 `https://github.com/lwz1357/agent-campus-submission-example` 的目录组织：README、`docs/01`–`05`、`src/`、`tests/`、`data/`、`examples/` 和 `examples/test-results.txt`。该示例是另一个题目（短视频脚本生成）的离线确定性 Mock，README 明确说明它不实现真实模型驱动的 ReAct，不作为达标依据；因此只参考材料组织，没有复制其代码、数据或业务逻辑。本题的排期规则、工具、Trace 和 live 证据均为重新实现。

## 1. 使用范围

本项目使用 OpenCode AI 编程助手（模型 `deepseek-v4.1-flash`，provider `opencode-go`）完成以下工作：

- 读题后的需求拆解、Grill 问题设计、spec 草稿。
- Python 代码、pytest 测试、示例数据和 Trace 生成脚本。
- 运行测试、修复失败、生成离线示例和真实模型 Trace。
- 文档初稿和代码审查清单。

候选人负责：

- 确认题目选择和 7 项业务决策（硬/软约束、判重口径、确认流程、批量上限、规则风险、live 证据方案、仓库交付）。
- 要求按 grill → spec → TDD → code review 的流程推进。
- 最终复核代码、文档、实际运行结果和 GitHub 提交。

## 2. 关键决策由谁定

| 决策 | 来源 |
| --- | --- |
| 硬冲突不落草稿 | 候选人从 Grill 推荐项中确认 |
| 判重指纹 = 素材 + 内容版本 + 要求时段 + 约束 | 候选人确认 |
| 两阶段 `run` / `confirm` | 候选人确认 |
| 默认 12 步 / 20 次工具调用、最多 5 个素材 | 候选人确认 |
| 硬规则阻断、软警告 pending | 候选人确认 |
| 用本机 OpenCode 凭证产出 live Trace | 候选人确认；实际直连 key 过期后改用本机 OpenCode 服务 |
| 先本地 git，后 push | 候选人确认 |

## 3. AI 生成后被验证/修改的内容

### 3.1 测试驱动的修正

- 报告最初漏了 `schedule_status` 字段，离线验收测试立即报 `KeyError`；补字段后通过。
- OpenAI engine 测试里的 fake `model_dump` 不接受 `exclude_none` 参数；改为无参调用后通过。
- CLI 测试 helper 的参数展开写错，导致 `--fault` 无法传入；修正 helper 后保存失败用例通过。
- 保存失败时顶层 `status` 仍是 `completed`，CLI 退出码为 0；增加 `outcome=needs_attention` 并调整退出码后，失败场景返回 1。
- `parse_local_action` 一开始把 `reason` 同时留在 arguments 和顶层字段，Trace 有重复；后来统一弹出到 `reason`。

这些修正均由测试或实际 Trace 暴露，不是事后编造。

### 3.2 真实模型 Trace 的验证

- 本机 OpenCode 直连 API key 返回 `api_key_expired`，无法直接调用 `opencode.ai/zen/go/v1`。
- 改用本机 OpenCode 服务的 `POST /api/experimental/generate/text`，用 `opencode-go/deepseek-v4.1-flash` 生成 6 条真实 Trace。
- 真实模型在 `examples/live/07-save-failure` 中先输出非法 `confirmation_status=confirmed`，被工具拒绝后修正为 `not_required`；随后 `SAVE_FAILED`，模型没有虚报成功。
- 真实模型在 `examples/live/02-missing-info` 中提前 `finish`，被 `INCOMPLETE_COVERAGE` 拒绝后补做 `check_rules`，再结束。

## 4. 没有交给 AI 判断的内容

- 不把模型输出当作保存成功的证据；保存状态以 store/ledger 为准。
- 不让模型直接生成最终 JSON 状态；最终报告由 `report.build` 从工具观察生成。
- 不用 mock 结果证明真实模型决策质量；mock 只证明流程和护栏。
- 不把本机凭证、`.env`、`.runs/` 写入仓库。
- 不声称已经发布；代码和输出都没有发布路径。

## 5. 可复现验证

```bash
python3 -m pytest -q
python3 scripts/build_examples.py
python3 run.py run --request examples/07-save-failure/input.json \
  --data-dir data --state-dir .runs/check --engine offline \
  --fault save_draft --output /tmp/check-output.json --trace /tmp/check-trace.json
jq '.items[0].draft, .outcome' /tmp/check-output.json
```

预期：`draft.save_status=failed`、`draft.id=null`、`outcome=needs_attention`。

## 6. 边界

- AI 生成的 live Trace 只代表当前机器、当前模型、当前实验接口的行为。
- 标准 OpenAI-compatible live 路径没有用真实 key 跑过，只有 fake client 单测。
- 候选人提交前应自己重跑测试、抽查 Trace、确认 GitHub 仓库公开和 commit SHA。
- 本文件不替代候选人对代码的理解；面试时需要能解释工具护栏、幂等和硬约束处理。
