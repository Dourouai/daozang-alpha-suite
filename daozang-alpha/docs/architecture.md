# 道藏 Alpha 架构

这份文档只描述 `daozang-alpha` 的工程边界。完整的“A 股投资侠客”总架构见：

- [A 股投资侠客架构与边界](investment-xiake-architecture.md)

## 项目定位

`daozang-alpha` 是投资侠客的量化研究内核。

它的职责是：

```text
读取历史数据 -> 构造因子和标签 -> 训练模型 -> 回测验证 -> 导出 alpha 分数
```

它不负责盘中执行、新闻公告抓取、飞书提醒和自动交易。

## 总体结构

```text
              +---------------------+
              | Qlib data directory |
              +----------+----------+
                         |
                         v
+------------------------+------------------------+
|                daozang-alpha                     |
|                                                 |
|  data check -> feature set -> model -> backtest |
|                         |                       |
|                         v                       |
|              alpha score export                 |
+------------------------+------------------------+
                         |
                         v
          +--------------+---------------+
          | beichen-alpha or manual desk |
          +------------------------------+
```

## 模块边界

`daozang-alpha` 负责：

- Qlib 环境和数据目录检查。
- 训练、验证、测试窗口配置。
- 量价特征和收益标签。
- 模型训练和版本记录。
- IC、RankIC、分组收益和回测评估。
- 每日模型分数导出。

`daozang-alpha` 暂时不负责：

- 新闻抓取。
- 巨潮公告解析。
- 盘中实时确认。
- 飞书推送。
- 自动下单。
- 最终买卖建议。

这些能力已经更接近 `beichen-alpha` 的职责。

## 数据流

1. 读取 Qlib 本地数据。
2. 根据配置选择股票池和时间窗口。
3. 构造特征和标签。
4. 训练模型。
5. 在验证集调参，在测试集评估。
6. 生成最新交易日的模型分数。
7. 导出 CSV/JSON。

## 导出格式草案

```csv
trade_date,instrument,score,rank,pct_rank,model,feature_set,horizon_days,universe
2026-07-03,SH600000,0.0231,1,0.998,lightgbm,Alpha158,5,csi300
```

后续可以追加：

- stock_name
- industry
- market_cap
- liquidity_score
- risk_flags
- beichen_score
- final_score

## 和 beichen-alpha 的接口

第一阶段使用文件接口，不做数据库或服务化。

`daozang-alpha` 输出：

```text
data/exports/alpha_scores_YYYY-MM-DD.csv
data/exports/alpha_scores_latest.csv
```

`beichen-alpha` 读取：

```text
trade_date
instrument
score
rank
pct_rank
model
feature_set
horizon_days
universe
```

读取后，`beichen-alpha` 再叠加：

- 市场温度。
- 行业轮动。
- 全球市场联动。
- 新闻观点。
- 巨潮公告风险。
- 实时行情确认。
- 观察区、确认价、失效线、追高线。

## 全球联动边界

`daozang-alpha` 主攻 A 股量化研究，但后续不能只看 A 股本地数据。

全球变量可以作为外生因子进入研究：

- 美股指数和美股科技链。
- 港股和中概股。
- 美债利率和美元指数。
- 人民币汇率。
- 原油、黄金、铜、铁矿石等商品。
- 主要央行、地缘和出口管制事件。

第一阶段先由 `beichen-alpha` 用规则方式做市场温度和行业偏向；第二阶段再沉淀成结构化日频特征，由 `daozang-alpha` 验证是否能提升 IC、RankIC 和分组收益。

## 新闻公告因子边界

新闻和公告可以进入 Qlib，但必须先结构化。

推荐顺序：

1. `beichen-alpha` 抓取和蒸馏新闻公告。
2. 保存为结构化事件 JSONL。
3. `daozang-alpha` 聚合成日频因子。
4. 通过 IC、RankIC、分组收益验证是否有增益。

不建议：

```text
新闻原文 -> 直接喂模型
```

因为这样噪声高、难回放、容易产生未来函数。

## 命令规划

当前已有：

- `daozang-alpha doctor`
- `daozang-alpha feasibility`
- `daozang-alpha roadmap`
- `daozang-alpha smoke-test-data`
- `daozang-alpha run-baseline`

下一步增加：

- `daozang-alpha backtest`
- `daozang-alpha export-scores`
