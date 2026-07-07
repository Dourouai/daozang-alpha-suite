"""Global linkage factor: 美股→A股行业映射.

Maps overnight US sector/index moves to A-share sector scores.
US markets close after A-shares, providing a leading signal for next-day trading.

Key mappings:
- NASDAQ/SOX (费城半导体) → A-share 半导体、AI硬件
- XBI (美国生科) → A-share 医药、创新药
- XLF (美国金融) → A-share 银行、非银金融
- VIX (波动率) → overall risk appetite for all sectors
- USD/CNH → 出口链、航空
- US Treasury yields → 高股息/红利策略
"""

from __future__ import annotations

from datetime import date, datetime

from beichen_alpha.models import FactorScore, GlobalLinkageSnapshot, StockProfile
from beichen_alpha.profile_tags import profile_all_tags, profile_primary_industry


# ---------------------------------------------------------------------------
# US → A-share sector mapping
# ---------------------------------------------------------------------------

# Each entry: (us_indicator_name, a_share_sectors, direction, weight)
# direction: 1 = positive correlation, -1 = inverse correlation
US_SECTOR_MAP = (
    # (US indicator keyword, [A-share sector keywords], direction, base_score, label)
    ("纳斯达克", ("半导体", "AI硬件", "AI", "算力", "CPO", "光模块", "电子", "数字经济"), 1, 10, "美股科技"),
    ("费城", ("半导体", "AI硬件", "芯片", "电子"), 1, 12, "美股半导体"),
    ("生科", ("医药", "创新药", "医疗", "生物", "CRO", "CXO"), 1, 10, "美股生科→创新药"),
    ("生物科技", ("医药", "创新药", "医疗", "生物", "CRO", "CXO"), 1, 10, "美股生物科技→创新药"),
    ("金融", ("银行", "非银金融", "保险", "券商"), 1, 6, "美股金融→A股金融"),
    ("能源", ("石油石化", "能源", "煤炭", "油服"), 1, 6, "美股能源→A股能源"),
    ("黄金", ("黄金", "贵金属", "有色", "工业金属"), 1, 8, "金价→资源"),
    ("铜", ("有色", "工业金属", "资源", "材料资源"), 1, 6, "铜价→有色"),
    ("原油", ("石油石化", "能源", "化工"), 1, 6, "油价→能源"),
    ("中国", ("港股映射", "中概", "中国资产", "互联网", "平台经济"), 1, 5, "中国资产ETF"),
    ("人民币", ("出口链", "航空", "纺织"), -1, 6, "人民币升值→出口压力"),  # inverse
    ("人民币", ("进口", "造纸", "化工下游"), 1, 4, "人民币升值→进口成本降"),
    ("VIX", ("高股息", "防御", "红利", "公用事业"), 1, 8, "VIX升→避险"),
    ("国债", ("高股息", "红利", "防御", "银行"), 1, 6, "美债利率↓→红利"),
    ("国债", ("科技", "成长", "AI", "半导体"), -1, 6, "美债利率↑→成长承压"),  # inverse
)


def score_global_linkage(
    profile: StockProfile | None,
    snapshot: GlobalLinkageSnapshot | None,
    as_of: datetime | None = None,
) -> list[FactorScore]:
    """Score a stock based on overnight US market moves mapped to its sector.

    Args:
        profile: Stock profile with industry/themes.
        snapshot: Global linkage snapshot from yfinance/FRED.
        as_of: Reference timestamp (for freshness).

    Returns:
        List of FactorScore — positive if US sector moves favor this stock's sector.
    """
    if snapshot is None:
        return [FactorScore("全球联动", 0, True, "全球联动数据不可用，按中性处理")]
    if profile is None:
        return [FactorScore("全球联动", 0, True, "缺少股票画像，按中性处理")]

    # Collect stock's sector identifiers
    stock_sectors: set[str] = set()
    if profile.primary_industry:
        stock_sectors.add(profile.primary_industry)
    if profile.industry:
        stock_sectors.add(profile.industry)
    for tag in profile_all_tags(profile):
        stock_sectors.add(tag)

    if not stock_sectors:
        return [FactorScore("全球联动", 0, True, "未匹配到行业")]

    # Collect US indicator moves from snapshot
    us_moves = _extract_us_moves(snapshot)
    if not us_moves:
        return [FactorScore("全球联动", 0, True, "暂无美股变动数据")]

    score = 0
    matches: list[str] = []
    for us_key, a_sectors, direction, base, label in US_SECTOR_MAP:
        # Find matching US indicator
        us_change = _find_us_change(us_key, us_moves)
        if us_change is None or abs(us_change) < 0.003:  # ignore <0.3% move
            continue

        # Check if stock matches any A-share sector
        sector_match = any(
            a_sector.lower() in stock_sector.lower()
            for a_sector in a_sectors
            for stock_sector in stock_sectors
        )
        if not sector_match:
            continue

        # Score: direction * normalized change * base weight
        normalized = min(abs(us_change) * 100, 5.0)  # cap at 5% equivalent
        contribution = int(round(direction * normalized * base * 0.4))
        if contribution != 0:
            score += contribution
            arrow = "↑" if us_change > 0 else "↓"
            matches.append(f"{label}{arrow}{abs(us_change):.1%}")

    score = max(min(score, 18), -12)
    if not matches:
        return [FactorScore("全球联动", 0, True, "美股变动未映射到该行业")]

    detail = "；".join(matches[:3])
    passed = score >= 0
    return [FactorScore("全球联动", score, passed, detail)]


def _extract_us_moves(snapshot: GlobalLinkageSnapshot) -> dict[str, float]:
    """Extract US index/sector % changes from a GlobalLinkageSnapshot."""
    moves: dict[str, float] = {}
    for indicator in snapshot.indicators:
        name_lower = indicator.name.lower()
        change = indicator.change_pct
        if change is None:
            continue
        # Normalize: if value is >1, it's likely basis points not percent
        pct = change / 100 if abs(change) > 1 else change
        moves[name_lower] = pct
        # Also store by code
        moves[indicator.code.lower()] = pct
    return moves


def _find_us_change(us_key: str, us_moves: dict[str, float]) -> float | None:
    """Find the percent change of a US indicator by keyword matching."""
    us_lower = us_key.lower()
    for name, change in us_moves.items():
        if us_lower in name:
            return change
    # Try broader matching
    for name, change in us_moves.items():
        if any(word in name for word in us_lower.split()):
            return change
    return None
