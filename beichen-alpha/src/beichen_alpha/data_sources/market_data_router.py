from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime
from typing import Iterable, Protocol

from beichen_alpha.models import RealtimeQuote

from .akshare_source import normalize_symbol
from .realtime_quote_source import TencentRealtimeQuoteSource


class RealtimeQuoteProvider(Protocol):
    name: str

    def load(self) -> dict[str, RealtimeQuote]:
        ...


class MarketDataRouter:
    def __init__(
        self,
        providers: Iterable[RealtimeQuoteProvider],
        stale_seconds: int = 20 * 60,
        max_source_diff_pct: float = 0.30,
    ) -> None:
        self.providers = list(providers)
        self.stale_seconds = stale_seconds
        self.max_source_diff_pct = max_source_diff_pct
        self.health: list[QuoteSourceHealth] = []

    def load(self) -> dict[str, RealtimeQuote]:
        loaded: list[tuple[RealtimeQuoteProvider, dict[str, RealtimeQuote], float]] = []
        self.health = []
        for provider in self.providers:
            start = time.perf_counter()
            try:
                quotes = provider.load()
            except Exception as exc:
                latency_ms = (time.perf_counter() - start) * 1000
                self.health.append(
                    QuoteSourceHealth(
                        source=provider.name,
                        ok=False,
                        latency_ms=latency_ms,
                        count=0,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue
            latency_ms = (time.perf_counter() - start) * 1000
            self.health.append(
                QuoteSourceHealth(
                    source=provider.name,
                    ok=True,
                    latency_ms=latency_ms,
                    count=len(quotes),
                )
            )
            loaded.append((provider, quotes, latency_ms))

        if not loaded:
            return {}

        symbols = sorted({symbol for _, quotes, _ in loaded for symbol in quotes})
        routed: dict[str, RealtimeQuote] = {}
        now = datetime.now()
        for symbol in symbols:
            candidates = [
                (provider, quotes[symbol], latency_ms)
                for provider, quotes, latency_ms in loaded
                if symbol in quotes
            ]
            primary_provider, primary_quote, primary_latency = candidates[0]
            source_diff_pct = calc_max_source_diff_pct([quote for _, quote, _ in candidates])
            stale = is_stale(primary_quote.quote_time, now, self.stale_seconds)
            warning = build_warning(source_diff_pct, self.max_source_diff_pct, stale, candidates)
            routed[symbol] = replace(
                primary_quote,
                source=primary_provider.name,
                latency_ms=primary_latency,
                stale=stale,
                source_diff_pct=source_diff_pct,
                warning=warning,
            )
        return routed


class DefaultMarketDataRouter(MarketDataRouter):
    def __init__(self, symbols: Iterable[str]) -> None:
        normalized = [normalize_symbol(symbol) for symbol in symbols if symbol.strip()]
        providers: list[RealtimeQuoteProvider] = [
            NamedQuoteProvider("tencent", TencentRealtimeQuoteSource(normalized)),
            AkshareRealtimeQuoteSource(normalized),
            EfinanceRealtimeQuoteSource(normalized),
            EasyQuotationRealtimeQuoteSource(normalized),
        ]
        super().__init__(providers)


class NamedQuoteProvider:
    def __init__(self, name: str, provider: RealtimeQuoteProvider) -> None:
        self.name = name
        self.provider = provider

    def load(self) -> dict[str, RealtimeQuote]:
        return self.provider.load()


class AkshareRealtimeQuoteSource:
    name = "akshare"

    def __init__(self, symbols: Iterable[str]) -> None:
        self.symbols = {normalize_symbol(symbol) for symbol in symbols if symbol.strip()}

    def load(self) -> dict[str, RealtimeQuote]:
        import akshare as ak

        frame = ak.stock_zh_a_spot_em()
        quotes: dict[str, RealtimeQuote] = {}
        for record in frame.to_dict(orient="records"):
            code = normalize_symbol(str(get_first(record, "代码", "code") or ""))
            if code not in self.symbols:
                continue
            price = to_float(get_first(record, "最新价", "最新", "price"))
            if price <= 0:
                continue
            amount = to_float(get_first(record, "成交额", "amount"))
            volume = to_float(get_first(record, "成交量", "volume"))
            quotes[code] = RealtimeQuote(
                code=code,
                name=str(get_first(record, "名称", "name") or code),
                price=price,
                open=to_float(get_first(record, "今开", "开盘", "open")),
                high=to_float(get_first(record, "最高", "high")),
                low=to_float(get_first(record, "最低", "low")),
                prev_close=to_float(get_first(record, "昨收", "prev_close")),
                change_pct=to_float(get_first(record, "涨跌幅", "change_pct")),
                amount_billion=amount / 100_000_000 if amount > 0 else None,
                volume_hand=volume / 100 if volume > 0 else None,
                quote_time=datetime.now(),
                source=self.name,
            )
        return quotes


class EfinanceRealtimeQuoteSource:
    name = "efinance"

    def __init__(self, symbols: Iterable[str]) -> None:
        self.symbols = {normalize_symbol(symbol) for symbol in symbols if symbol.strip()}

    def load(self) -> dict[str, RealtimeQuote]:
        import efinance as ef

        frame = ef.stock.get_realtime_quotes()
        quotes: dict[str, RealtimeQuote] = {}
        for record in frame.to_dict(orient="records"):
            code = normalize_symbol(str(get_first(record, "股票代码", "代码", "code") or ""))
            if code not in self.symbols:
                continue
            price = to_float(get_first(record, "最新价", "最新", "price"))
            if price <= 0:
                continue
            amount = to_float(get_first(record, "成交额", "amount"))
            volume = to_float(get_first(record, "成交量", "volume"))
            quotes[code] = RealtimeQuote(
                code=code,
                name=str(get_first(record, "股票名称", "名称", "name") or code),
                price=price,
                open=to_float(get_first(record, "今开", "开盘", "open")),
                high=to_float(get_first(record, "最高", "high")),
                low=to_float(get_first(record, "最低", "low")),
                prev_close=to_float(get_first(record, "昨收", "prev_close")),
                change_pct=to_float(get_first(record, "涨跌幅", "change_pct")),
                amount_billion=amount / 100_000_000 if amount > 0 else None,
                volume_hand=volume / 100 if volume > 0 else None,
                quote_time=datetime.now(),
                source=self.name,
            )
        return quotes


class EasyQuotationRealtimeQuoteSource:
    name = "easyquotation"

    def __init__(self, symbols: Iterable[str]) -> None:
        self.symbols = [normalize_symbol(symbol) for symbol in symbols if symbol.strip()]

    def load(self) -> dict[str, RealtimeQuote]:
        import easyquotation

        quotation = easyquotation.use("tencent")
        records = quotation.stocks(self.symbols)
        quotes: dict[str, RealtimeQuote] = {}
        for raw_code, record in records.items():
            code = normalize_symbol(str(record.get("code") or raw_code))
            if code not in self.symbols:
                continue
            price = to_float(get_first(record, "now", "price", "最新价"))
            if price <= 0:
                continue
            quotes[code] = RealtimeQuote(
                code=code,
                name=str(get_first(record, "name", "名称") or code),
                price=price,
                open=to_float(get_first(record, "open", "今开", "开盘")),
                high=to_float(get_first(record, "high", "最高")),
                low=to_float(get_first(record, "low", "最低")),
                prev_close=to_float(get_first(record, "close", "昨收")),
                change_pct=to_float(get_first(record, "涨跌幅", "change_pct")),
                amount_billion=None,
                volume_hand=to_float(get_first(record, "volume", "成交量")) or None,
                quote_time=datetime.now(),
                source=self.name,
            )
        return quotes


class QuoteSourceHealth:
    def __init__(
        self,
        source: str,
        ok: bool,
        latency_ms: float,
        count: int,
        error: str = "",
    ) -> None:
        self.source = source
        self.ok = ok
        self.latency_ms = latency_ms
        self.count = count
        self.error = error


def calc_max_source_diff_pct(quotes: list[RealtimeQuote]) -> float | None:
    prices = [quote.price for quote in quotes if quote.price > 0]
    if len(prices) < 2:
        return None
    low = min(prices)
    high = max(prices)
    if low <= 0:
        return None
    return (high / low - 1) * 100


def is_stale(quote_time: datetime | None, now: datetime, stale_seconds: int) -> bool:
    if quote_time is None:
        return False
    if quote_time.date() != now.date():
        return True
    return (now - quote_time).total_seconds() > stale_seconds


def build_warning(
    source_diff_pct: float | None,
    max_source_diff_pct: float,
    stale: bool,
    candidates: list[tuple[RealtimeQuoteProvider, RealtimeQuote, float]],
) -> str:
    warnings = []
    if stale:
        warnings.append("行情时间可能过期")
    if source_diff_pct is not None and source_diff_pct > max_source_diff_pct:
        warnings.append(f"多源价格差 {source_diff_pct:.2f}%")
    if len(candidates) == 1:
        warnings.append("仅单一行情源可用")
    return "；".join(warnings)


def get_first(record: dict, *names: str):
    for name in names:
        if name in record:
            return record[name]
    return None


def to_float(value) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
