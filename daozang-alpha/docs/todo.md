# 道藏 Alpha 待办

## 北辰统一飞书入口

- [ ] 通过北辰 Alpha 飞书适配层推送道藏模型摘要。

  边界：

  - 不维护独立道藏飞书机器人。
  - 不在道藏配置中保存 webhook。
  - 道藏只产出模型分数、回测报告和研究日志。
  - 北辰统一负责飞书通知、对话入口和后续人机协作流程。

  后续待实现命令规划：

  ```bash
  cd ../beichen-alpha
  PYTHONPATH=src python3 -m beichen_alpha trade-plan --with-daozang-scores
  ```
