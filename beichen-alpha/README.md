# Beichen Alpha

Beichen Alpha 是一个个人自用的 A 股候选池推荐助手原型。

它的边界很明确：

- 不自动交易
- 不直接下单
- 不承诺收益
- 只输出候选股票、评分、买入观察区、确认价、失效线和风险提示

当前版本开始接入真实数据源：默认通过 AKShare 拉取 A 股日线行情、市场温度、行业轮动、风险日历、普通新闻和巨潮公告；也可以用 BaoStock 作为 A 股历史行情兜底。

## 安装数据依赖

```bash
python3 -m pip install akshare baostock pandas yfinance
```

AKShare 会访问腾讯等上游公开行情源；如果网络、代理或上游接口异常，命令会返回数据源错误提示。
BaoStock 可用作历史行情备用源，适合和 AKShare / Qlib 做交叉校验。
yfinance 用于全球联动模型特征源；FRED 使用公开 CSV，不需要额外 Python 依赖。

安装文章投喂依赖：

```bash
python3 -m pip install beautifulsoup4
```

## 快速运行

```bash
PYTHONPATH=src python3 -m beichen_alpha \
  --cycle balanced \
  --horizon ultra_short_2_3d \
  --universe-limit 30 \
  --limit 5 \
  --disable-news \
  --disable-disclosures
```

默认会使用全市场动态初筛，不需要手工维护股票池。
上面这条是快速试跑命令，会先跳过普通新闻和巨潮公告源；风险日历仍会检查解禁和质押。如果要启用完整公告风险源，去掉 `--disable-news --disable-disclosures`，但运行时间会更长。
市场温度和行业轮动默认开启；如果只是调试主流程，也可以加 `--disable-market-regime --disable-sector-rotation`。

默认策略：

- 只保留总市值不低于 `300` 亿的大中盘股票。
- 默认排除 `消费`、`品牌消费` 主题。
- 默认持有周期为 `ultra_short_2_3d`，即 2-3 个交易日的超短线候选池。
- 可用 `--cycle defensive|recovery|growth|inflation|balanced` 切换宏观周期偏好。
- 可用 `--horizon ultra_short_2_3d|short_3_5d|position_10_20d` 切换持有周期。
- 默认会读取官方 RSS 和 `config/macro_events.csv` 作为宏观事件源；没有有效事件时按中性处理。
- 可加 `--realtime` 拉取腾讯实时行情快照，用当前价判断是否站上确认价、是否追高或盘中失效。
- 实时执行层默认启用站稳因子：结果写入 `data/runtime/realtime_checks.json`，连续两次站上稳确认价且间隔不低于 5 分钟才标记为 `实时可买`。
- 周五/T+1 风控会更严格：周五新开仓要求高于确认价约 0.5%，否则降级为 `周五观察`。
- 实时执行层还启用板块共振因子：同板块至少 2 只、且至少 50% 样本同步站上稳确认价，个股才可进入 `实时可买`；否则降级为 `板块未共振`。

指定日期范围：

```bash
PYTHONPATH=src python3 -m beichen_alpha --symbols 600160,300498 --start 20250101 --end 20260702
```

使用自选池文件：

```bash
PYTHONPATH=src python3 -m beichen_alpha --watchlist path/to/watchlist.txt
```

使用 BaoStock 作为历史行情源：

```bash
PYTHONPATH=src python3 -m beichen_alpha \
  --source baostock \
  --symbols 600036,600025 \
  --benchmark 000300 \
  --start 20250101 \
  --end 20260703
```

同步全球联动模型特征：

```bash
PYTHONPATH=src python3 -m beichen_alpha sync-global-features
```

这个命令会读取 FRED 的美债、美元、信用和金融条件序列，并用 yfinance 读取美股、港股、VIX、离岸人民币、黄金和原油，默认落盘到 `data/features/global_linkage_daily.csv`。该数据是模型特征底座，不直接进入候选池评分；后续训练时需要按 as-of date 对齐并滞后，避免未来函数。

`watchlist.txt` 一行一个股票代码：

```text
600160
300498
002120
```

使用全市场动态初筛：

```bash
PYTHONPATH=src python3 -m beichen_alpha \
  --universe-limit 60 \
  --universe-candidates 300 \
  --min-turnover 5 \
  --horizon ultra_short_2_3d \
  --cycle balanced
```

同步全股票画像缓存，不抓 K 线：

```bash
PYTHONPATH=src python3 -m beichen_alpha sync-universe
```

默认会保存到 `data/cache/universe_latest.jsonl`，包含全 A 股票代码、名称、总市值、推断行业和主题。推荐时会优先读取这个缓存，再只对入围股票拉取日线。

动态初筛流程：

- 优先读取本地全股票画像缓存。
- 没有缓存时，优先用新浪全 A 快照；如果新浪快照临时不可用，降级为全 A 代码表 + 腾讯总市值排序。
- 默认排除 ST、退市、新股前缀、消费/品牌消费、小市值。
- 再拉取入围股票日线、公告、普通新闻和个人观点源做评分。
- 默认额外拉取市场温度和行业轮动：市场温度看指数趋势、全 A 上涨占比、涨跌停数量和成交额；行业轮动优先看东方财富行业板块的 3/5 日强弱和量能，如果行业板块接口不可用，会降级为用候选池 K 线按行业聚合。
- 默认额外读取宏观/政策事件源：优先从官方 RSS 抓 Fed/BEA 等事件，并从官方政策列表页抓财政、税费、产业、流动性等标题信号，再叠加 `config/macro_events.csv` 人工覆盖，把美国就业、通胀、美联储官员讲话、美元、美债、原油、地缘风险、费城半导体、日韩半导体链和国内政策事件映射到 A 股行业偏向，并按有效期衰减。
- 默认额外拉取风险日历：检查未来窗口限售解禁、存续股权质押压力，并把巨潮公告里的预亏、减持、诉讼、处罚、退市风险映射为硬风控事件。

刷新动态基础池 50 只：

```bash
PYTHONPATH=src python3 -m beichen_alpha daily-refresh-pool \
  --pool-size 50 \
  --scan-limit 120 \
  --profile config/profile_overrides.csv
```

刷新后会生成：

- `data/watchlists/broad_target_pool_YYYY-MM-DD.txt`：当天基础池快照。
- `data/watchlists/broad_target_pool_latest.txt`：最新基础池，供盘中重点池筛选使用。
- 控制台输出 `新增`、`移除`、`保留` 和当前前 10 名。

如果已配置飞书 webhook，可直接推送刷新结果：

```bash
scripts/beichen_daily_refresh_pool.sh
```

可选画像修正文件只用于补充行业/主题，不作为股票池：

```bash
PYTHONPATH=src python3 -m beichen_alpha --profile config/profile_overrides.csv
```

股票画像支持主行业、辅行业、风格标签和概念标签。旧的 `industry/themes` 仍兼容，系统会自动拆分；后续精细维护建议参考 [`docs/stock_profile_schema.md`](docs/stock_profile_schema.md)。

运行测试：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

## 服务器预备

服务器部署不是自动交易，只用于定时研究、飞书提醒、决策日志和模型数据刷新。

本项目已经提供两个服务器入口：

```bash
./scripts/server_healthcheck.sh
./scripts/server_daily_run.sh
./scripts/beichen_chat_server.sh
```

`server_healthcheck.sh` 会检查持仓文件、候选池、日志目录、运行目录、飞书 webhook 和道藏模型分数是否就绪。

`server_daily_run.sh` 默认只运行健康检查和 3 日交易计划；可以通过 `config/local.env` 打开更重的任务：

```bash
export RUN_POOL_REFRESH="true"
export RUN_FOCUS_CHECK="true"
```

完整部署步骤见 monorepo 根目录的 [`docs/deployment.md`](../docs/deployment.md)。

## 飞书推送

复制或编辑本地密钥文件：

```bash
cp config/local.env.example config/local.env
```

在 `config/local.env` 中填入自定义机器人的 webhook：

```bash
export FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/..."
export FEISHU_SECRET=""
```

如果飞书机器人开启了“签名校验”，把签名密钥填到 `FEISHU_SECRET`；未开启则保持空字符串。

发送测试消息：

```bash
./scripts/feishu_test.sh
```

重点池脚本会自动读取 `config/local.env`，配置了 webhook 时推送到飞书；未配置时只在本地输出结果：

```bash
./scripts/beichen_focus_pool_feishu.sh
```

该脚本默认会开启实时行情确认，并在飞书消息里显示：

- `实时可买`：连续两次站上稳确认价，且未超过确认价上方 2% 追高线。
- `待站稳`：当前价已经站上稳确认价，但缺少上一轮站稳记录，或站稳时间不足 5 分钟。
- `板块未共振`：个股已满足价格/站稳条件，但同板块样本同步确认不足，暂不执行。
- `周五观察`：周五/T+1 风控下，当前价过了普通确认但没达到更高的周五确认缓冲。
- `贴线观察`：当前价刚站上确认价但缓冲不足，容易假突破，暂不执行。
- `接近确认`：距离确认价不足 1%，继续等待触发。
- `未触发`：尚未站上确认价。
- `已追高`：当前价超过追高线，不追。
- `盘中失效`：当前价跌破失效线，不执行。

注意 A 股普通股票为 T+1：买入当日不可卖出。买入后的盘中失效只记录为风险预警和次交易日处理计划。

飞书推送默认使用卡片样式，保留精简执行字段，避免纯文本长消息难以阅读。需要旧版纯文本时可加：

```bash
PYTHONPATH=src python3 -m beichen_alpha --notify feishu --notify-style text
```

## 飞书对话适配层

自定义机器人 webhook 只能单向推送，不能接收用户消息。要让北辰在飞书里“可对话”，需要创建飞书应用，开启事件订阅和消息回复权限，然后把事件回调地址指向北辰服务：

```bash
./scripts/beichen_chat_server.sh
```

本地服务入口：

- `GET /health`：健康检查。
- `POST /feishu/events`：飞书应用事件回调。

本地环境变量仍然放在 `config/local.env`：

```bash
export FEISHU_APP_ID=""
export FEISHU_APP_SECRET=""
export FEISHU_EVENT_VERIFY_TOKEN=""
export FEISHU_CHAT_HOST="127.0.0.1"
export FEISHU_CHAT_PORT="8787"
```

第一版支持的对话命令：

- `帮助`：查看命令菜单。
- `状态`：检查本地运行状态。
- `持仓`：查看本地持仓摘要。
- `计划`：查看最近一次 3 日交易计划。
- `日志`：查看决策日志摘要。

道藏 Alpha 不再维护独立飞书 webhook。道藏只输出模型分数和研究报告，后续由北辰读取并通过同一个飞书入口推送或回复。

## 投喂博主观点

手动投喂微信公众号文章链接：

```bash
PYTHONPATH=src python3 -m beichen_alpha ingest --url "https://mp.weixin.qq.com/s/..." --source-name "在下刀哥"
```

手动投喂视频号、抖音或其他来源的文字总结：

```bash
PYTHONPATH=src python3 -m beichen_alpha ingest \
  --text "7月2日市场总结..." \
  --title "7月2日市场总结" \
  --source-name "许戈" \
  --author "许戈" \
  --published-at "2026-07-02"
```

默认只保存蒸馏后的观点信号到 `data/opinion_signals.jsonl`，不会保存文章全文。当前规则会提取：

- 标题、来源、作者、发布时间
- 信号日期、投喂日期、规则版本、命中规则
- 主题方向，例如 AI 硬件、存储、算力、非银金融
- 风险标签，例如拥挤交易、周期估值、海外宏观偏鹰
- 与动态候选池/可选画像修正的映射；消费主题标的默认不自动映射

`data/opinion_signals.jsonl` 同时会作为个人观点新闻源参与推荐评分。它默认读取最近 7 天的观点信号：

- 个人观点会单独做更强的时效衰减：12 小时内权重最高，1-3 天快速下降，超过 5 天基本不再影响评分。

```bash
PYTHONPATH=src python3 -m beichen_alpha --cycle defensive --opinion-lookback-days 7
```

关闭个人观点新闻源：

```bash
PYTHONPATH=src python3 -m beichen_alpha --cycle defensive --disable-opinions
```

只试跑、不落盘：

```bash
PYTHONPATH=src python3 -m beichen_alpha ingest --url "https://mp.weixin.qq.com/s/..." --source-name "在下刀哥" --dry-run
```

## 宏观事件源

宏观事件源适合记录“事件 -> 资产含义 -> A 股行业映射”，例如美国就业弱于预期、美联储官员偏鹰/偏鸽、CPI/PCE 超预期、美元/美债快速变化、原油冲击、地缘风险、费城半导体、日韩半导体链波动，以及国内财政、税费、产业、资本市场和监管政策。

自动 RSS 源默认读取：

```bash
config/macro_rss_feeds.csv
```

当前默认启用 Fed 讲话、Fed 货币政策新闻和 BEA 新闻发布 RSS；BLS RSS 模板保留在配置里，但默认关闭，避免脚本访问被 403 时拖慢早盘任务。

官方政策列表页默认读取：

```bash
config/macro_policy_pages.csv
```

当前默认启用财政部政策发布、国家发改委通知和人民银行新闻页面。页面标题只作为低/中置信度政策事件信号，进入 `policy_event` 分类后再映射行业；它不会跳过板块共振、风险日历和实时站稳确认。

人工覆盖源默认读取：

```bash
config/macro_events.csv
```

启用一条事件时，把模板行的 `enabled` 改成 `true`，并确认日期、方向、利好/利空行业、有效期和置信度：

```csv
true,2026-07-03,美国就业弱于预期,manual,us_jobs,dovish,"黄金/有色/半导体/AI硬件/医药","银行/煤炭",8,2,0.80,降息预期升温,
```

关闭宏观事件因子：

```bash
PYTHONPATH=src python3 -m beichen_alpha --disable-macro-events
```

只关闭 RSS、保留人工覆盖：

```bash
PYTHONPATH=src python3 -m beichen_alpha --disable-macro-rss
```

只关闭官方政策页：

```bash
PYTHONPATH=src python3 -m beichen_alpha --disable-policy-pages
```

详细枚举见 [`docs/macro_event_factor.md`](docs/macro_event_factor.md)。

## 架构

当前项目拆成几层：

```text
data_sources / news_sources / disclosure_sources  ->  strategy  ->  reports / cli
content_sources -> distill -> data/opinion_signals.jsonl
```

- `data_sources`: 数据源适配器，目前支持 AKShare、BaoStock、Qlib 本地 bin 和 CSV。
- `news_sources`: 新闻源适配器，目前支持 AKShare 个股新闻，也会把个人观点信号转成低权重新闻事件。
- `disclosure_sources`: 公告源适配器，目前支持巨潮信息披露公告。
- `risk_sources`: 风险日历适配器，目前支持限售解禁、股权质押，并复用公告硬风险。
- `content_sources`: 手动投喂内容源，目前支持微信公众号文章链接。
- `distill`: 把文章正文蒸馏成带日期和规则的观点信号，不保存全文。
- `strategy`: 因子、价位、推荐排序，保持和数据源无关。
- `reports`: 控制台或未来页面展示。
- `cli`: 只负责参数解析和层之间的组装。

详细说明见 [`docs/architecture.md`](docs/architecture.md)。

## 当前因子

当前保留一组容易解释、容易回测的核心因子：

| 因子 | 目的 |
|---|---|
| 流动性 | 过滤成交额太小的股票 |
| 趋势 | 判断是否处在短线强势结构 |
| 相对强弱 | 判断是否强于沪深300样例基准 |
| 回踩承接 | 避免追高，寻找靠近 5 日线的位置 |
| 量能 | 判断是否有资金参与 |
| 风险距离 | 控制买入价到失效线的亏损空间 |
| 短线动量 | 3 日相对强弱，用于 3-5 个交易日持有周期 |
| 短线过热 | 避免 5 日涨幅或距 5 日线过大时追高 |
| 3-5日赔率 | 控制短线买点到失效线的距离 |
| 市场温度 | 判断当前环境是否适合做 3-5 天短线，偏冷扣分、偏暖加分、过热谨慎 |
| 风格偏向 | 根据当前 `cycle` 给红利、防御、成长、通胀、复苏等风格做轻量加减分；`balanced` 已降低防御/能源权重 |
| 宏观事件 | 把美国就业、通胀、美联储官员讲话、美元、美债、原油、地缘风险、费半和日韩半导体链映射为 A 股行业加减分 |
| 行业轮动 | 判断个股所属行业是否处在 3/5 日相对强势和放量状态 |
| 产业链传导 | 判断 AI→半导体→电子/材料/化工/资源、新能源→材料/资源、金融链等是否出现健康接力或退潮补涨风险 |
| 风险日历 | 识别未来解禁、股权质押、减持、问询处罚、诉讼、业绩预警等短线踩雷风险 |
| 公告风险 | 识别巨潮公告里的预亏、减持、诉讼、处罚、退市风险，重大风险直接排除 |
| 新闻事件 | 识别近窗利好/利空事件；普通新闻和个人观点源只做加减分，重大硬风险优先交给公告源 |

公告风险因子默认开启：

```bash
PYTHONPATH=src python3 -m beichen_alpha --cycle defensive --disclosure-lookback-days 60
```

关闭公告风险因子：

```bash
PYTHONPATH=src python3 -m beichen_alpha --cycle defensive --disable-disclosures
```

关闭风险日历，或只跳过股权质押检查：

```bash
PYTHONPATH=src python3 -m beichen_alpha --disable-risk-calendar
PYTHONPATH=src python3 -m beichen_alpha --disable-pledge-risk
```

AKShare 普通新闻源默认开启：

```bash
PYTHONPATH=src python3 -m beichen_alpha --cycle defensive --news-lookback-days 7
```

关闭 AKShare 普通新闻源：

```bash
PYTHONPATH=src python3 -m beichen_alpha --cycle defensive --disable-news
```

关闭市场温度和行业轮动源：

```bash
PYTHONPATH=src python3 -m beichen_alpha --disable-market-regime --disable-sector-rotation
```

## 输出字段

- `score`: 兼容旧字段，目前等同于 `candidate_score`
- `candidate_score`: 候选评分，用来判断“值不值得看”；由大盘环境、宏观事件、风格偏向、行业共振、个股强弱、流动性、观点偏向、基本质量和风险扣分汇总。
- `candidate_breakdown`: 候选评分拆分，例如 `大盘环境+20 宏观事件+8 风格偏向+6 行业共振+18 个股强弱+72 流动性+30 风险扣分-14`。
- `macro_events`: 宏观事件匹配摘要，例如 `美国就业弱于预期 dovish 黄金+8`。
- `execution_score`: 实时执行评分，仅在开启 `--realtime` 时输出；由实时站稳、放量确认、VWAP、板块同步、宏观同步、追高惩罚和周五/T+1 惩罚汇总。
- `execution_breakdown`: 执行评分拆分，用来解释为什么是 `实时可买`、`待站稳`、`接近确认` 或 `不执行`。
- `status`: 可执行、条件执行、观察、突破、等待、偏离、失效、排除
- `market_temperature`: 市场温度
- `sector_rotation`: 行业轮动匹配结果
- `risk_calendar`: 风险日历摘要，`硬:` 表示一票否决风险，`警:` 表示重扣分风险
- `observation_zone`: 买入观察区
- `confirm_price`: 确认价
- `invalid_price`: 失效线
- `take_profit_price`: 2-3 天/3-5 天短线止盈参考价
- `holding_period`: 默认 2-3 个交易日
- `sell_plan`: 第 3 个交易日复核、第 5 个交易日未延续降低仓位，以及符合 A 股 T+1 的止盈/风控计划
- `reason`: 推荐理由
- `risk`: 风险提示

## 后续计划

1. 加全市场初筛
2. 加板块过滤
3. 加 Streamlit 页面
4. 加简单回测
5. 加 LLM 解释层

本项目仅用于研究和测试，不构成投资建议。
