from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from beichen_alpha.models import ArticleContent, OpinionSignal, StockProfile
from beichen_alpha.profile_tags import profile_all_tags


RULE_VERSION = "opinion-rules-v1.2"
THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "AI硬件": ("AI硬件", "GPU", "服务器", "数据中心", "光模块", "CPO", "液冷"),
    "存储": ("存储", "HBM", "DRAM", "DDR4", "DDR5", "内存", "美光", "海力士"),
    "算力": ("算力", "云计算", "算力租赁"),
    "半导体": ("半导体", "芯片", "ASIC", "国产替代"),
    "非银金融": ("非银金融", "券商", "证券"),
    "宽基指数": ("宽基", "宽基指数", "指数", "ETF", "科创ETF"),
    "创新药": ("创新药", "医药"),
    "畜牧": ("畜牧", "养殖", "生猪"),
    "农林牧渔": ("农林牧渔", "农业", "牧渔"),
    "贵金属": ("贵金属", "黄金"),
    "煤炭石化": ("煤炭", "石化", "石油"),
    "工业金属": ("工业金属", "有色", "铜", "铝", "钼", "锂", "钴", "稀土"),
}

AI_HARDWARE_NEGATIVE = (
    "崩盘",
    "大跌",
    "跌超",
    "回调",
    "利空",
    "做空",
    "获利盘",
    "过剩担忧",
    "算力过剩",
    "资本开支见顶",
    "闲置算力",
    "降本增效",
    "挤泡沫",
    "过度拥挤",
    "估值阶段",
    "利润下降",
)
POSITIVE_FLOW = ("流入", "稳住", "行情", "走强")
MACRO_HAWKISH = ("偏鹰", "通胀", "2%")
EXCLUDED_PROFILE_THEMES = {"消费", "品牌消费"}


def distill_article(
    article: ArticleContent,
    profiles: dict[str, StockProfile] | None = None,
    ingested_at: datetime | None = None,
) -> OpinionSignal:
    text = article.text
    signal_text = f"{article.title}\n{text}"
    now = ingested_at or datetime.now()
    themes = detect_themes(signal_text)
    key_points: list[str] = []
    risk_flags: list[str] = []
    matched_rules: list[str] = []

    if has_any(signal_text, ("AI硬件", "存储", "算力", "芯片", "光模块", "半导体")) and has_any(
        signal_text, AI_HARDWARE_NEGATIVE
    ):
        key_points.append("AI硬件、存储、算力链条短线偏谨慎，核心压力来自获利盘和资本开支预期下修。")
        risk_flags.append("AI硬件拥挤交易风险")
        risk_flags.append("存储链周期估值风险")
        matched_rules.append("RISK_AI_HARDWARE_NEGATIVE: AI硬件/存储/算力/半导体 + 崩盘/大跌/利空/获利盘/资本开支见顶等")

    if has_any(signal_text, ("沃什", "美联储")) and has_any(signal_text, MACRO_HAWKISH):
        key_points.append("海外利率环境没有明确转向宽松，宏观层面对成长股估值仍有压制。")
        risk_flags.append("海外宏观偏鹰风险")
        matched_rules.append("RISK_MACRO_HAWKISH: 沃什/美联储 + 偏鹰/通胀/2%")

    if has_any(signal_text, ("美联储", "加息", "通胀", "美元利率")) and has_any(signal_text, ("加息预期", "抗通胀", "复杂")):
        key_points.append("海外美元利率和通胀预期仍会放大市场波动，需要降低收益预期。")
        risk_flags.append("美元利率波动风险")
        matched_rules.append("RISK_US_RATE_VOLATILITY: 美联储/加息/通胀/美元利率 + 加息预期/抗通胀/复杂")

    if has_any(signal_text, ("非银金融", "券商")) and has_any(signal_text, POSITIVE_FLOW):
        key_points.append("非银金融/券商有资金偏好线索，可作为顺周期方向观察。")
        matched_rules.append("POS_NON_BANK_FLOW: 非银金融/券商 + 流入/稳住/行情")

    if has_any(signal_text, ("农林牧渔", "农业", "牧渔")) and has_any(signal_text, POSITIVE_FLOW):
        key_points.append("农林牧渔出现走强线索，但需要受当前不碰消费的策略约束。")
        matched_rules.append("WATCH_AGRICULTURE_FLOW: 农林牧渔/农业/牧渔 + 流入/走强")

    if has_any(signal_text, ("工业金属", "有色", "铜", "铝")) and has_any(signal_text, POSITIVE_FLOW):
        key_points.append("工业金属/有色方向出现走强线索，需要结合美元、美债、商品价格和板块放量确认。")
        matched_rules.append("WATCH_INDUSTRIAL_METALS_FLOW: 工业金属/有色/铜/铝 + 流入/走强")

    if has_any(signal_text, ("贵金属", "黄金", "煤炭", "石化", "石油", "工业金属", "有色", "铜", "铝")) and has_any(signal_text, ("回调", "下跌", "受外围影响")):
        key_points.append("贵金属、煤炭石化、工业金属等资源方向存在外围冲击后的回调压力。")
        risk_flags.append("资源股外围回调风险")
        matched_rules.append("RISK_RESOURCE_PULLBACK: 贵金属/黄金/煤炭/石化/工业金属 + 回调/下跌/受外围影响")

    if has_any(signal_text, ("宽基", "宽基指数", "科创ETF", "ETF")) and has_any(signal_text, ("稳健", "激进", "分散", "少单押")):
        key_points.append("配置建议偏分散：稳健关注宽基指数，激进关注科创ETF，减少单一股票押注。")
        matched_rules.append("ALLOC_ETF_DIVERSIFY: 宽基/科创ETF/ETF + 稳健/激进/分散/少单押")

    if has_any(signal_text, THEME_KEYWORDS["创新药"]):
        key_points.append("创新药被提及为观察方向，但需要后续行情和公告确认。")
        matched_rules.append("WATCH_INNOVATIVE_DRUG: 创新药/医药")

    if has_any(signal_text, THEME_KEYWORDS["畜牧"]):
        key_points.append("畜牧被提及为观察方向；若沿用当前策略，需要继续受消费主题过滤约束。")
        matched_rules.append("WATCH_ANIMAL_HUSBANDRY: 畜牧/养殖/生猪")

    if not key_points:
        key_points.append("文章未提取出足够明确的可交易观点，先仅归档观察。")
        matched_rules.append("ARCHIVE_ONLY: 未命中明确方向规则")

    stance = build_stance(themes, key_points, risk_flags)
    confidence = calc_confidence(signal_text, key_points, risk_flags)
    symbols = map_symbols(signal_text, profiles or {})

    return OpinionSignal(
        source_name=article.source_name,
        source_author=article.author,
        title=article.title,
        url=article.url,
        signal_date=article.published_at or now,
        ingested_at=now,
        published_at=article.published_at,
        rule_version=RULE_VERSION,
        summary="；".join(key_points[:3]),
        stance=stance,
        confidence=confidence,
        themes=tuple(themes),
        symbols=tuple(symbols),
        risk_flags=tuple(dict.fromkeys(risk_flags)),
        key_points=tuple(key_points),
        matched_rules=tuple(matched_rules),
    )


def detect_themes(text: str) -> list[str]:
    themes = []
    for theme, keywords in THEME_KEYWORDS.items():
        if has_any(text, keywords):
            themes.append(theme)
    return themes


def build_stance(themes: list[str], key_points: list[str], risk_flags: list[str]) -> str:
    stance_parts = []
    if any(theme in themes for theme in ("AI硬件", "存储", "算力", "半导体")) and risk_flags:
        stance_parts.append("AI硬件/存储链偏谨慎")
    if "非银金融" in themes:
        stance_parts.append("非银金融偏积极观察")
    if "农林牧渔" in themes:
        stance_parts.append("农林牧渔观察")
    if "宽基指数" in themes:
        stance_parts.append("宽基/科创ETF分散配置")
    if any(theme in themes for theme in ("贵金属", "煤炭石化")):
        stance_parts.append("资源股偏谨慎")
    if "工业金属" in themes:
        stance_parts.append("工业金属观察")
    if "创新药" in themes:
        stance_parts.append("创新药观察")
    if "畜牧" in themes:
        stance_parts.append("畜牧观察")
    if stance_parts:
        return "；".join(stance_parts)
    return "仅归档观察"


def calc_confidence(text: str, key_points: list[str], risk_flags: list[str]) -> float:
    score = 0.5
    score += min(len(key_points) * 0.06, 0.18)
    score += min(len(risk_flags) * 0.04, 0.12)
    if len(text) >= 800:
        score += 0.05
    return round(min(score, 0.82), 2)


def map_symbols(text: str, profiles: dict[str, StockProfile]) -> list[str]:
    symbols = []
    for code, profile in profiles.items():
        if EXCLUDED_PROFILE_THEMES.intersection(profile_all_tags(profile)):
            continue
        terms = [code, profile.name]
        if any(term and term in text for term in terms):
            symbols.append(code)
    return symbols


def append_opinion_signal(signal: OpinionSignal, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as file:
        file.write(json.dumps(opinion_signal_to_dict(signal), ensure_ascii=False) + "\n")
    return target


def opinion_signal_to_dict(signal: OpinionSignal) -> dict:
    return {
        "source_name": signal.source_name,
        "source_author": signal.source_author,
        "title": signal.title,
        "url": signal.url,
        "signal_date": signal.signal_date.isoformat(timespec="seconds"),
        "ingested_at": signal.ingested_at.isoformat(timespec="seconds"),
        "published_at": signal.published_at.isoformat(timespec="seconds") if signal.published_at else None,
        "rule_version": signal.rule_version,
        "summary": signal.summary,
        "stance": signal.stance,
        "confidence": signal.confidence,
        "themes": list(signal.themes),
        "symbols": list(signal.symbols),
        "risk_flags": list(signal.risk_flags),
        "key_points": list(signal.key_points),
        "matched_rules": list(signal.matched_rules),
    }


def opinion_signal_from_dict(record: dict) -> OpinionSignal:
    ingested_at = parse_datetime(record.get("ingested_at")) or datetime.now()
    published_at = parse_datetime(record.get("published_at"))
    signal_date = parse_datetime(record.get("signal_date")) or published_at or ingested_at
    return OpinionSignal(
        source_name=str(record.get("source_name") or ""),
        source_author=str(record.get("source_author") or ""),
        title=str(record.get("title") or ""),
        url=str(record.get("url") or ""),
        signal_date=signal_date,
        ingested_at=ingested_at,
        published_at=published_at,
        rule_version=str(record.get("rule_version") or "opinion-rules-legacy"),
        summary=str(record.get("summary") or ""),
        stance=str(record.get("stance") or ""),
        confidence=float(record.get("confidence") or 0.0),
        themes=tuple(record.get("themes") or ()),
        symbols=tuple(record.get("symbols") or ()),
        risk_flags=tuple(record.get("risk_flags") or ()),
        key_points=tuple(record.get("key_points") or ()),
        matched_rules=tuple(record.get("matched_rules") or ("LEGACY_RECORD: 旧记录未保存命中规则",)),
    )


def parse_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)
