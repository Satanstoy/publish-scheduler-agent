# 素材审核与发布排期 Agent

校招 AI Agent 开发岗项目题 1。输入待审素材和发布时间要求，Agent 读取素材、检查规则、查询已有排期、保存草稿，输出每项素材的审核状态、风险、建议时段、待确认项和草稿保存状态。

核心边界：**系统只保存草稿，不接真实发布。** 工具注册表里没有发布工具，所有输出固定 `published=false`。

技术栈：Python 3.12 + argparse CLI + JSON 文件存储 + pytest。模型接入使用标准 OpenAI-compatible SDK（仅 live 模式）和本机 OpenCode 实验接口（live 证据）。**未使用 LangGraph、Google ADK、AgentScope 等框架**；决策循环、工具护栏、幂等和报告生成都是原生实现。

## 快速开始

要求 Python 3.12+。离线模式没有第三方运行时依赖；`openai` 只在 live 模式导入，`pytest` 只在测试时需要。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m pytest -q
```

运行正常排期用例：

```bash
python3 run.py run \
  --request examples/01-normal/input.json \
  --data-dir data \
  --state-dir .runs/quick-normal \
  --engine offline \
  --now 2026-11-10T09:00:00+08:00 \
  --output /tmp/normal-output.json \
  --trace /tmp/normal-trace.json

cat /tmp/normal-output.json
```

输出里应看到 `outcome=scheduled`、`schedule_status=drafted`、`draft.save_status=saved`、`published=false`。

运行全部离线示例：

```bash
python3 scripts/build_examples.py
```

## 评审导航

| 检查项 | 入口 |
| --- | --- |
| 用户、目标、范围、成功标准 | `docs/01-requirements.md` |
| 调用流程、动态决策、技术取舍 | `docs/02-solution.md` |
| 测试预期、实际结果、失败和限制 | `docs/03-evaluation.md`、`examples/test-results.txt` |
| AI 使用范围、修改和验证 | `docs/04-ai-usage.md` |
| 最难问题、学习收获、下一步 | `docs/05-retrospective.md` |
| 代码审查发现和修正 | `docs/06-code-review.md` |
| 交付规范逐项对照 | `SUBMISSION.md` |

目录组织参考了 `lwz1357/agent-campus-submission-example` 的交付结构；该示例是另一个题目（短视频脚本生成）的离线 mock，不包含真实模型 ReAct，因此只参考目录与材料组织，没有复制其代码或业务内容。

## 必验场景

| 场景 | 命令/入口 | 预期 |
| --- | --- | --- |
| 正常排期 | `examples/01-normal` | 保存 10:00 草稿，`drafted` |
| 素材信息缺失 | `examples/02-missing-info` | `needs_input`，列出缺失字段，无草稿 |
| 规则风险 | `examples/03-rule-risk` | 命中禁词“赌博”，`blocked_rule`，无草稿 |
| 时段冲突（软） | `examples/04-soft-conflict` | 建议 19:00，保存 pending 草稿，`published=false` |
| 时段冲突（硬） | `examples/05-hard-conflict` | `blocked_conflict`，建议 19:00，无草稿 |
| 重复提交 | `examples/06-duplicate` | 两次运行只有一份草稿，第二次 `existing` |
| 保存失败 | `examples/07-save-failure` | `save_failed`，无草稿，明确错误 |
| 软规则警告 | `examples/08-soft-warning` | 100 分钟长视频，pending 草稿 |
| 确认边界 | `examples/09-confirm` | `user_confirmed`，仍是 draft，`published=false` |
| 执行上限 | `examples/10-step-limit` | `incomplete`，不伪造成功 |

`examples/README.md` 有逐条说明、实际输出和 Trace 对照。

重复提交的复现命令：

```bash
for i in 1 2; do
  python3 run.py run \
    --request examples/06-duplicate/input.json \
    --data-dir data \
    --state-dir .runs/dup \
    --engine offline \
    --now 2026-11-10T09:00:00+08:00 \
    --output "/tmp/dup-$i.json" \
    --trace "/tmp/dup-trace-$i.json"
done
jq '.items[0].draft' /tmp/dup-1.json /tmp/dup-2.json
jq '.drafts | length' .runs/dup/drafts.json
```

保存失败用 `--fault save_draft` 注入：

```bash
python3 run.py run \
  --request examples/07-save-failure/input.json \
  --data-dir data \
  --state-dir .runs/fail \
  --engine offline \
  --now 2026-11-10T09:00:00+08:00 \
  --fault save_draft \
  --output /tmp/fail-output.json \
  --trace /tmp/fail-trace.json
```

软冲突生成草稿后确认：

```bash
python3 run.py run \
  --request examples/04-soft-conflict/input.json \
  --data-dir data \
  --state-dir .runs/soft \
  --engine offline \
  --now 2026-11-10T09:00:00+08:00 \
  --output /tmp/soft-output.json \
  --trace /tmp/soft-trace.json

python3 run.py confirm \
  --state-dir .runs/soft \
  --data-dir data \
  --draft-id DRF-F5AC5C49C1 \
  --now 2026-11-10T09:00:00+08:00
```

`confirm` 只把 `confirmation_status` 改成 `user_confirmed`，`status` 仍是 `draft`，`published=false`。

## 模型接入

### 离线 Mock（默认，无网络）

`--engine offline`。确定性参考策略，用于测试和复现。输出固定标注：

> 离线 Mock 仅验证流程与护栏，不代表真实模型决策效果。

### 标准 OpenAI-compatible（live）

```bash
cp .env.example .env
# 编辑 .env：OPENAI_API_KEY、OPENAI_BASE_URL、OPENAI_MODEL
python3 run.py run ... --engine live
```

使用 Chat Completions + function calling。适用于 OpenAI、DeepSeek、OpenRouter 等兼容接口。没有 key 时 CLI 会报配置错误，不会静默回退到 mock。

### 本机 OpenCode（live，实验性）

```bash
export OPENCODE_CLI_PATH="/path/to/opencode-cli"
python3 run.py run ... --engine opencode-local
```

通过本机 OpenCode 服务的 `POST /api/experimental/generate/text` 走文本 JSON 决策协议。仓库不包含任何凭证；`examples/live/` 下的真实模型 Trace 由该模式生成，模型为 `opencode-go/deepseek-v4.1-flash`。每个 live 用例目录含 `manifest.json`（实际命令/模型）、`input.json`、`trace.json`（模型决策/工具调用）、`output.json` 和 `store.json`。该接口是实验性接口，评审环境不一定可用。

三种引擎共用同一套工具、ledger、报告和 Trace 格式。离线 mock 证明流程和护栏；live 证明模型能根据工具结果调整动作。

## 输出口径

```json
{
  "status": "completed",
  "outcome": "scheduled",
  "published": false,
  "items": [
    {
      "material_id": "M00",
      "status": "drafted",
      "review_status": "passed",
      "schedule_status": "drafted",
      "suggested_slot": {"date": "2026-11-12", "start": "10:00", "end": "11:00"},
      "confirmation_status": "not_required",
      "pending_items": [],
      "draft": {"id": "DRF-...", "save_status": "saved", "published": false},
      "decision_basis": "请求时段 10:00 空闲，规则无风险，已保存草稿。",
      "published": false
    }
  ]
}
```

- 顶层 `status`：Agent 是否跑完（`completed` / `incomplete` / `failed`）。
- 单项 `status`：该素材的业务终态（`drafted` / `pending_confirmation` / `blocked_conflict` / `blocked_rule` / `needs_input` / `save_failed` / `incomplete`）。
- `outcome`：整次运行的业务结果（`scheduled` / `needs_confirmation` / `needs_attention` / `incomplete` / `failed`）。
- `review_status`：`passed` / `warning` / `blocked` / `needs_input` / `unknown`。
- `schedule_status`：`drafted` / `pending_confirmation` / `blocked_conflict` / `not_scheduled` / `save_failed` / `incomplete`。
- `draft.save_status`：`saved` / `existing` / `failed` / `not_attempted`。
- `confirmation_status`：`not_required` / `pending_user_confirmation` / `user_confirmed` / `blocked` / `not_applicable`。

判定口径：

- 硬规则违规、缺字段不落草稿。
- `hard` 约束冲突不落替代草稿，只返回备选。
- `preferred` 约束冲突落替代草稿，必须标 `pending_user_confirmation`。
- 相同请求指纹重复保存返回原草稿，`save_status=existing`。
- 保存失败返回 `SAVE_FAILED`，`draft.id=null`，`published=false`。
- 达到步数/工具调用上限返回 `incomplete`，不伪造终态。

## 目录

```text
src/publish_agent/   CLI、模型引擎、工具、规则、时段、存储、报告、Trace
tests/               单元测试、集成测试、验收测试
data/                素材、已有排期、审核规则
examples/            正常、缺资料、规则风险、软/硬冲突、重复、保存失败、确认
docs/                00 grill、01 需求、02 方案、03 评估、04 AI 使用、05 复盘、06 代码审查
scripts/             示例生成脚本
run.py               无需安装即可运行的 CLI 入口
```

## 测试

```bash
python3 -m pytest -q
# 可读报告写入 examples/test-results.txt
python3 -m pytest -v -o addopts="" > examples/test-results.txt
```

测试覆盖：

- 请求校验、字段缺失、规则硬/软风险、时段冲突与替代。
- 草稿创建、重复提交、并发同请求、确认与确认冲突。
- 工具前置条件、硬约束拒绝、保存失败注入、finish 覆盖率。
- 离线 Agent 的正常/缺失/规则/软冲突/硬冲突/重复/失败/上限路径。
- 模型提前 finish 被拒绝后继续、模型错误、纯文本停止。
- CLI 输出文件、退出码、confirm。
- OpenAI tool call 解析；本机 OpenCode JSON 协议解析。

## 设计要点

1. **没有发布工具**。代码中没有 publish 函数，输出固定 `published=false`。
2. **硬约束在工具层拦截**。模型即使想保存替代时段，`save_draft` 也会返回 `HARD_CONSTRAINT_VIOLATION`。
3. **保存结果以 ledger 为准**。最终报告由工具观察生成，模型不能自由文本声称保存成功。
4. **幂等由 store 保证**。判重指纹不含 `request_id`；同指纹只产生一份草稿，重复运行返回同一 ID。
5. **自冲突处理**。查询忙碌时段时排除同指纹草稿，避免重复运行把自己判成冲突。
6. **执行上限**。默认 12 个模型步、20 次工具调用，到上限返回未完成状态。
7. **确认不等于发布**。`confirm` 只改确认状态，草稿仍是 draft。

## 已知限制

- 只支持请求当天的替代时段，不跨天搜索。
- JSON 文件存储只保证单机进程/线程安全，不做跨机器并发。
- 内容版本更新后旧草稿不自动取消或替换，需要人工处理。
- 不接真实发布系统、账号、权限、审计日志。
- `opencode-local` 依赖本机实验性接口；标准 live 模式需要自备 OpenAI-compatible key。
- 公开的 live Trace 证明模型在当前机器上的行为，不代表模型在所有环境下的稳定性。

## 提交信息

- 题号：项目题 1「素材审核与发布排期 Agent」
- GitHub URL：待填写
- 最终 commit SHA：待填写（以 `git rev-parse HEAD` 为准）

提交前请确认 `.env`、`.runs/`、任何真实 key 和隐私数据都未提交。
