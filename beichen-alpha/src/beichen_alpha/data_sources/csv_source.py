from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from beichen_alpha.models import Bar


class CsvPriceSource:
    """Load normalized daily bars from a local CSV file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, list[Bar]]:
        return load_price_csv(self.path)


def load_price_csv(path: str | Path) -> dict[str, list[Bar]]:
    rows: dict[str, list[Bar]] = defaultdict(list)
    with Path(path).open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            bar = Bar(
                code=row["code"],
                name=row["name"],
                date=row["date"],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(float(row["volume"])),
                amount=float(row["amount"]),
            )
            rows[bar.code].append(bar)

    return {code: sorted(bars, key=lambda item: item.date) for code, bars in rows.items()}
