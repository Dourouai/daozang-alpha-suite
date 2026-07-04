# 道藏 Alpha

道藏 Alpha 是一个个人自用的 A 股量化研究与股票候选评分项目。

它的第一定位不是自动交易，也不是“保证收益”的荐股系统，而是：

- 用 Microsoft Qlib 做离线研究、因子建模、训练和回测。
- 每天输出可解释的模型候选分数。
- 把 A 股放在全球市场联动里观察，不只看本地行情。
- 把模型分数交给人工判断，或后续接入 `beichen-alpha` 的新闻、公告、实时行情和风控层。

## 先说结论

用 `microsoft/qlib` 做 A 股推荐助手是可行的，但它更适合作为“研究引擎”，不适合单独承担完整产品。

Qlib 擅长：

- 标准化行情数据读取。
- 因子表达式和特征工程。
- Alpha158、Alpha360 等基础特征集。
- LightGBM、深度模型等监督学习流程。
- 回测、组合分析、实验管理。

Qlib 不自动解决：

- A 股高质量数据源问题。
- 复权、停牌、涨跌停、ST、退市、财报发布时间等细节清洗。
- 新闻、公告、政策、舆情和盘中执行。
- 合规边界和风险提示。
- 从模型分数到真实买卖纪律的完整闭环。

所以本项目的正确方向是：

```text
Qlib 离线研究 -> 模型分数 -> 风险过滤 -> 人工确认/候选池
```

而不是：

```text
Qlib -> 直接买卖
```

## 和 beichen-alpha 的关系

当前工作区已有 `beichen-alpha`，它已经在做 A 股候选池、规则评分、市场温度、行业轮动、公告风险、新闻观点、飞书推送和飞书对话适配。

建议分工如下：

| 项目 | 定位 | 输出 |
| --- | --- | --- |
| `daozang-alpha` | Qlib 研究引擎、模型训练、回测、每日 alpha 分数 | `data/exports/alpha_scores_YYYY-MM-DD.csv` |
| `beichen-alpha` | 执行前助手、实时行情、新闻公告、风险过滤、飞书提醒、飞书对话入口 | 当日候选池和执行提示 |

后续可以让 `beichen-alpha` 读取 `daozang-alpha` 的模型分数，把“机器学习分”作为一个新增因子。

飞书入口统一归北辰维护。道藏不再保存独立 webhook，也不直接面向飞书用户；它只产出模型分数、回测报告和研究日志。

完整边界见：

- [A 股投资侠客架构与边界](docs/investment-xiake-architecture.md)
- [A 股投资侠客基础门规](docs/xiake-rules.md)
- [道藏 Alpha 架构](docs/architecture.md)

## 项目边界

- 不自动下单。
- 不承诺收益。
- 不把单次回测收益当成真实能力。
- 不使用未来函数。
- 不在没有交易成本、滑点、停牌和涨跌停限制的情况下评价策略。
- 所有输出只作为研究候选，不构成投资建议。

## 第一阶段目标

第一阶段只做最小可验证闭环：

1. 检查本地 Qlib 环境和数据目录。
2. 固定一个 A 股股票池，比如 CSI300 或 CSI500。
3. 用日线数据构造 5 日或 10 日收益标签。
4. 跑一个 baseline：`Alpha158 + LightGBM`。
5. 做 walk-forward 回测和 IC/RankIC 检查。
6. 每天导出 TopN 候选分数，不进入自动交易。

## 推荐环境

```bash
cd /Users/yancy/Documents/vibe-project/daozang-alpha-suite/daozang-alpha
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[research]"
```

如果在 Apple Silicon Mac 上安装 LightGBM 遇到 OpenMP 问题，通常需要先安装：

```bash
brew install libomp
```

如果本机没有 Homebrew，但 `scikit-learn` wheel 已自带 `libomp.dylib`，可以运行：

```bash
bash scripts/fix_lightgbm_libomp.sh
```

## Qlib 数据

Qlib 需要先准备本地数据目录。默认配置指向项目本地目录：

```text
data/qlib/cn_data
```

可以通过环境变量覆盖：

```bash
export DAOZANG_QLIB_PROVIDER_URI="$HOME/.qlib/qlib_data/cn_data"
```

第一步推荐使用 `chenditc/investment_data` 的 Qlib 格式社区数据包：

```bash
cd /Users/yancy/Documents/vibe-project/daozang-alpha-suite/daozang-alpha
bash scripts/setup_chenditc_qlib_data.sh
```

运行环境检查：

```bash
python -m daozang_alpha doctor
```

如果还没有安装 Qlib，`doctor` 会提示；如果数据目录不存在，也会提示下一步该准备数据。

## 当前命令

查看可行性分析：

```bash
python -m daozang_alpha feasibility
```

查看路线图：

```bash
python -m daozang_alpha roadmap
```

检查本地环境：

```bash
python -m daozang_alpha doctor
```

读取一段 Qlib 样本行情：

```bash
python -m daozang_alpha smoke-test-data
```

运行第一版快速 baseline：

```bash
python -m daozang_alpha run-baseline --quick
```

快速 baseline 会使用 `CSI300 + Alpha158 + LightGBM + 未来 5 日收益标签`，但只取较短时间窗口和 20 只股票验证链路。完整跑法：

```bash
python -m daozang_alpha run-baseline
```

本机第一次完整 CSI300 跑通时使用了较轻的训练轮数：

```bash
python -m daozang_alpha run-baseline --num-boost-round 50 --early-stopping-rounds 10
```

运行轻量测试：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

## 目录结构

```text
daozang-alpha/
  config/                 # 配置样例
  data/                   # 本地数据和导出文件，默认不入库
  docs/                   # 可行性、架构和研究计划
  reports/                # 回测报告输出，默认不入库
  src/daozang_alpha/       # Python 包
  tests/                  # 轻量测试
```

## 下一步

下一次开发建议从这三件事开始：

1. 给 `run-baseline` 增加交易成本、滑点、停牌和涨跌停约束。
2. 增加 `export-scores` 命令，把最新模型分导出为稳定接口，供人工或 `beichen-alpha` 使用。
3. 增加实验记录表，保存每次训练窗口、模型参数、IC/RankIC 和分组收益。
