from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from beichen_alpha.models import Bar

from .akshare_source import index_name, normalize_symbol


class BaostockPriceSource:
    """Load A-share daily bars through BaoStock as a historical-data fallback."""

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
        self.start_date = baostock_date(start_date or default_start_date())
        self.end_date = baostock_date(end_date or date.today().strftime("%Y%m%d"))
        self.adjust = adjust

    def load(self) -> dict[str, list[Bar]]:
        bs = import_baostock()
        login_result = bs.login()
        if getattr(login_result, "error_code", "0") != "0":
            raise RuntimeError(f"BaoStock login failed: {getattr(login_result, 'error_msg', '')}")

        try:
            price_map: dict[str, list[Bar]] = {}
            price_map[self.benchmark] = fetch_baostock_bars(
                bs,
                self.benchmark,
                self.start_date,
                self.end_date,
                self.adjust,
                fallback_name=index_name(self.benchmark),
                is_index=True,
            )
            for symbol in self.symbols:
                price_map[symbol] = fetch_baostock_bars(
                    bs,
                    symbol,
                    self.start_date,
                    self.end_date,
                    self.adjust,
                    fallback_name=symbol,
                )
            return price_map
        finally:
            bs.logout()


def import_baostock():
    try:
        import baostock as bs
    except ImportError as exc:
        raise RuntimeError(
            "BaoStock is not installed. Install it with: python3 -m pip install baostock pandas"
        ) from exc
    return bs


def fetch_baostock_bars(
    bs,
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str,
    fallback_name: str,
    is_index: bool = False,
) -> list[Bar]:
    query = bs.query_history_k_data_plus(
        baostock_symbol(symbol, is_index=is_index),
        "date,code,open,high,low,close,volume,amount",
        start_date=start_date,
        end_date=end_date,
        frequency="d",
        adjustflag=baostock_adjustflag(adjust),
    )
    if getattr(query, "error_code", "0") != "0":
        raise RuntimeError(
            f"BaoStock query failed for {symbol}: {getattr(query, 'error_msg', '')}"
        )
    bars = normalize_baostock_rows(query, normalize_symbol(symbol), fallback_name)
    if not bars:
        raise ValueError(f"BaoStock returned no bars for {symbol}")
    return bars


def normalize_baostock_rows(query, code: str, fallback_name: str) -> list[Bar]:
    rows: list[Bar] = []
    fields = list(getattr(query, "fields", []))
    while query.next():
        raw = dict(zip(fields, query.get_row_data(), strict=False))
        rows.append(
            Bar(
                code=code,
                name=fallback_name,
                date=str(raw.get("date") or ""),
                open=to_float(raw.get("open")),
                high=to_float(raw.get("high")),
                low=to_float(raw.get("low")),
                close=to_float(raw.get("close")),
                volume=to_int(raw.get("volume")),
                amount=to_float(raw.get("amount")),
            )
        )
    return sorted([bar for bar in rows if bar.date and bar.close > 0], key=lambda item: item.date)


def baostock_symbol(symbol: str, is_index: bool = False) -> str:
    code = normalize_symbol(symbol)
    if is_index:
        market = "sz" if code.startswith("399") else "sh"
    else:
        market = "sh" if code.startswith(("5", "6", "9")) else "sz"
    return f"{market}.{code}"


def baostock_adjustflag(adjust: str) -> str:
    if adjust == "hfq":
        return "1"
    if adjust == "qfq":
        return "2"
    return "3"


def baostock_date(value: str) -> str:
    clean = str(value).strip()
    if len(clean) == 8 and clean.isdigit():
        return f"{clean[:4]}-{clean[4:6]}-{clean[6:8]}"
    return clean


def default_start_date() -> str:
    return (date.today() - timedelta(days=240)).strftime("%Y%m%d")


def to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def to_int(value) -> int:
    if value in (None, ""):
        return 0
    return int(float(value))
