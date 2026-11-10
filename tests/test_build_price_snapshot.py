import unittest

try:
    import numpy as np
except ModuleNotFoundError:
    raise unittest.SkipTest("the snapshot builder requires NumPy")

from build_price_snapshot import archive_code, snapshot
from market_pool_backtest import Stock


class SnapshotTests(unittest.TestCase):
    def test_archive_codes_carry_the_exchange(self):
        cases = {"600000": "sh600000", "000001": "sz000001", "300750": "sz300750",
                 "430047": "bj430047", "920002": "bj920002"}
        for code, expected in cases.items():
            with self.subTest(code=code):
                self.assertEqual(archive_code(code), expected)

    def test_rows_show_real_prices_and_limits_use_one_raw_tick(self):
        days = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
        raw = np.array([[10, 10, 10.2, 9.9],
                        [11, 11, 11, 10.8],          # opens at the 11.00 limit-up price: no buy at the open
                        [10.5, 9.9, 10.6, 9.9],      # closes at the 9.90 limit-down price: no sale
                        [10.87, 10.5, 10.9, 10.4]])  # two ticks under the 10.89 limit: still buyable
        archive = {"sh600000": Stock(raw * 2, np.ones(4), np.full(4, 2.0))}  # adjusted = raw * factor
        result = snapshot(days, archive, {"600000": "name"}, ["600000", "600001"])
        stock = result["stocks"]["600000"]
        self.assertEqual(stock["rows"]["2024-01-05"], [10.87, 10.5, 10.9, 10.4])
        self.assertEqual(stock["limits"], {"2024-01-03": [True, False], "2024-01-04": [False, True]})
        self.assertIn("error", result["stocks"]["600001"])  # not in the archive


if __name__ == "__main__":
    unittest.main()
