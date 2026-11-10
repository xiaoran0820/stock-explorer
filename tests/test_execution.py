import unittest

from backtest_picker import BUY_COST, SELL_COST
from execution import buy_quantity, price_limit, sale_value, snapshot_blocks


class ExecutionTests(unittest.TestCase):
    def test_price_limit_by_board_and_date(self):
        cases = [("600036", "2024-01-02", 0.10), ("sz000001", "2024-01-02", 0.10),
                 ("300750", "2020-08-21", 0.10), ("sz300750", "2020-08-24", 0.20),
                 ("sh688981", "2020-01-02", 0.20), ("bj430047", "2024-01-02", 0.30),
                 ("830799", "2024-01-02", 0.30), ("920000", "2024-01-02", 0.30)]
        for code, day, expected in cases:
            with self.subTest(code=code, day=day):
                self.assertEqual(price_limit(code, day), expected)

    def test_snapshot_blocks_read_the_saved_limit_flags(self):
        stock = {"rows": {}, "limits": {"2024-01-03": [True, False], "2024-01-04": [False, True]}}
        self.assertEqual(snapshot_blocks(stock), {"2024-01-03": (True, False), "2024-01-04": (False, True)})
        # A snapshot cut before the flags existed has to be rebuilt, not guessed from adjusted prices.
        with self.assertRaises(ValueError):
            snapshot_blocks({"rows": {}})

    def test_costs_apply_once_each_way(self):
        quantity = buy_quantity(1.0, 10, 0.001)
        self.assertAlmostEqual(quantity, (1 - BUY_COST) / 10.01)
        self.assertAlmostEqual(sale_value(quantity, 10, 0.001), quantity * 9.99 * (1 - SELL_COST))


if __name__ == "__main__":
    unittest.main()
