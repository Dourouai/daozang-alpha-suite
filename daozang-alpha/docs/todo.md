# 道藏 Alpha 待办

## Feishu

- [ ] 添加道藏 Alpha 飞书机器人助手。

  目标：让道藏 Alpha 后续可以把环境检查、数据 smoke test、baseline 回测结果、每日 alpha 分数和风险摘要推送到飞书。

  边界：

  - 不在代码或文档中提交完整 webhook。
  - 本地 webhook 存放在 `config/local.env`。
  - 运行时读取 `DAOZANG_FEISHU_WEBHOOK`。
  - 第一版只做手动触发推送，不做自动定时任务。

  初始命令规划：

  ```bash
  python -m daozang_alpha notify-feishu --message "道藏 Alpha smoke test passed"
  python -m daozang_alpha notify-feishu --from-report reports/latest_summary.md
  ```
