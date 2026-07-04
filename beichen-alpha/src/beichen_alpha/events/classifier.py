from __future__ import annotations

from datetime import datetime, timedelta

from beichen_alpha.models import NewsEvent


POSITIVE_RULES: list[tuple[str, tuple[str, ...], float]] = [
    ("policy_positive", ("政策", "支持", "鼓励", "试点", "规划", "方案", "行动计划"), 0.7),
    ("industry_demand_up", ("需求", "景气", "订单", "合同", "中标", "涨价", "供需", "扩产"), 0.6),
    ("earnings_positive", ("预增", "扭亏", "增长", "创新高", "超预期", "利润增加"), 0.8),
    ("technology_upgrade", ("突破", "国产替代", "高端", "创新", "专利", "首台", "首套"), 0.6),
]

NEGATIVE_RULES: list[tuple[str, tuple[str, ...], float, bool]] = [
    ("regulatory_risk", ("立案", "调查", "处罚", "监管", "警示函", "违规", "问询函"), 0.9, True),
    ("earnings_warning", ("预亏", "亏损", "下滑", "下降", "不及预期", "业绩变脸"), 0.8, False),
    ("shareholder_reduce", ("减持", "清仓", "被动减持"), 0.7, False),
    ("litigation", ("诉讼", "仲裁", "冻结", "查封"), 0.8, True),
    ("debt_liquidity_risk", ("违约", "逾期", "债务", "流动性风险", "兑付"), 0.9, True),
    ("delisting_risk", ("退市", "ST", "*ST", "暂停上市"), 1.0, True),
]

DISCLOSURE_HARD_NEGATIVE_RULES: list[tuple[str, tuple[str, ...], float]] = [
    (
        "regulatory_penalty",
        ("立案", "行政处罚", "处罚事先告知", "纪律处分", "公开谴责", "监管措施", "警示函"),
        1.0,
    ),
    ("major_litigation", ("重大诉讼", "重大仲裁", "诉讼", "仲裁", "冻结", "查封"), 0.9),
    (
        "shareholder_reduce",
        (
            "控股股东拟减持",
            "实际控制人拟减持",
            "董监高拟减持",
            "清仓减持",
            "被动减持",
            "减持数量超过",
        ),
        0.9,
    ),
    ("delisting_risk", ("退市", "*ST", "ST", "风险警示", "终止上市"), 1.0),
]

DISCLOSURE_SOFT_NEGATIVE_RULES: list[tuple[str, tuple[str, ...], float]] = [
    ("shareholder_reduce", ("减持计划", "拟减持", "减持股份", "股份减持"), 0.72),
    ("earnings_revision", ("业绩预告修正", "业绩快报修正", "业绩下修"), 0.85),
    ("exchange_inquiry", ("问询函", "关注函", "监管工作函"), 0.65),
]

DISCLOSURE_POSITIVE_RULES: list[tuple[str, tuple[str, ...], float]] = [
    (
        "share_buyback",
        ("回购股份", "股份回购", "回购方案", "首次回购", "回购公司股份", "集中竞价方式回购", "集中竞价交易方式回购"),
        0.82,
    ),
    ("shareholder_increase", ("增持计划", "拟增持", "增持股份", "完成增持"), 0.72),
    ("major_contract", ("重大合同", "项目中标", "中标通知书", "签订合同", "订单"), 0.72),
    ("equity_incentive", ("股权激励", "员工持股计划", "限制性股票激励"), 0.62),
]

EARNINGS_CONTEXT = ("业绩", "利润", "净利润", "扣非", "亏损", "盈利")
EARNINGS_NEGATIVE = ("预亏", "亏损", "下降", "下滑", "减少", "大幅减少", "业绩预告修正")
EARNINGS_POSITIVE = ("预增", "扭亏", "增长", "增加", "大幅增加", "同比上升", "创新高")


def classify_news(
    code: str,
    title: str,
    source: str,
    url: str = "",
    published_at: datetime | None = None,
    content: str = "",
) -> NewsEvent:
    text = f"{title} {content}"

    for event_type, keywords, importance, hard_exclude in NEGATIVE_RULES:
        if any(keyword in text for keyword in keywords):
            return NewsEvent(
                code=code,
                title=title,
                source=source,
                url=url,
                published_at=published_at,
                event_type=event_type,
                polarity=-1,
                importance=importance,
                confidence=0.75,
                hard_exclude=hard_exclude,
            )

    for event_type, keywords, importance in POSITIVE_RULES:
        if any(keyword in text for keyword in keywords):
            return NewsEvent(
                code=code,
                title=title,
                source=source,
                url=url,
                published_at=published_at,
                event_type=event_type,
                polarity=1,
                importance=importance,
                confidence=0.65,
            )

    return NewsEvent(
        code=code,
        title=title,
        source=source,
        url=url,
        published_at=published_at,
        event_type="neutral",
        polarity=0,
        importance=0.2,
        confidence=0.5,
    )


def classify_disclosure(
    code: str,
    title: str,
    source: str = "巨潮公告",
    url: str = "",
    published_at: datetime | None = None,
    content: str = "",
) -> NewsEvent:
    text = f"{title} {content}"

    if has_earnings_context(text) and any(keyword in text for keyword in EARNINGS_NEGATIVE):
        return NewsEvent(
            code=code,
            title=title,
            source=source,
            url=url,
            published_at=published_at,
            event_type="earnings_warning",
            polarity=-1,
            importance=0.95,
            confidence=0.9,
            hard_exclude=True,
        )

    for event_type, keywords, importance in DISCLOSURE_HARD_NEGATIVE_RULES:
        if any(keyword in text for keyword in keywords):
            return NewsEvent(
                code=code,
                title=title,
                source=source,
                url=url,
                published_at=published_at,
                event_type=event_type,
                polarity=-1,
                importance=importance,
                confidence=0.9,
                hard_exclude=True,
            )

    for event_type, keywords, importance in DISCLOSURE_SOFT_NEGATIVE_RULES:
        if any(keyword in text for keyword in keywords):
            return NewsEvent(
                code=code,
                title=title,
                source=source,
                url=url,
                published_at=published_at,
                event_type=event_type,
                polarity=-1,
                importance=importance,
                confidence=0.8,
                hard_exclude=False,
            )

    if has_earnings_context(text) and any(keyword in text for keyword in EARNINGS_POSITIVE):
        return NewsEvent(
            code=code,
            title=title,
            source=source,
            url=url,
            published_at=published_at,
            event_type="earnings_positive",
            polarity=1,
            importance=0.75,
            confidence=0.85,
        )

    for event_type, keywords, importance in DISCLOSURE_POSITIVE_RULES:
        if any(keyword in text for keyword in keywords):
            return NewsEvent(
                code=code,
                title=title,
                source=source,
                url=url,
                published_at=published_at,
                event_type=event_type,
                polarity=1,
                importance=importance,
                confidence=0.82,
            )

    return NewsEvent(
        code=code,
        title=title,
        source=source,
        url=url,
        published_at=published_at,
        event_type="neutral",
        polarity=0,
        importance=0.2,
        confidence=0.6,
    )


def has_earnings_context(text: str) -> bool:
    return any(keyword in text for keyword in EARNINGS_CONTEXT)


def filter_events(events: list[NewsEvent], as_of: datetime, lookback_days: int) -> list[NewsEvent]:
    start = as_of - timedelta(days=lookback_days)
    filtered = []
    for event in events:
        if event.published_at is None:
            filtered.append(event)
            continue
        if start <= event.published_at <= as_of:
            filtered.append(event)
    return filtered
