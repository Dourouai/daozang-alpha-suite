from __future__ import annotations

import contextlib
import io
import urllib.request
from datetime import date, timedelta
from typing import Callable, Iterable

from beichen_alpha.models import Bar


class AksharePriceSource:
    """Load A-share daily bars through AKShare.

    The default implementation uses AKShare Tencent historical endpoints,
    because they are simpler and currently more reachable than the Eastmoney
    endpoints used by some AKShare helpers.
    """

    def __init__(
        self,
        symbols: Iterable[str],
        benchmark: str = "000300",
        start_date: str | None = None,
        end_date: str | None = None,
        adjust: str = "qfq",
    ) -> None:
        self.symbols = [normalize_symbol(symbol) for symbol in symbols if symbol.strip()]
        self.benchmark = normalize_symbol(benchmark)
        self.start_date = start_date or default_start_date()
        self.end_date = end_date or date.today().strftime("%Y%m%d")
        self.adjust = adjust

    def load(self) -> dict[str, list[Bar]]:
        ak = import_akshare()
        price_map: dict[str, list[Bar]] = {}

        try:
            benchmark_bars = fetch_index_bars(ak, self.benchmark, self.start_date, self.end_date)
            price_map[self.benchmark] = benchmark_bars

            names = fetch_tencent_names(self.symbols)
            for symbol in self.symbols:
                bars = fetch_stock_bars(
                    ak,
                    symbol,
                    self.start_date,
                    self.end_date,
                    self.adjust,
                    names.get(symbol, symbol),
                )
                price_map[symbol] = bars
        except Exception as exc:
            raise RuntimeError(
                "AKShare request failed. Check network access and AKShare upstream availability. "
                f"Error type: {type(exc).__name__}"
            ) from exc

        return price_map


def import_akshare():
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError(
            "AKShare is not installed. Install it with: python3 -m pip install akshare pandas"
        ) from exc
    return ak


def fetch_stock_bars(
    ak,
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str,
    name: str,
) -> list[Bar]:
    market_symbol = stock_market_symbol(symbol)
    frame = quiet_call(
        ak.stock_zh_a_hist_tx,
        symbol=market_symbol,
        start_date=start_date,
        end_date=end_date,
        adjust=adjust,
        timeout=10,
    )
    bars = normalize_tx_frame(frame, code=symbol, fallback_name=name)
    if not bars:
        raise ValueError(f"AKShare returned no stock bars for {symbol}")
    return bars


def fetch_index_bars(ak, symbol: str, start_date: str, end_date: str) -> list[Bar]:
    market_symbol = index_market_symbol(symbol)
    frame = quiet_call(
        ak.stock_zh_index_daily_tx,
        symbol=market_symbol,
        start_date=start_date,
        end_date=end_date,
    )
    bars = normalize_tx_frame(frame, code=symbol, fallback_name=index_name(symbol))
    if not bars:
        raise ValueError(f"AKShare returned no index bars for {symbol}")
    return bars


def quiet_call(func: Callable, **kwargs):
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        return func(**kwargs)


def fetch_tencent_names(symbols: Iterable[str]) -> dict[str, str]:
    market_symbols = [stock_market_symbol(symbol) for symbol in symbols]
    if not market_symbols:
        return {}

    url = "http://qt.gtimg.cn/q=" + ",".join(market_symbols)
    try:
        payload = urllib.request.urlopen(url, timeout=10).read().decode("gbk", errors="replace")
    except OSError:
        return {}

    names: dict[str, str] = {}
    for record in payload.split(";"):
        record = record.strip()
        if not record or '="' not in record:
            continue
        _, raw = record.split('="', 1)
        fields = raw.rstrip('"').split("~")
        if len(fields) >= 3 and fields[1] and fields[2]:
            names[normalize_symbol(fields[2])] = fields[1]
    return names


def normalize_tx_frame(frame, code: str, fallback_name: str) -> list[Bar]:
    rows = []
    for record in frame.to_dict(orient="records"):
        close = to_float(record.get("close"))
        volume = to_int(record.get("amount"))
        rows.append(
            Bar(
                code=code,
                name=fallback_name,
                date=normalize_date(record.get("date")),
                open=to_float(record.get("open")),
                high=to_float(record.get("high")),
                low=to_float(record.get("low")),
                close=close,
                volume=volume,
                amount=volume * close * 100,
            )
        )
    return sorted(rows, key=lambda item: item.date)


def normalize_symbol(symbol: str) -> str:
    value = symbol.strip().lower()
    for suffix in (".ss", ".sh", ".sz"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    for prefix in ("sh", "sz"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    return value


def stock_market_symbol(symbol: str) -> str:
    code = normalize_symbol(symbol)
    market = "sh" if code.startswith(("5", "6", "9")) else "sz"
    return f"{market}{code}"


def index_market_symbol(symbol: str) -> str:
    code = normalize_symbol(symbol)
    market = "sz" if code.startswith("399") else "sh"
    return f"{market}{code}"


def normalize_date(value) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)


def to_float(value) -> float:
    if value is None:
        return 0.0
    return float(value)


def to_int(value) -> int:
    if value is None:
        return 0
    return int(float(value))


def default_start_date() -> str:
    return (date.today() - timedelta(days=240)).strftime("%Y%m%d")


def index_name(symbol: str) -> str:
    return {
        "000300": "沪深300",
        "000001": "上证指数",
        "399001": "深证成指",
        "399006": "创业板指",
    }.get(symbol, symbol)
