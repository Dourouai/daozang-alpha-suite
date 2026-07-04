from __future__ import annotations

import math
import struct
from collections.abc import Iterable
from pathlib import Path

from beichen_alpha.models import Bar


class QlibBinPriceSource:
    """Read daily bars directly from a local Qlib binary provider directory."""

    def __init__(self, provider_uri: str | Path, codes: Iterable[str] | None = None) -> None:
        self.provider_uri = Path(provider_uri)
        self.codes = tuple(codes or ())

    def load(self) -> dict[str, list[Bar]]:
        return self.load_codes(self.codes)

    def load_codes(self, codes: Iterable[str]) -> dict[str, list[Bar]]:
        calendar = load_calendar(self.provider_uri)
        result: dict[str, list[Bar]] = {}
        for code in codes:
            normalized = normalize_qlib_symbol(code)
            bars = self.load_symbol(normalized, calendar)
            if bars:
                result[plain_stock_code(normalized)] = bars
        return result

    def load_symbol(self, qlib_symbol: str, calendar: list[str] | None = None) -> list[Bar]:
        active_calendar = calendar or load_calendar(self.provider_uri)
        feature_dir = self.provider_uri / "features" / qlib_symbol.lower()
        close_series = read_qlib_feature(feature_dir / "close.day.bin")
        if close_series is None:
            return []

        fields = {
            "open": read_qlib_feature(feature_dir / "open.day.bin"),
            "high": read_qlib_feature(feature_dir / "high.day.bin"),
            "low": read_qlib_feature(feature_dir / "low.day.bin"),
            "close": close_series,
            "volume": read_qlib_feature(feature_dir / "volume.day.bin"),
            "amount": read_qlib_feature(feature_dir / "amount.day.bin"),
        }
        start_index, closes = close_series
        bars: list[Bar] = []
        code = plain_stock_code(qlib_symbol)

        for offset, close in enumerate(closes):
            calendar_index = start_index + offset
            if calendar_index >= len(active_calendar) or not is_valid_number(close):
                continue
            open_price = feature_value(fields["open"], calendar_index, close)
            high = feature_value(fields["high"], calendar_index, close)
            low = feature_value(fields["low"], calendar_index, close)
            volume = feature_value(fields["volume"], calendar_index, 0.0)
            amount = feature_value(fields["amount"], calendar_index, 0.0)
            bars.append(
                Bar(
                    code=code,
                    name=code,
                    date=active_calendar[calendar_index],
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=int(volume) if is_valid_number(volume) else 0,
                    amount=amount if is_valid_number(amount) else 0.0,
                )
            )
        return bars


def load_calendar(provider_uri: str | Path) -> list[str]:
    calendar_path = Path(provider_uri) / "calendars" / "day.txt"
    if not calendar_path.exists():
        return []
    return [
        line.strip()
        for line in calendar_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_qlib_feature(path: str | Path) -> tuple[int, list[float]] | None:
    feature_path = Path(path)
    if not feature_path.exists():
        return None
    raw = feature_path.read_bytes()
    if len(raw) < 8 or len(raw) % 4 != 0:
        return None
    values = struct.unpack(f"<{len(raw) // 4}f", raw)
    return int(values[0]), list(values[1:])


def feature_value(series: tuple[int, list[float]] | None, calendar_index: int, default: float) -> float:
    if series is None:
        return default
    start_index, values = series
    offset = calendar_index - start_index
    if offset < 0 or offset >= len(values):
        return default
    value = values[offset]
    return value if is_valid_number(value) else default


def normalize_qlib_symbol(code: str) -> str:
    value = str(code).strip().lower()
    if value.startswith(("sh", "sz", "bj")) and len(value) >= 8:
        return value[:2] + value[-6:]
    digits = "".join(char for char in value if char.isdigit())
    if len(digits) < 6:
        raise ValueError(f"invalid stock code: {code}")
    plain = digits[-6:]
    if plain.startswith(("60", "68", "51", "56", "58")):
        return f"sh{plain}"
    if plain.startswith(("00", "12", "15", "16", "18", "20", "30")):
        return f"sz{plain}"
    if plain.startswith(("43", "83", "87", "88", "92")):
        return f"bj{plain}"
    return f"sh{plain}"


def plain_stock_code(qlib_symbol: str) -> str:
    value = str(qlib_symbol).strip().lower()
    return value[-6:]


def is_valid_number(value: float) -> bool:
    return math.isfinite(value) and value > 0
