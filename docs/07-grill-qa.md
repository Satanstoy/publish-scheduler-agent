# 07 Grill 问题与回答（素材审核与排期）

项目 1 的完整设计在 `docs/00`–`06`。这里把面试现场最可能被追问的问题集中成 Q&A，答案尽量给代码、测试或示例位置。

## 30 秒介绍

输入一批待审素材和发布时间要求，Agent 依次读取素材、检查规则、查询时段、保存草稿，输出每项素材的审核状态、风险、建议时段、待确认项和草稿保存状态。系统只保存草稿，不接真实发布。硬约束冲突不落替代草稿，软冲突落 pending 草稿；重复提交只产生一份；保存失败如实返回，不能虚报。默认 12 个模型步、20 次工具调用，到上限返回未完成。

## 一、边界与业务

### Q1：为什么代码里没有发布工具？

题目要求交付排期草稿，不要求真实发布。工具注册表有 `get_current_time`、`get_material`、`check_rules`、`query_slots`、`save_draft`、`finish`，没有 `publish` 函数；所有输出固定 `published=false`。这样可以把“审核和排期建议”与“真实发布权限”分开，避免模型越权。

如果面试官问“那这个 Agent 有什么业务价值”，回答：它把素材审核、规则风险、时段冲突和草稿落盘串成一条可审计链路，运营只需要确认草稿，不需要手工查规则和日历。

### Q2：硬约束和软约束为什么处理不同？

`hard` 是硬性发布时间，冲突时不能改时间，也不能落替代草稿，只返回备选时段和 `blocked_conflict`；`preferred` 是偏好时间，冲突时可以建议替代时段，但草稿必须标 `pending_user_confirmation`。草稿也是排期 artifact，保存替代时段等于改变了硬性要求，所以工具层 `save_draft` 会拒绝硬约束替代。

见 `src/publish_agent/tools.py` 的 `save_draft` 校验顺序，以及 `examples/05-hard-conflict`、`examples/04-soft-conflict`。

### Q3：素材缺字段和规则风险怎么区分？

缺字段返回 `needs_input`，列出缺失的 dotted path，不查时段、不保存；规则风险分硬规则和软警告：版权未授权、禁词、黑名单日期、超长视频上限是硬阻断，临期和长视频是软警告。软警告可以落 pending 草稿，但必须人工确认。`docs/01-requirements.md` 的 7.1–7.3 有完整口径。

## 二、幂等与状态

### Q4：重复提交怎么保证只有一份草稿？

判重指纹是 `material_id + content_version + 日期 + 时间 + 约束类型 + 渠道`，不包含 `request_id`。`query_slots` 查询时排除同指纹草稿，避免重复运行把自己判成冲突；`save_draft` 在锁内再查一次指纹，命中就返回原草稿，`save_status=existing`。

`examples/06-duplicate` 连跑两次，`store.json` 只有一份草稿；测试 `test_duplicate_submission_creates_one_draft` 和 8 线程并发测试覆盖。

### Q5：内容版本更新后会不会覆盖旧草稿？

不会。`content_version` 参与指纹，版本变化视为新内容，允许生成新草稿；旧草稿不自动取消或替换。当前没有实现 supersede 和历史版本对比，这是已知边界。面试时承认这个取舍：优先保证重复提交幂等，不把版本管理做进 demo。

### Q6：保存失败后，为什么输出里还能看到素材和时段？

失败的是保存，不是审核。报告由 ledger 生成，素材、规则、时段结果都保留；`draft.id=null`、`save_status=failed`、`error.code=SAVE_FAILED`。这样运营知道“素材可排，但草稿没落盘”。`examples/07-save-failure` 和 `test_save_failure_is_reported_without_draft` 覆盖。

### Q7：模型说“已保存”但实际没保存，怎么办？

最终报告不采用模型自由文本，`report.build` 从 ledger 的 `save_draft` 结果生成 `draft.save_status` 和 `outcome`。模型说已保存但 ledger 没有草稿时，输出仍然是 `save_failed`。这是项目 1 最重要的护栏之一。

## 三、模型与循环

### Q8：为什么不用 LangGraph 或 ADK？

核心链路是 6 个工具（5 个排期工具 + 时间工具）、单层循环，手写 `AgentRunner` 大约 150 行，工具白名单、执行上限、finish 覆盖率和 Trace 都能直接讲。框架不会替我做硬约束校验和幂等，反而多一层状态图概念。面试官如果追问“团队协作怎么办”，回答：多人维护时框架的图结构和可观测性有价值，但单题 demo 优先透明和可测试。

### Q9：怎么证明模型会根据工具结果调整？

`examples/live/` 有 6 条真实模型 Trace，模型是 `opencode-go/deepseek-v4.1-flash`，走本机 OpenCode 的实验接口。可看三条调整：`02-missing-info` 提前 finish 被 `INCOMPLETE_COVERAGE` 拒绝后补做 `check_rules`；`07-save-failure` 参数错误被拒后修正；硬冲突查询后不保存。离线 mock 只证明流程和护栏，两者在文档里分开标注。

### Q10：为什么 live 用本机 OpenCode，不用标准 OpenAI？

本机直连 key 返回 `api_key_expired`。标准 OpenAI-compatible 模式保留在 `--engine live`，评审可以自备 key；live Trace 用本机 OpenCode 的实验接口生成。文档明确写了这个接口是实验性的，评审环境不一定可用，离线路径仍可复现全部验收。

### Q11：执行上限怎么设？批量 5 条会不会不够？

默认 12 个模型步、20 次工具调用。单素材大约 4–5 次调用，5 条素材可能触达 20 次上限，返回 `incomplete`。这是有意的：到上限必须如实返回未完成，不能伪造终态。`examples/10-step-limit` 和两个上限测试覆盖。生产化会把上限改成“每素材预算 + 全局预算”。

### Q12：模糊确认怎么处理？

`confirm-text` 是受限解析层，不调用模型。只有恰好一份 pending 草稿时，affirmative 词才确认；多份 pending 时“可以/继续”只返回 `needs_clarification`，不确认任何一份；带修改意图的消息不自动确认；确认时如果时段被新排期占用，返回 `confirm_conflict`，草稿保持 pending。见 `examples/11-vague-confirm`、`examples/12-ambiguous-confirm`。

## 四、安全与评估

### Q13：安全边界在哪里？

应用层：模型只能调用 6 个固定工具（含 `get_current_time`），没有 shell、网络、任意文件读写或发布工具；工具只读 `data/`，只写 `--state-dir/drafts.json`；写入用线程锁、文件锁和原子替换；`save_draft` 在工具层校验前置条件、硬约束、冲突和幂等。没有 OS/容器级 sandbox，运行不可信代码仍需容器或 VM。README 的“安全边界”一节写明了这一点。

### Q14：怎么测试硬约束和保存失败？

用 `--fault save_draft` 注入保存失败；单元测试和 CLI 测试都断言 store 无草稿、输出 `SAVE_FAILED`、`outcome=needs_attention`。硬约束由 `examples/05-hard-conflict` 和 `test_hard_conflict_does_not_save_draft` 覆盖。测试总数 89，`examples/test-results.txt` 有完整输出。

### Q15：如果评审修改素材内容，Agent 会变吗？

会。规则、时段、草稿都从 `data/` 和请求读取，不硬编码具体素材。内容版本变化会产生新指纹；规则命中会改变 `review_status`；时段冲突会改变建议。live Trace 的 `03-rule-risk` 和 `05-hard-conflict` 是两条不同动作链。

### Q16：这个项目最大的不足是什么？

不支持跨天替代、草稿取消/替换、旧版本 supersede；JSON 存储不做跨机器并发；批量 5 条的默认上限偏紧；标准 OpenAI live 没用真实 key 跑过。文档把这些写进“已知限制”，没有藏成已完成。

## 五、现场演示

```bash
python3 -m pytest -q

python3 run.py run \
  --request examples/04-soft-conflict/input.json \
  --data-dir data \
  --state-dir /tmp/grill-soft \
  --engine offline \
  --now 2026-11-10T09:00:00+08:00

python3 - <<'PY'
import json
d = json.load(open('/tmp/grill-soft/output.json'))
print(d['outcome'], d['items'][0]['schedule_status'])
print(d['items'][0]['suggested_slot'], d['items'][0]['draft'])
PY
```

保存失败：

```bash
python3 run.py run \
  --request examples/07-save-failure/input.json \
  --data-dir data \
  --state-dir /tmp/grill-fail \
  --engine offline \
  --now 2026-11-10T09:00:00+08:00 \
  --fault save_draft
```

现场重点看 `published=false`、`draft.id=null`、`error.code=SAVE_FAILED`。

## 六、不会答时的边界

- 被问真实发布：明确说没有发布工具，只保存草稿。
- 被问分布式并发：承认 JSON 存储只覆盖单机，生产要换数据库或队列。
- 被问真实模型稳定性：live Trace 只代表当前机器和当前模型，不代表所有环境。
- 被问跨天排期：当前只搜同日工作时段，跨天是下一步。
