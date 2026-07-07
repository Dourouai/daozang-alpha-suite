import tempfile
import unittest
from pathlib import Path

from daozang_alpha.qlib_incremental import (
    DailyBar,
    append_calendar_dates,
    append_instrument_bars,
    read_bin_series,
    write_bin_series,
)


class QlibIncrementalTest(unittest.TestCase):
    def test_append_calendar_dates_dedupes_and_sorts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "day.txt"
            path.write_text("2026-07-02\n2026-07-03\n", encoding="utf-8")

            append_calendar_dates(path, ["2026-07-03", "2026-07-06"])

            self.assertEqual(path.read_text(encoding="utf-8").splitlines(), ["2026-07-02", "2026-07-03", "2026-07-06"])

    def test_append_instrument_bars_uses_existing_price_factor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            feature_dir = Path(tmpdir) / "features/sh600036"
            feature_dir.mkdir(parents=True)
            for field in ("open", "high", "low", "close", "volume", "amount", "factor", "vwap", "adjclose", "change"):
                write_bin_series(feature_dir / f"{field}.day.bin", 0, [10.0, 12.0])
            write_bin_series(feature_dir / "close.day.bin", 0, [10.0, 20.0])
            write_bin_series(feature_dir / "adjclose.day.bin", 0, [100.0, 200.0])
            calendar = ["2026-07-02", "2026-07-03"]
            bars = {
                "2026-07-03": DailyBar("2026-07-03", 9.0, 11.0, 8.0, 10.0, 1000.0),
                "2026-07-06": DailyBar("2026-07-06", 10.0, 12.0, 9.0, 11.0, 1200.0),
            }

            updated = append_instrument_bars(feature_dir, calendar, ("2026-07-06",), bars)

            self.assertTrue(updated)
            _, close_values = read_bin_series(feature_dir / "close.day.bin")
            _, high_values = read_bin_series(feature_dir / "high.day.bin")
            _, factor_values = read_bin_series(feature_dir / "factor.day.bin")
            _, amount_values = read_bin_series(feature_dir / "amount.day.bin")
            _, adjclose_values = read_bin_series(feature_dir / "adjclose.day.bin")
            _, change_values = read_bin_series(feature_dir / "change.day.bin")
            self.assertEqual(close_values[-1], 22.0)
            self.assertEqual(high_values[-1], 24.0)
            self.assertEqual(factor_values[-1], 2.0)
            self.assertEqual(amount_values[-1], 1320.0)
            self.assertEqual(adjclose_values[-1], 220.0)
            self.assertAlmostEqual(change_values[-1], 0.1, places=6)


if __name__ == "__main__":
    unittest.main()
