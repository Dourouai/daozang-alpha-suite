"""Policy keyword factor: 政策关键词→行业映射.

Scores policy events (RSS, policy pages, macro CSV) by keyword matching
against stock sectors. Higher policy-source tier → higher weight.

Policy tier weights:
- Tier 1 (国务院/中央政治局/中央经济工作会议): ×3
- Tier 2 (部委/央行/证监会/发改委): ×2
- Tier 3 (地方政府/行业协会): ×1

Keyword → sector rules map common policy language to A-share sectors.
"""

from __future__ import annotations

from beichen_alpha.models import FactorScore, MacroEvent, StockProfile
from beichen_alpha.profile_tags import profile_all_tags, profile_primary_industry

from .macro_event_factor import macro_time_decay


# ---------------------------------------------------------------------------
# Policy tier detection
# ---------------------------------------------------------------------------

# (keyword, tier_weight)
POLICY_TIER_KEYWORDS = (
    # Tier 1: Top-level (×3)
    ("国务院", 3),
    ("中央政治局", 3),
    ("中央经济工作会议", 3),
    ("中央财经委员会", 3),
    ("深改委", 3),
    ("全国人大", 3),
    # Tier 2: Ministry-level (×2)
    ("发改委", 2),
    ("证监会", 2),
    ("人民银行", 2),
    ("央行", 2),
    ("财政部", 2),
    ("工信部", 2),
    ("科技部", 2),
    ("商务部", 2),
    ("金融监管总局", 2),
    ("银保监会", 2),
    ("国家医保局", 2),
    ("药监局", 2),
    ("能源局", 2),
    ("上交所", 2),
    ("深交所", 2),
    # Tier 3: Local/industry (×1) — default if no match
)


# ---------------------------------------------------------------------------
# Policy keyword → A-share sector mapping
# ---------------------------------------------------------------------------

# (policy_keyword, [a_share_sectors], stance, weight)
# stance: +1 = positive, -1 = restrictive
POLICY_KEYWORD_MAP = (
    # Innovation / tech
    ("新质生产力", ("AI硬件", "半导体", "机器人", "先进制造", "高端制造", "数字经济"), 1, 8),
    ("人工智能", ("AI硬件", "AI", "算力", "CPO", "光模块", "数字经济"), 1, 8),
    ("自主可控", ("半导体", "国产替代", "芯片", "信创", "电子"), 1, 7),
    ("国产替代", ("半导体", "国产替代", "芯片", "信创", "电子"), 1, 7),
    ("数字经济", ("数字经济", "AI", "算力", "平台经济"), 1, 6),
    ("低空经济", ("低空经济", "无人机", "通用航空"), 1, 6),
    ("商业航天", ("军工", "航天", "卫星"), 1, 5),
    ("机器人", ("机器人", "人形机器人", "工业机器人", "先进制造"), 1, 7),
    ("量子", ("量子计算", "量子通信", "AI硬件"), 1, 5),

    # Manufacturing / equipment
    ("设备更新", ("先进制造", "高端制造", "工业母机", "工程机械"), 1, 7),
    ("以旧换新", ("家电", "汽车", "消费"), 1, 6),
    ("高端制造", ("先进制造", "高端制造", "工业母机"), 1, 6),
    ("专精特新", ("先进制造", "高端制造", "国产替代"), 1, 5),

    # Healthcare
    ("创新药", ("医药", "创新药", "医疗", "生物"), 1, 9),
    ("医药", ("医药", "创新药", "医疗", "生物", "CRO", "CXO"), 1, 7),
    ("医疗", ("医药", "医疗", "创新药"), 1, 6),
    ("医保", ("医药", "医疗"), -1, 6),  # 医保控费=利空
    ("集采", ("医药", "医疗"), -1, 8),  # 集采=利空

    # Energy / resources
    ("新能源", ("新能源", "光伏", "风电", "储能", "固态电池"), 1, 7),
    ("碳中和", ("新能源", "光伏", "风电", "储能", "电力"), 1, 6),
    ("能源安全", ("石油石化", "能源", "煤炭"), 1, 5),
    ("稀土", ("稀土", "工业金属", "有色"), 1, 6),

    # Circular economy / green transition
    ("循环经济", ("环保", "再生资源", "固废处理", "工业金属"), 1, 7),
    ("再生资源", ("环保", "再生资源", "工业金属"), 1, 6),
    ("固废", ("环保", "固废处理"), 1, 6),
    ("城市矿产", ("工业金属", "再生资源", "环保"), 1, 5),
    ("绿色建造", ("建筑", "建材", "工程机械"), 1, 5),
    ("再制造", ("先进制造", "工程机械", "汽车零部件"), 1, 5),
    ("新三样", ("新能源", "锂电池", "光伏", "环保"), 1, 6),
    ("清洁生产", ("环保", "新能源", "化工"), 1, 5),
    ("绿色设计", ("环保", "先进制造"), 1, 4),

    # Consumption
    ("扩内需", ("消费", "品牌消费", "家电", "汽车", "平台经济"), 1, 7),
    ("消费", ("消费", "品牌消费", "家电", "汽车", "白酒"), 1, 6),
    ("汽车", ("汽车", "新能源", "汽车零部件"), 1, 6),
    ("平台经济", ("平台经济", "互联网", "数字经济"), 1, 5),

    # Real estate / infra
    ("房地产", ("房地产", "建材", "家居", "银行"), 1, 5),
    ("城中村", ("房地产", "建材", "基建"), 1, 5),
    ("基建", ("基建", "建筑", "工程机械", "水泥"), 1, 5),

    # Finance
    ("降准", ("银行", "非银金融", "房地产"), 1, 6),
    ("降息", ("银行", "非银金融", "房地产", "高股息"), 1, 6),
    ("资本市场", ("非银金融", "券商", "银行"), 1, 5),
    ("稳定市场", ("非银金融", "券商"), 1, 4),

    # Defense
    ("军工", ("军工", "国防", "航天"), 1, 5),
    ("国防", ("军工", "国防", "航天"), 1, 5),

    # Regulation/risk (negative)
    ("反垄断", ("平台经济", "互联网"), -1, 6),
    ("监管", ("平台经济", "互联网", "金融"), -1, 5),
    ("整顿", ("平台经济", "互联网", "教育"), -1, 6),
    ("限制", ("平台经济", "互联网", "游戏"), -1, 5),
)


def score_policy_keywords(
    profile: StockProfile | None,
    events: list[MacroEvent] | None,
    as_of: datetime | None = None,
) -> list[FactorScore]:
    """Score policy events by keyword matching against stock sectors.

    Enhances the base macro event scoring with:
    1. Policy source tier weighting (国务院 > 部委 > 地方)
    2. Keyword → sector impact mapping
    3. Keyword density scoring within event descriptions

    Args:
        profile: Stock profile with industry/themes.
        events: Macro events (from CSV, RSS, and policy pages).
        as_of: Reference timestamp for time decay.

    Returns:
        FactorScore list — independent of score_macro_events, can coexist.
    """
    if profile is None:
        return [FactorScore("政策关键词", 0, True, "缺少股票画像")]
    if not events:
        return [FactorScore("政策关键词", 0, True, "暂无政策事件")]

    stock_sectors = _collect_sectors(profile)
    if not stock_sectors:
        return [FactorScore("政策关键词", 0, True, "未匹配到行业")]

    score = 0
    matches: list[str] = []
    for event in events:
        # Time decay
        decay = macro_time_decay(event, as_of)
        if decay <= 0:
            continue

        # Policy tier weight
        tier = _detect_policy_tier(event)
        tier_weight = tier * 0.5  # scale tier effect

        # Search event text for policy keywords
        event_text = f"{event.title} {event.detail or ''}".lower()
        for policy_kw, sectors, stance, weight in POLICY_KEYWORD_MAP:
            if policy_kw not in event_text:
                continue

            # Check sector overlap
            overlap = any(
                s.lower() in stock_sector.lower()
                for s in sectors
                for stock_sector in stock_sectors
            )
            if not overlap:
                continue

            contribution = int(round(stance * weight * decay * tier_weight))
            if contribution == 0:
                continue
            score += contribution
            if len(matches) < 3:
                direction = "利好" if stance > 0 else "利空"
                matches.append(f"{direction}:{policy_kw}(T{tier})")

    score = max(min(score, 20), -16)
    if not matches:
        return [FactorScore("政策关键词", 0, True, "政策事件未匹配到关键词")]

    detail = "；".join(matches)
    passed = score >= 0
    return [FactorScore("政策关键词", score, passed, detail)]


def _collect_sectors(profile: StockProfile) -> set[str]:
    """Collect all sector identifiers for a stock."""
    sectors: set[str] = set()
    if profile.primary_industry:
        sectors.add(profile.primary_industry)
    if profile.industry:
        sectors.add(profile.industry)
    for tag in profile_all_tags(profile):
        sectors.add(tag)
    return sectors


def _detect_policy_tier(event: MacroEvent) -> int:
    """Detect policy tier from event title/source (3=top, 2=ministry, 1=local)."""
    text = f"{event.title} {event.source} {event.detail or ''}".lower()
    for keyword, tier in POLICY_TIER_KEYWORDS:
        if keyword in text:
            return tier
    return 1  # default tier
