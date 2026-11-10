# 01 需求说明

## 1. 背景与用户

内容团队每天会收到一批待发布素材。素材可能缺少必要元数据，可能命中版权/禁词/黑名单规则，也可能与已有排期冲突。操作员需要快速判断哪些素材可以排、哪些需要补资料、哪些必须阻断，并拿到一份可以人工确认的排期草稿。

本项目服务这个操作员，交付一个 CLI Agent。它不替代人做最终发布决定，只负责把素材审到“可确认的草稿”状态。

## 2. 目标

1. 读取请求中的素材 ID、日期、时间、约束类型和渠道。
2. 读取素材元数据，发现缺失字段。
3. 按规则检查版权、禁词、黑名单日期、时长和临期风险。
4. 查询已有排期与草稿，判断时段冲突，给出同一天的空闲替代时段。
5. 对可排素材保存草稿；对软冲突保存替代时段草稿并标记待确认；对硬冲突、硬规则违规、缺资料不保存草稿。
6. 防止相同请求重复保存产生多份草稿。
7. 保存失败时明确返回失败，不得虚报成功。
8. 输出每项素材的审核状态、风险原因、建议时段、待确认项、草稿保存状态。
9. 保留可审计的执行 Trace，证明模型根据工具结果调整动作。
10. 设置执行步数和工具调用次数上限，到上限返回未完成状态。

## 3. 非目标

- 不接真实发布系统，不提供发布按钮，不写“已发布”。
- 不做 Web UI、移动端、部署、账号体系、权限系统。
- 不做多 Agent 协作，不做生产级 Runtime。
- 不做跨机器并发、分布式锁、消息队列。
- 不做完整的日历系统集成；已有排期来自本地 JSON。
- 不做草稿取消、替换、历史版本回滚；内容更新后的旧草稿留给人工处理。
- 不证明真实模型的排期质量；模型只负责决策，正确性由确定性工具和测试保证。

## 4. 输入

### 4.1 请求文件

```json
{
  "request_id": "REQ-001",
  "submitted_at": "2026-11-10T09:00:00+08:00",
  "items": [
    {
      "material_id": "M00",
      "date": "2026-11-12",
      "time": "10:00",
      "constraint": "preferred",
      "channel": "wechat",
      "note": "正常排期"
    }
  ]
}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `request_id` | 是 | 请求标识，仅用于展示，不参与判重 |
| `submitted_at` | 否 | 请求提交时间，用于临期判断；缺省用运行时间 |
| `items` | 是 | 1–5 个素材项，`material_id` 不得重复 |
| `material_id` | 是 | 素材 ID |
| `date` | 是 | 期望发布日期，`YYYY-MM-DD` |
| `time` | 是 | 期望发布时间，`HH:MM` |
| `constraint` | 是 | `hard` 或 `preferred` |
| `channel` | 否 | 发布渠道，默认 `wechat` |
| `note` | 否 | 操作员备注，只展示，不参与规则 |

### 4.2 数据文件

- `data/materials.json`：素材元数据，包含 `id`、`title`、`summary`、`content_version`、`status`、`channel`、`duration_minutes`、`metadata`。
- `data/schedule.json`：已有排期，包含日期、起止时间、渠道、素材 ID、状态。
- `data/rules.json`：审核规则，包含必填字段、禁词、黑名单日期、时长上限、工作时段、步长、临期阈值。
- 运行时 `--state-dir` 下的 `drafts.json`：Agent 保存的草稿和判重索引。

### 4.3 约束类型

- `hard`：硬性发布时间。冲突时不得改时间；不保存替代草稿；只返回备选时段和建议，等用户决定是否调整需求。
- `preferred`：偏好时间。冲突时可以建议替代时段，但必须保存为 `pending_user_confirmation` 草稿，不能称为已确认或已发布。

## 5. 输出

顶层输出：

```json
{
  "schema_version": "1.0",
  "run_id": "RUN-...",
  "request_id": "REQ-001",
  "engine": "offline-mock",
  "model": "reference-policy",
  "mode_note": "离线 Mock 仅验证流程与护栏，不代表真实模型决策效果。",
  "status": "completed",
  "stop_reason": "finished",
  "published": false,
  "limits": {
    "max_steps": 12,
    "max_tool_calls": 20,
    "used_steps": 5,
    "used_tool_calls": 5
  },
  "summary": {
    "total": 1,
    "drafted": 1,
    "pending_confirmation": 0,
    "blocked_rule": 0,
    "blocked_conflict": 0,
    "needs_input": 0,
    "save_failed": 0,
    "incomplete": 0
  },
  "items": [],
  "proposed_schedule": [],
  "confirmation_required": []
}
```

每项素材必须包含：

| 字段 | 说明 |
| --- | --- |
| `material_id` | 素材 ID |
| `title` | 素材标题；素材不存在时为 `null` |
| `review_status` | `passed` / `warning` / `blocked` / `needs_input` / `unknown` |
| `risks` | 风险列表，每项含 `code`、`severity`、`message`、`field` |
| `requested_slot` | 请求时段，含日期、起止、时区、约束类型；时长未知时 `end` 为 `null` |
| `suggested_slot` | 建议时段；无可建议时段时为 `null` |
| `slot_conflict` | 冲突信息，含 `has_conflict` 和冲突条目列表 |
| `confirmation_status` | `not_required` / `pending_user_confirmation` / `user_confirmed` / `blocked` / `not_applicable` |
| `pending_items` | 待确认项，每项含 `code` 和 `message` |
| `draft` | `id`、`save_status`、`deduplicated`、`status`、`published` |
| `error` | 失败时的错误码和消息；否则为 `null` |
| `decision_basis` | 基于工具证据生成的决策依据 |
| `published` | 固定 `false` |

`proposed_schedule` 只包含已保存的草稿，按时间排序。`confirmation_required` 列出所有待确认的素材 ID。

## 6. 状态口径

### 6.1 审核状态 `review_status`

- `passed`：规则无风险，素材可进入排期。
- `warning`：只命中软警告，需要人工确认，但可以保存草稿。
- `blocked`：命中硬规则违规，不得保存草稿。
- `needs_input`：素材不存在或缺少必填字段，无法完成审核。
- `unknown`：执行上限或模型错误导致未完成审核。

### 6.2 排期状态 `schedule_status`

- `drafted`：已保存草稿，时段与请求一致，无需确认。
- `pending_confirmation`：已保存草稿，但使用了替代时段或存在软警告，需要用户确认。
- `blocked_conflict`：请求时段冲突，且不能自动改时段（硬约束或无可用替代）。
- `not_scheduled`：因缺资料或硬规则违规未进入排期。
- `save_failed`：时段可用，但草稿保存失败。
- `incomplete`：执行上限/模型错误导致未完成。

### 6.3 草稿保存状态 `draft.save_status`

- `saved`：本次新建并保存。
- `existing`：相同请求指纹已有草稿，本次未重复写入。
- `failed`：保存失败，没有草稿。
- `not_attempted`：未尝试保存。

### 6.4 确认状态 `confirmation_status`

- `not_required`：请求时段直接可用，无软风险。
- `pending_user_confirmation`：使用了替代时段或存在软警告，等待用户确认。
- `user_confirmed`：用户通过 `confirm` 子命令确认，仍然只是草稿。
- `blocked`：硬规则或硬冲突导致不能确认。
- `not_applicable`：没有草稿可确认。

## 7. 业务规则

### 7.1 必填字段

`title`、`duration_minutes`、`metadata.authorized`、`metadata.copyright_owner`、`metadata.language` 任一缺失或为空，返回 `needs_input`，列出缺失字段，不保存草稿。

### 7.2 硬规则

- `metadata.authorized` 不为 `true`：版权未授权，阻断。
- 标题或摘要命中禁词：阻断。
- 请求日期在 `blackout_dates` 中：阻断。
- `duration_minutes` 超过 `max_duration_minutes`：阻断。
- 请求时间落在工作时段外：当前作为硬风险阻断，避免生成无效排期。

### 7.3 软规则

- `duration_minutes` 超过 `long_duration_warning_minutes` 且不超过上限：软警告，需要人工确认。
- 请求开始时间距 `submitted_at` 不足 `min_lead_time_hours`：软警告，需要人工确认。

### 7.4 时段冲突

- 冲突判断基于同一渠道的已有排期和已保存草稿。
- 已保存草稿会占用规划时段；同一请求指纹的草稿在查询时排除，避免重复运行把自己判成冲突。
- 替代时段只在同一天工作时段内搜索，步长 30 分钟，按“距请求时间最近、优先向后”排序。
- 硬约束冲突：不保存草稿，返回 `blocked_conflict`，`suggested_slot` 给第一个备选。
- 软约束冲突：保存替代时段草稿，`confirmation_status=pending_user_confirmation`。
- 无任何可用替代：返回 `blocked_conflict`，`suggested_slot=null`。

### 7.5 重复提交

判重指纹：

```text
sha256(material_id | content_version | date | time | constraint | channel)
```

- `request_id` 不参与。
- 相同指纹再次保存：返回已有草稿，`save_status=existing`，`deduplicated=true`，不新增草稿。
- 内容版本变化：指纹变化，允许新草稿；旧草稿仍占用时段，可能产生冲突，需人工处理。

### 7.6 保存失败

- 存储写入失败、注入故障或磁盘错误时，返回 `save_failed`，`draft.id=null`，`draft.save_status=failed`，`published=false`。
- 不得返回“已保存”“已提交”“稍后重试即可”等模糊成功话术。
- 注入故障通过 `--fault save_draft` 触发，只用于验收和测试。

### 7.7 确认边界

- `confirm` 只更新草稿的 `confirmation_status` 为 `user_confirmed`，不改变 `status=draft`，不设置 `published=true`。
- 确认时重新检查时段冲突；如果草稿创建后时段被占用，返回 `CONFIRM_CONFLICT`，草稿保持待确认。
- 硬冲突没有草稿，无法确认；用户必须修改请求中的约束类型或时间后重新运行。

## 8. 功能需求

| 编号 | 需求 | 验收方式 |
| --- | --- | --- |
| F1 | 读取素材并识别缺失字段 | 缺资料场景无草稿，列出字段 |
| F2 | 硬/软规则检查 | 规则风险场景阻断；软警告场景 pending |
| F3 | 时段冲突与替代时段 | 软冲突建议 19:00 并保存 pending；硬冲突不落草稿 |
| F4 | 草稿保存与幂等 | 重复提交只产生一份草稿，返回同一 ID |
| F5 | 保存失败明确报告 | 注入故障场景返回 `save_failed`，store 无草稿 |
| F6 | 确认流程不发布 | `confirm` 后 `published=false`，状态仍为 draft |
| F7 | 模型决策循环 | Trace 至少两条路径展示不同工具结果导致不同动作 |
| F8 | 步数/调用上限 | 到上限返回 `incomplete`，不伪造成功 |
| F9 | 结果对账 | 模型报告与 ledger 不一致时，以 ledger 为准 |
| F10 | 可复制运行 | README 给出 CLI 命令，examples 有实际输出和 Trace |

## 9. 验收场景

| 场景 | 输入 | 期望 |
| --- | --- | --- |
| 正常排期 | M00，2026-11-12 10:00，preferred | `drafted`，草稿保存，`published=false`，无待确认 |
| 素材信息缺失 | M02，缺 `duration_minutes` 和 `metadata.copyright_owner` | `needs_input`，列出字段，无草稿 |
| 规则风险 | M03，摘要命中禁词“赌博” | `blocked`，`blocked_rule`，无草稿 |
| 时段冲突（软） | M01，2026-11-12 18:00 已占用，19:00 可用 | 建议 19:00，保存草稿，`pending_user_confirmation`，不得称为已发布 |
| 时段冲突（硬） | M04，2026-11-14 18:00 已占用，hard | `blocked_conflict`，建议 19:00，无草稿，请求时段不变 |
| 重复提交 | 同一 M01 请求再跑一次 | 仍只有一份草稿，返回同一 ID，`existing`/`deduplicated=true` |
| 保存失败 | M05，21:00 可用，`--fault save_draft` | `save_failed`，无草稿，明确失败 |
| 确认边界 | 对 M01 待确认草稿执行 `confirm` | `user_confirmed`，`status=draft`，`published=false` |
| 执行上限 | 低步数/低调用上限运行 | `status=incomplete`，未完成素材标 `incomplete` |

## 10. 成功标准

1. 七个必测场景（正常、缺资料、规则风险、软冲突、硬冲突、重复、保存失败）全部有自动化测试，测试通过。
2. 重复提交后 store 中只有一份草稿，且两次输出引用同一草稿 ID。
3. 保存失败场景 store 中没有草稿，输出没有成功声明。
4. 所有输出 `published=false`，代码中不存在发布工具或发布函数。
5. 至少两条 Trace 展示“工具结果不同 → 下一步动作不同”。
6. 执行上限触发时返回明确未完成状态。
7. README 的运行命令在干净环境中可复制执行。
8. 文档准确说明离线 Mock 的边界、真实模型 Trace 的来源和已知限制。

## 11. 约束与假设

- Python 3.12，CLI 运行，JSON 文件存储。
- 单机运行；并发安全只保证同一台机器上的进程/线程通过文件锁和线程锁串行写入。
- 时区 `Asia/Shanghai`；所有时间按本地时间处理。
- 替代时段只搜索请求当天；跨天调整需要用户修改请求。
- 一次请求最多 5 个素材；默认 12 个模型步、20 次工具调用，CLI 可调。
- 真实模型不可用时使用 `--engine offline`；输出必须标注 Mock 边界。
