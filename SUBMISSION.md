# Submission

- 题号：项目题 1「素材审核与发布排期 Agent」
- GitHub URL：`待填写`
- 最终 commit SHA：`待填写`

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
