from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from beichen_alpha.distill import opinion_signal_from_dict
from beichen_alpha.models import NewsEvent, OpinionSignal, StockProfile
from beichen_alpha.profile_tags import profile_all_tags


NEGATIVE_THEMES = {"AI硬件", "存储", "算力", "半导体"}
POSITIVE_THEMES = {"非银金融"}
WATCH_ONLY_THEMES = {"创新药", "畜牧"}
EXCLUDED_PROFILE_THEMES = {"消费", "品牌消费"}
SECTOR_BIAS_POSITIVE_THEMES = {"非银金融", "创新药"}
SECTOR_BIAS_NEGATIVE_THEMES = {"AI硬件", "存储", "算力", "半导体", "贵金属", "煤炭石化", "资源", "工业金属"}

THEME_PROFILE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "AI硬件": ("AI硬件", "GPU", "服务器", "数据中心", "光模块", "CPO", "液冷", "PCB"),
    "存储": ("存储", "HBM", "DRAM", "内存", "存储芯片"),
    "算力": ("算力", "云计算", "数据中心", "服务器"),
    "半导体": ("半导体", "芯片", "集成电路", "ASIC"),
    "非银金融": ("证券", "券商", "保险", "多元金融", "非银金融"),
    "创新药": ("创新药", "医药", "生物医药"),
    "畜牧": ("畜牧", "养殖", "生猪"),
    "贵金属": ("贵金属", "黄金", "白银"),
    "煤炭石化": ("煤炭", "石油", "石化"),
    "资源": ("资源", "煤炭", "石油", "石化", "有色", "黄金"),
    "工业金属": ("工业金属", "有色", "铜", "铝", "钼", "锂", "钴", "稀土"),
}


class OpinionSignalNewsSource:
    def __init__(
        self,
        symbols: Iterable[str],
        profiles: dict[str, StockProfile],
        path: str | Path,
        as_of: datetime,
        lookback_days: int = 7,
    ) -> None:
        self.symbols = [symbol.strip() for symbol in symbols if symbol.strip()]
        self.profiles = profiles
        self.path = Path(path)
        self.as_of = as_of
        self.lookback_days = lookback_days

    def load(self) -> dict[str, list[NewsEvent]]:
        result: dict[str, list[NewsEvent]] = {symbol: [] for symbol in self.symbols}
        if not self.path.exists():
            return result

        for signal in filter_recent_signals(load_opinion_signals(self.path), self.as_of, self.lookback_days):
            for symbol in self.symbols:
                event = signal_to_news_event(signal, symbol, self.profiles.get(symbol))
                if event is not None:
                    result[symbol].append(event)
        return result


def load_opinion_signals(path: str | Path) -> list[OpinionSignal]:
    signals: list[OpinionSignal] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            signals.append(opinion_signal_from_dict(json.loads(line)))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return signals


def filter_recent_signals(
    signals: list[OpinionSignal],
    as_of: datetime,
    lookback_days: int,
) -> list[OpinionSignal]:
    start = as_of - timedelta(days=lookback_days)
    return [signal for signal in signals if start <= signal.signal_date <= as_of]


def signal_to_news_event(
    signal: OpinionSignal,
    symbol: str,
    profile: StockProfile | None,
) -> NewsEvent | None:
    match_type = match_signal(signal, symbol, profile)
    if match_type == "negative":
        return build_event(signal, symbol, polarity=-1, event_type="opinion_risk", importance=0.5)
    if match_type == "positive":
        return build_event(signal, symbol, polarity=1, event_type="opinion_positive", importance=0.32)
    if match_type == "sector_negative":
        return build_event(signal, symbol, polarity=-1, event_type="opinion_sector_risk", importance=0.65)
    if match_type == "sector_positive":
        return build_event(signal, symbol, polarity=1, event_type="opinion_sector_positive", importance=0.55)
    return None


def match_signal(signal: OpinionSignal, symbol: str, profile: StockProfile | None) -> str:
    profile_tags = profile_all_tags(profile) if profile else ()
    if profile is None or EXCLUDED_PROFILE_THEMES.intersection(profile_tags):
        return ""

    matched_themes = {
        theme for theme in signal.themes if profile_matches_theme(profile, theme)
    }
    if matched_themes.intersection(NEGATIVE_THEMES) and signal.risk_flags:
        return "negative"
    if matched_themes.intersection(POSITIVE_THEMES) and "偏积极" in signal.stance:
        return "positive"
    if matched_themes.intersection(SECTOR_BIAS_NEGATIVE_THEMES) and is_negative_sector_stance(signal):
        return "sector_negative"
    if matched_themes.intersection(SECTOR_BIAS_POSITIVE_THEMES) and is_positive_sector_stance(signal):
        return "sector_positive"
    return ""


def profile_matches_theme(profile: StockProfile, theme: str) -> bool:
    keywords = THEME_PROFILE_KEYWORDS.get(theme, (theme,))
    profile_text = " ".join([profile.name, profile.industry, *profile_all_tags(profile)])
    return any(keyword and keyword in profile_text for keyword in keywords)


def is_positive_sector_stance(signal: OpinionSignal) -> bool:
    text = " ".join([signal.stance, signal.summary, *signal.key_points])
    return any(keyword in text for keyword in ("偏积极", "走强", "资金偏好", "观察方向", "机会"))


def is_negative_sector_stance(signal: OpinionSignal) -> bool:
    text = " ".join([signal.stance, signal.summary, *signal.key_points, *signal.risk_flags])
    return any(keyword in text for keyword in ("偏谨慎", "回调", "下跌", "风险", "崩盘", "利空", "拥挤"))


def build_event(
    signal: OpinionSignal,
    symbol: str,
    polarity: int,
    event_type: str,
    importance: float,
) -> NewsEvent:
    date_text = signal.signal_date.strftime("%Y-%m-%d")
    title = f"{signal.source_name}观点 {date_text}: {signal.stance}｜{signal.title}"
    return NewsEvent(
        code=symbol,
        title=title,
        source=f"个人观点源:{signal.source_name}",
        url=signal.url,
        published_at=signal.signal_date,
        event_type=event_type,
        polarity=polarity,
        importance=importance,
        confidence=min(signal.confidence, 0.8),
        hard_exclude=False,
    )
