# 研究计划

## Phase 0: 环境和数据

目标：确认 Qlib 可以在本机读取 A 股数据。

验收：

- `python -m daozang_alpha doctor` 通过。
- 数据目录存在 `calendars`、`features`、`instruments` 等结构。
- 能读取一个小股票池的日线字段。

## Phase 1: Baseline

目标：跑通最小机器学习选股闭环。

默认设定：

- 股票池：CSI300。
- 特征：Alpha158。
- 模型：LightGBM。
- 标签：未来 5 日收益。
- 频率：日线。
- 输出：Top50 模型分数。

验收：

- 有训练日志。
- 有 IC/RankIC。
- 有分组收益。
- 有交易成本后的回测结果。
- 有每日分数 CSV。

当前第一版命令：

```bash
PYTHONPATH=src .venv/bin/python -m daozang_alpha run-baseline --quick
```

输出：

- `reports/baseline_*.json`
- `data/exports/alpha_scores_*.csv`
- `data/exports/alpha_scores_latest.csv`

当前状态：

- quick baseline 已跑通，用于快速验证数据链路。
- 完整 CSI300 baseline 已跑通一次，使用 `--num-boost-round 50 --early-stopping-rounds 10`。
- 现阶段只有 IC、RankIC 和简单分组收益；交易成本、滑点、停牌和涨跌停约束还没进入评估。

## Phase 2: A 股现实约束

目标：让回测更接近真实可执行。

需要加入：

- ST 过滤。
- 停牌过滤。
- 涨跌停不可买卖限制。
- 成交额/换手过滤。
- 新股上市天数过滤。
- 交易成本和滑点。

## Phase 2.5: 全球联动因子

目标：把 A 股放回全球金融市场联动里观察。

先研究这些外生变量：

- 纳斯达克、标普 500、费城半导体指数。
- 恒生指数、恒生科技指数、中概股。
- 美债 10 年期收益率、美元指数、人民币汇率。
- 原油、黄金、铜、铁矿石等商品。
- 美联储、主要央行、地缘和出口管制事件。

要求：

- 所有全球变量必须按真实发布时间和交易时区对齐。
- 不能用未来数据解释过去 A 股走势。
- 先做规则观察和分组验证，再考虑进入主模型。

## Phase 3: 和 beichen-alpha 合并打分

目标：把模型分数作为 `beichen-alpha` 的一个因子。

合并逻辑草案：

```text
最终候选分 =
  Qlib 模型分
  + 行业轮动分
  + 市场温度分
  + 新闻观点分
  - 公告风险扣分
  - 交易拥挤扣分
```

模型分只负责“统计优势”，不负责最终买入。

## Phase 4: 模拟盘

目标：建立真实纪律。

要求：

- 每天固定时间生成候选。
- 盘后记录是否触发。
- 次日和 5 日后复盘。
- 保留所有错过、误判、止损和未执行样本。
- 不允许只保存成功案例。
