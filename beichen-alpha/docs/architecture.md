# Beichen Alpha Architecture

Beichen Alpha follows a small layered architecture. The goal is to keep market data, strategy logic, and presentation separate while staying simple enough for personal research.

```text
universe_sources -> data_sources -> strategy -> reports / cli
news_sources  ->     |
disclosure_sources -> |
risk_sources ->       |
      |              |
      v              v
Bar / Event data  Recommendation

content_sources -> distill -> data/opinion_signals.jsonl -> opinion news source
```

## Layers

### data_sources

Data source adapters load normalized market data records.

Current adapters:

- `AksharePriceSource`: loads A-share daily bars from AKShare.
- `AkshareMarketRegimeSource`: loads index bars and full-market spot breadth data, then produces a `MarketRegime` snapshot.
- `AkshareSectorRotationSource`: loads Eastmoney industry board spot and history data, then produces normalized `SectorSignal` records. If the board endpoint is unavailable, the CLI falls back to aggregating already-loaded candidate bars by normalized industry.
- `CsvPriceSource`: reads normalized CSV bars for offline tests or imported data.

### universe_sources

Universe source adapters select which symbols should be scored before price bars are loaded.

Current adapter:

- `AkshareUniverseSource`: builds the default dynamic large-cap universe. It first tries Sina full-market spot data for turnover-based screening; if that source is blocked or unavailable, it falls back to the full A-share code list plus Tencent market-cap profiles. It excludes ST, delisting names, new listings, consumer themes, and small caps.
- Optional profile CSV files are metadata overrides only. They do not define the default recommendation universe.
- `sync-universe`: stores all-stock profile data in `data/cache/universe_latest.jsonl`. This cache contains code, name, inferred industry/themes, market cap, and spot metadata; it does not store K-line history.
- Recommendation runs read the all-stock profile cache first, then fetch K-lines only for the selected scoring universe.

### news_sources

News source adapters load normalized `NewsEvent` records.

Current adapters:

- `AkshareNewsSource`: loads recent stock news through AKShare and classifies titles into event types.
- `OpinionSignalNewsSource`: loads local distilled opinion signals from JSONL and converts recent matching signals into low-weight `NewsEvent` records.

### disclosure_sources

Disclosure source adapters load official announcement events into the same normalized `NewsEvent` records.

Current adapter:

- `CninfoDisclosureSource`: loads CNINFO information-disclosure announcements through AKShare. It focuses on earnings warnings, shareholder reductions, litigation, penalties, and delisting-risk disclosures.

### risk_sources

Risk calendar adapters load forward-looking or persistent risk events into normalized `RiskCalendarEvent` records.

Current adapter:

- `AkshareRiskCalendarSource`: loads upcoming restricted-share release batches and per-stock pledge pressure through AKShare. The CLI also maps hard negative disclosure events into the risk calendar so earnings warnings, reductions, inquiries, litigation, penalties, and delisting risks can act as one-vote veto events.

### content_sources

Content source adapters load manually supplied creator content into normalized `ArticleContent` records.

Current adapter:

- `WechatArticleSource`: loads a single WeChat article URL, extracts the title, author, publish time, and main body text. The full article body is used only in memory during ingestion.

### distill

Distillation code converts `ArticleContent` into compact `OpinionSignal` records.

- `rule_distiller.py`: extracts themes, stance, risk flags, and optional symbol mappings with deterministic keyword rules.
- Output is appended to `data/opinion_signals.jsonl`; generated JSONL files are ignored by git because they are personal research data.
- Every signal stores `signal_date`, `ingested_at`, `published_at`, `rule_version`, and `matched_rules` so the recommendation can be audited later.

### strategy

Strategy code is deterministic and data-source agnostic.

- `factors.py`: computes factor scores.
- `levels.py`: computes observation zone, confirmation price, and invalidation price.
- `engine.py`: builds and ranks recommendations.
- `policy.py`: applies large-cap, economic-cycle, and industry-theme rules.
- `market_factor.py`: scores market temperature and industry rotation. These factors are data-source agnostic and accept normalized `MarketRegime` / `SectorSignal` objects.
- `disclosure_factor.py`: scores official announcements and hard-excludes major disclosure risks.
- `risk_calendar_factor.py`: scores risk-calendar events. Hard events exclude the candidate; non-hard but severe events apply a large penalty.
- `news_factor.py`: scores recent news events and hard-excludes major risk events.
- Personal opinion news events are scored by the same news factor, but they are intentionally low weight and never hard-exclude a stock.
- The default horizon is `short_3_5d`: the strategy adds short-term momentum, overheat, and risk/reward checks, then outputs a 3-5 trading day sell plan.

### reports

Report code formats recommendations for humans.

- `console.py`: renders CLI table output.

### cli

The CLI wires the layers together. It should stay thin:

1. Parse arguments.
2. Load bars, market regime, sector rotation, risk calendar, news, and disclosures from selected sources.
3. Run the recommendation engine.
4. Render the report.

For article ingestion, the CLI follows a separate small path:

1. Parse the supplied article URL.
2. Extract article content in memory.
3. Distill it into an `OpinionSignal`.
4. Append the signal to local JSONL storage unless `--dry-run` is set.

## Design Rules

- Do not place data-fetching code inside strategy modules.
- Do not place scoring logic inside the CLI.
- Do not store full creator articles; store compact signals, source metadata, and links.
- Treat creator opinions as personal research inputs. They may add or subtract score, but official disclosures and price-risk rules take precedence.
- Every factor must be explainable with formula, score, pass condition, and detail.
- Risk calendar events should never require full-market heavy endpoints in the daily path unless they are cached or explicitly requested.
- Every recommendation must include an invalidation price.
- Every short-term recommendation must include a holding period, take-profit reference, and exit plan.
- Automatic trading is out of scope.
