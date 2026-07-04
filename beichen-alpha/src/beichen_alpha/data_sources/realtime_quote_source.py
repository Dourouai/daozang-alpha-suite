from __future__ import annotations

import urllib.request
from datetime import datetime
from typing import Iterable

from beichen_alpha.models import RealtimeQuote

from .akshare_source import normalize_symbol, stock_market_symbol


class TencentRealtimeQuoteSource:
    def __init__(self, symbols: Iterable[str], chunk_size: int = 60) -> None:
        self.symbols = [normalize_symbol(symbol) for symbol in symbols if symbol.strip()]
        self.chunk_size = chunk_size

    def load(self) -> dict[str, RealtimeQuote]:
        quotes: dict[str, RealtimeQuote] = {}
        for index in range(0, len(self.symbols), self.chunk_size):
            chunk = self.symbols[index : index + self.chunk_size]
            quotes.update(fetch_tencent_quotes(chunk))
        return quotes


def fetch_tencent_quotes(symbols: Iterable[str]) -> dict[str, RealtimeQuote]:
    market_symbols = [stock_market_symbol(symbol) for symbol in symbols]
    if not market_symbols:
        return {}
    url = "http://qt.gtimg.cn/q=" + ",".join(market_symbols)
    payload = urllib.request.urlopen(url, timeout=10).read().decode("gbk", errors="replace")
    quotes: dict[str, RealtimeQuote] = {}
    for record in payload.split(";"):
        quote = parse_tencent_quote(record)
        if quote is not None:
            quotes[quote.code] = quote
    return quotes


def parse_tencent_quote(record: str) -> RealtimeQuote | None:
    clean = record.strip()
    if not clean or '="' not in clean:
        return None
    _, raw = clean.split('="', 1)
    fields = raw.rstrip('"').split("~")
    if len(fields) < 35:
        return None
    code = normalize_symbol(fields[2])
    name = fields[1].strip()
    price = to_float(fields[3])
    if not code or not name or price <= 0:
        return None

    amount_wan = to_float(get_field(fields, 57)) or to_float(get_field(fields, 37))
    volume_hand = to_float(get_field(fields, 36))
    return RealtimeQuote(
        code=code,
        name=name,
        price=price,
        open=to_float(fields[5]),
        high=to_float(fields[33]),
        low=to_float(fields[34]),
        prev_close=to_float(fields[4]),
        change_pct=to_float(fields[32]),
        amount_billion=(amount_wan / 10000) if amount_wan > 0 else None,
        volume_hand=volume_hand if volume_hand > 0 else None,
        vwap_price=calc_vwap_price(amount_wan, volume_hand),
        quote_time=parse_quote_time(fields[30]),
    )


def get_field(fields: list[str], index: int) -> str:
    if index >= len(fields):
        return ""
    return fields[index]


def parse_quote_time(value: str) -> datetime | None:
    clean = value.strip()
    if not clean:
        return None
    try:
        return datetime.strptime(clean, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def calc_vwap_price(amount_wan: float, volume_hand: float) -> float | None:
    if amount_wan <= 0 or volume_hand <= 0:
        return None
    return round((amount_wan * 10000) / (volume_hand * 100), 2)


def to_float(value: str) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0
