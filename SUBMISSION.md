# Submission

- 题号：项目题 1「素材审核与发布排期 Agent」
- GitHub URL：`待填写`
- 最终 commit SHA：`待填写`

## 交付规范对照

| 要求 | 当前材料 | 状态 |
| --- | --- | --- |
| README 环境、安装、运行、评审入口 | `README.md` | 已具备 |
| `src/` 代码与模型/工具接入 | `src/publish_agent/` | 已具备 |
| `tests/` 可执行测试 | `tests/`，72 个测试 | 已具备 |
| `examples/` 输入、输出、Trace | 10 组离线 + 6 组 live，`examples/README.md` | 已具备 |
| `data/` 合成资料和规则 | `data/materials.json`、`schedule.json`、`rules.json` | 已具备 |
| `docs/01`–`05` | `docs/01`–`05`，另加 `00`、`06` | 已具备 |
| 依赖文件 | `requirements.txt`、`pyproject.toml` | 已具备 |
| `.env.example` | 已提供；offline 无需变量 | 已具备 |
| 至少 3 组正常/不足/工具失败验证 | `01-normal`、`02-missing-info`、`07-save-failure` | 已具备 |
| 覆盖全部验收点 | `docs/03-evaluation.md` 的验收矩阵 | 已具备 |
| 模型决策循环 | `examples/live/` 的 6 条真实模型 Trace | 已具备 |
| 至少两条执行路径 | 正常、缺资料、规则、软/硬冲突、保存失败 | 已具备 |
| 步数/工具调用上限 | `examples/10-step-limit`、`test_step_limit_returns_incomplete`、`test_tool_limit_returns_incomplete` | 已具备 |
| 仅保存草稿、不发布 | 无 publish 工具，所有输出 `published=false` | 已具备 |
| 重复提交只产生一份草稿 | `examples/06-duplicate`、并发/重复测试 | 已具备 |
| 保存失败明确返回 | `examples/07-save-failure`、`SAVE_FAILED`、`outcome=needs_attention` | 已具备 |
| 使用框架时注明版本/职责 | 未使用框架，原生 Python 实现 | 已说明 |
| 不提交真实密钥/隐私数据 | `.env` 忽略，示例为合成数据 | 已具备 |
| GitHub URL + 最终 commit SHA | 待登录 `gh` 后 push 并填写 | 待完成 |

## 提交前检查

- [ ] GitHub 仓库公开，或已给指定评审账号只读权限。
- [ ] `git rev-parse HEAD` 与上面 SHA 一致。
- [ ] 确认 `.env`、`.runs/`、真实 key、隐私数据未提交。
- [ ] `python3 -m pytest -q` 全部通过。
- [ ] `examples/01-normal`、`examples/02-missing-info`、`examples/07-save-failure` 的 input/output/trace 存在。
- [ ] README 的运行命令能从仓库根目录直接复制执行。
- [ ] `docs/01` 到 `docs/05` 已读完，内容与实际行为一致。

## 评审入口

```bash
python3 -m pytest -q
python3 run.py run \
  --request examples/01-normal/input.json \
  --data-dir data \
  --state-dir .runs/review \
  --engine offline \
  --now 2026-11-10T09:00:00+08:00 \
  --output /tmp/review-output.json \
  --trace /tmp/review-trace.json
cat /tmp/review-output.json
```
