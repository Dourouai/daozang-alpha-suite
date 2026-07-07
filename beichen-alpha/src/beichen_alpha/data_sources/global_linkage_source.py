from __future__ import annotations

import csv
import io
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from beichen_alpha.models import GlobalIndicator, GlobalLinkageSnapshot


@dataclass(frozen=True)
class FredSeries:
    code: str
    name: str
    category: str
    unit: str


@dataclass(frozen=True)
class YahooTicker:
    symbol: str
    name: str
    category: str
    unit: str = ""


DEFAULT_FRED_SERIES = (
    FredSeries("DGS10", "美国10年期国债收益率", "利率", "%"),
    FredSeries("DGS2", "美国2年期国债收益率", "利率", "%"),
    FredSeries("DTWEXBGS", "美元广义指数", "美元", "index"),
    FredSeries("BAMLH0A0HYM2", "美国高收益债利差", "信用", "%"),
    FredSeries("NFCI", "美国金融条件指数", "金融条件", "index"),
)

DEFAULT_YAHOO_TICKERS = (
    YahooTicker("^GSPC", "标普500", "美股"),
    YahooTicker("^IXIC", "纳斯达克", "美股"),
    YahooTicker("^DJI", "道琼斯", "美股"),
    YahooTicker("^HSI", "恒生指数", "港股"),
    YahooTicker("^VIX", "VIX波动率", "风险偏好"),
    YahooTicker("USDCNH=X", "美元/离岸人民币", "汇率"),
    YahooTicker("GC=F", "COMEX黄金", "商品"),
    YahooTicker("CL=F", "WTI原油", "商品"),
    YahooTicker("HG=F", "COMEX铜", "商品"),
    YahooTicker("SOXX", "美国半导体ETF", "美股行业"),
    YahooTicker("SMH", "美国半导体ETF", "美股行业"),
    YahooTicker("XBI", "美国生物科技ETF", "美股行业"),
    YahooTicker("XLF", "美国金融ETF", "美股行业"),
    YahooTicker("XLE", "美国能源ETF", "美股行业"),
    YahooTicker("KWEB", "中概互联网ETF", "中国资产"),
    YahooTicker("FXI", "中国大盘ETF", "中国资产"),
    YahooTicker("MCHI", "中国市场ETF", "中国资产"),
    YahooTicker("ASHR", "A股ETF", "中国资产"),
)


class GlobalLinkageSource:
    """Load global linkage observation data without feeding it into trading scores."""

    def __init__(
        self,
        fred_series: Iterable[FredSeries] = DEFAULT_FRED_SERIES,
        yahoo_tickers: Iterable[YahooTicker] = DEFAULT_YAHOO_TICKERS,
        lookback_days: int = 20,
        timeout: float = 10.0,
    ) -> None:
        self.fred_series = tuple(fred_series)
        self.yahoo_tickers = tuple(yahoo_tickers)
        self.lookback_days = lookback_days
        self.timeout = timeout

    def load(self) -> GlobalLinkageSnapshot:
        indicators: list[GlobalIndicator] = []
        health: list[str] = []

        for series in self.fred_series:
            try:
                indicators.append(fetch_fred_indicator(series, timeout=self.timeout))
            except Exception as exc:
                health.append(f"FRED:{series.code} FAIL({type(exc).__name__})")

        try:
            indicators.extend(
                fetch_yahoo_indicators(
                    self.yahoo_tickers,
                    lookback_days=self.lookback_days,
                )
            )
        except Exception as exc:
            health.append(f"yfinance FAIL({type(exc).__name__})")

        return build_global_linkage_snapshot(indicators, health=health)


def fetch_fred_indicator(series: FredSeries, timeout: float = 10.0) -> GlobalIndicator:
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?" + urllib.parse.urlencode(
        {"id": series.code}
    )
    text = fetch_text(url, timeout=timeout)

    points = parse_fred_csv(text, series.code)
    latest_date, latest, previous = latest_pair(points)
    change = None if previous is None else latest - previous
    change_pct = pct_change(latest, previous)
    return GlobalIndicator(
        code=series.code,
        name=series.name,
        category=series.category,
        source="FRED",
        latest_date=latest_date,
        latest=latest,
        previous=previous,
        change=change,
        change_pct=change_pct,
        unit=series.unit,
        detail=format_change(latest, change, change_pct, series.unit),
    )


def fetch_text(url: str, timeout: float = 10.0) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "BeichenAlpha/0.1",
            "Accept": "text/csv,*/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception:
        return fetch_text_with_curl(url, timeout=timeout)


def fetch_text_with_curl(url: str, timeout: float = 10.0) -> str:
    result = subprocess.run(
        ["curl", "-L", "-sS", "--max-time", str(max(int(timeout), 1)), url],
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout


def fetch_yahoo_indicators(
    tickers: Iterable[YahooTicker],
    lookback_days: int = 20,
) -> list[GlobalIndicator]:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError(
            "yfinance is not installed. Install it with: python3 -m pip install yfinance"
        ) from exc

    indicators: list[GlobalIndicator] = []
    for ticker in tickers:
        frame = yf.Ticker(ticker.symbol).history(
            period=f"{lookback_days}d",
            interval="1d",
            auto_adjust=False,
        )
        indicator = normalize_yahoo_history(frame, ticker)
        if indicator is not None:
            indicators.append(indicator)
    return indicators


def normalize_yahoo_history(frame, ticker: YahooTicker) -> GlobalIndicator | None:
    if frame is None or frame.empty or "Close" not in frame:
        return None
    close = frame["Close"].dropna()
    if close.empty:
        return None
    latest = float(close.iloc[-1])
    previous = float(close.iloc[-2]) if len(close) >= 2 else None
    change = None if previous is None else latest - previous
    change_pct = pct_change(latest, previous)
    latest_index = close.index[-1]
    latest_date = latest_index.strftime("%Y-%m-%d") if hasattr(latest_index, "strftime") else str(latest_index)
    return GlobalIndicator(
        code=ticker.symbol,
        name=ticker.name,
        category=ticker.category,
        source="yfinance",
        latest_date=latest_date,
        latest=latest,
        previous=previous,
        change=change,
        change_pct=change_pct,
        unit=ticker.unit,
        detail=format_change(latest, change, change_pct, ticker.unit),
    )


def parse_fred_csv(text: str, series_code: str) -> list[tuple[str, float]]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    date_field = "observation_date" if "observation_date" in reader.fieldnames else "DATE"
    value_field = series_code if series_code in reader.fieldnames else reader.fieldnames[-1]
    points: list[tuple[str, float]] = []
    for row in reader:
        raw_date = str(row.get(date_field) or "").strip()
        raw_value = str(row.get(value_field) or "").strip()
        if not raw_date or raw_value in {"", "."}:
            continue
        try:
            points.append((raw_date, float(raw_value)))
        except ValueError:
            continue
    return points


def latest_pair(points: list[tuple[str, float]]) -> tuple[str, float, float | None]:
    if not points:
        raise ValueError("no numeric observations")
    latest_date, latest = points[-1]
    previous = points[-2][1] if len(points) >= 2 else None
    return latest_date, latest, previous


def build_global_linkage_snapshot(
    indicators: Iterable[GlobalIndicator],
    health: Iterable[str] = (),
    as_of: datetime | None = None,
) -> GlobalLinkageSnapshot:
    rows = tuple(indicators)
    score, signals = evaluate_global_posture(rows)
    posture = classify_global_posture(score)
    return GlobalLinkageSnapshot(
        as_of=as_of or datetime.now(),
        indicators=rows,
        posture=posture,
        score=score,
        signals=tuple(signals),
        source_health=tuple(health),
    )


def evaluate_global_posture(indicators: Iterable[GlobalIndicator]) -> tuple[int, list[str]]:
    by_code = {item.code: item for item in indicators}
    score = 0
    signals: list[str] = []

    us10y = by_code.get("DGS10")
    if us10y and us10y.change is not None:
        if us10y.change >= 0.08:
            score -= 8
            signals.append(f"美债10Y上行约 {us10y.change * 100:+.0f}bp")
        elif us10y.change <= -0.08:
            score += 4
            signals.append(f"美债10Y回落约 {us10y.change * 100:+.0f}bp")

    vix = by_code.get("^VIX")
    if vix:
        if vix.latest >= 25:
            score -= 18
            signals.append(f"VIX高位 {vix.latest:.1f}")
        elif vix.latest >= 20:
            score -= 10
            signals.append(f"VIX偏高 {vix.latest:.1f}")
        elif vix.latest < 15:
            score += 4
            signals.append(f"VIX低位 {vix.latest:.1f}")

    dollar = by_code.get("DTWEXBGS")
    if dollar and dollar.change_pct is not None:
        if dollar.change_pct >= 0.003:
            score -= 5
            signals.append(f"美元指数走强 {dollar.change_pct:+.2%}")
        elif dollar.change_pct <= -0.003:
            score += 3
            signals.append(f"美元指数走弱 {dollar.change_pct:+.2%}")

    usdcnh = by_code.get("USDCNH=X")
    if usdcnh and usdcnh.change_pct is not None:
        if usdcnh.change_pct >= 0.003:
            score -= 6
            signals.append(f"离岸人民币走弱 {usdcnh.change_pct:+.2%}")
        elif usdcnh.change_pct <= -0.003:
            score += 4
            signals.append(f"离岸人民币走强 {usdcnh.change_pct:+.2%}")

    nasdaq = by_code.get("^IXIC")
    if nasdaq and nasdaq.change_pct is not None:
        if nasdaq.change_pct <= -0.012:
            score -= 8
            signals.append(f"纳斯达克回撤 {nasdaq.change_pct:+.2%}")
        elif nasdaq.change_pct >= 0.012:
            score += 5
            signals.append(f"纳斯达克走强 {nasdaq.change_pct:+.2%}")

    hsi = by_code.get("^HSI")
    if hsi and hsi.change_pct is not None:
        if hsi.change_pct <= -0.012:
            score -= 6
            signals.append(f"恒生指数走弱 {hsi.change_pct:+.2%}")
        elif hsi.change_pct >= 0.012:
            score += 4
            signals.append(f"恒生指数走强 {hsi.change_pct:+.2%}")

    credit = by_code.get("BAMLH0A0HYM2")
    if credit and credit.change is not None:
        if credit.change >= 0.08:
            score -= 6
            signals.append(f"高收益债利差扩大 {credit.change:+.2f}")
        elif credit.change <= -0.08:
            score += 3
            signals.append(f"高收益债利差收窄 {credit.change:+.2f}")

    if not signals:
        signals.append("外部变量暂无强信号")
    return score, signals


def classify_global_posture(score: int) -> str:
    if score <= -25:
        return "外部风险偏高"
    if score <= -10:
        return "外部偏谨慎"
    if score >= 10:
        return "外部风险偏暖"
    return "外部中性"


def format_change(
    latest: float,
    change: float | None,
    change_pct: float | None,
    unit: str,
) -> str:
    latest_text = f"{latest:.2f}{unit}" if unit and unit != "index" else f"{latest:.2f}"
    if change is None:
        return latest_text
    if change_pct is None or unit == "%":
        return f"{latest_text} ({change:+.2f})"
    return f"{latest_text} ({change:+.2f}, {change_pct:+.2%})"


def pct_change(latest: float, previous: float | None) -> float | None:
    if previous is None or previous == 0:
        return None
    return latest / previous - 1


def resolve_fred_series(raw: str) -> tuple[FredSeries, ...]:
    if not raw.strip():
        return DEFAULT_FRED_SERIES
    defaults = {item.code: item for item in DEFAULT_FRED_SERIES}
    return tuple(
        defaults.get(code.strip(), FredSeries(code.strip(), code.strip(), "宏观", ""))
        for code in raw.split(",")
        if code.strip()
    )


def resolve_yahoo_tickers(raw: str) -> tuple[YahooTicker, ...]:
    if not raw.strip():
        return DEFAULT_YAHOO_TICKERS
    defaults = {item.symbol: item for item in DEFAULT_YAHOO_TICKERS}
    return tuple(
        defaults.get(symbol.strip(), YahooTicker(symbol.strip(), symbol.strip(), "全球市场"))
        for symbol in raw.split(",")
        if symbol.strip()
    )
