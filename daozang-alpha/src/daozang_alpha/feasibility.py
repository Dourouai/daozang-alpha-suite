from __future__ import annotations


def render_feasibility() -> str:
    return """道藏 Alpha 可行性结论

1. 可以用 Qlib，但要把它放在正确的位置。
   Qlib 适合做离线研究、特征工程、模型训练、回测和每日模型分数导出。

2. 不建议让 Qlib 直接变成自动荐股或自动交易系统。
   A 股里的复权、停牌、涨跌停、ST、退市、公告时点和流动性约束都会影响真实表现。

3. 第一阶段应该先做 baseline，而不是追复杂模型。
   推荐从 CSI300/CSI500 + Alpha158 + LightGBM + 未来 5 日收益标签开始。

4. 判断项目是否有价值，要看 IC、RankIC、分组收益、交易成本后收益和跨年份稳定性。
   单条漂亮净值曲线不够。

5. 最理想的形态是：
   daozang-alpha 负责模型分数；beichen-alpha 负责实时行情、公告新闻、风控和提醒。
"""


def render_roadmap() -> str:
    return """道藏 Alpha 路线图

Phase 0: 环境检查
- 安装 pyqlib/lightgbm。
- 准备本地 Qlib CN 数据。
- 跑通 doctor。

Phase 1: Baseline
- CSI300 或 CSI500。
- Alpha158。
- LightGBM。
- 未来 5 日收益标签。
- 输出 IC、RankIC、分组收益和 Top50 分数。

Phase 2: 现实约束
- 加入 ST、停牌、涨跌停、新股、流动性过滤。
- 加入交易成本和滑点。
- 做滚动训练和分年度评估。

Phase 3: 融合助手
- 导出模型分数给 beichen-alpha。
- 和行业轮动、市场温度、公告风险、新闻观点合并。

Phase 4: 模拟盘
- 每天固定生成候选。
- 记录触发、未触发、止损、错过和复盘。
- 用真实纪律检验模型，而不是只看历史回测。
"""
