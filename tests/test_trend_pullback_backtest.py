import unittest

from backtest_picker import BUY_COST, SELL_COST
from trend_pullback_backtest import Setup, scan, simulate


class TrendPullbackBacktestTests(unittest.TestCase):
    def test_breakout_is_known_at_close(self):
        rows = {f"{i:03d}": (100 + i, 101 + i, 101.5 + i, 99 + i) for i in range(122)}
        setups, stops = scan(rows)
        self.assertEqual(setups["breakout"]["119"], Setup(220, 209))
        prefix_setups, prefix_stops = scan({day: bar for day, bar in rows.items() if day <= "119"})
        self.assertEqual(prefix_setups["breakout"], {"119": Setup(220, 209)})
        self.assertEqual(prefix_stops["119"], stops["119"])

    def test_entry_day_target_cannot_be_sold_and_slippage_changes_only_cost(self):
        rows = {
            "b": (10, 12, 12, 9.5),
            "c": (11, 11, 11.5, 10.5),
        }
        outcomes = []
        for slip in (0, 0.0005):
            nav, trades, _ = simulate(rows, {"a": Setup(10, 9)}, {},
                                      "bc", {"b": "a", "c": "b"}, "fixed", slip)
            self.assertEqual([(trade["entry"], trade["exit"]) for trade in trades], [("b", "c")])
            self.assertAlmostEqual(nav[-1], 11 / 10 * (1 - BUY_COST) * (1 - SELL_COST)
                                   * (1 - slip) / (1 + slip))
            outcomes.append(trades[0]["return"])
        self.assertGreater(outcomes[0], outcomes[1])

    def test_partial_sale_and_runner_count_as_one_trade(self):
        rows = {
            "b": (10, 10.5, 11, 9.5),
            "c": (11, 11, 11.4, 10.3),
            "d": (10, 10.5, 10.6, 9.5),
        }
        _, trades, counts = simulate(rows, {"a": Setup(10, 9)}, {},
                                     "bcd", dict(zip("bcd", "abc")), "partial", 0)
        self.assertEqual(counts["partial_sales"], 1)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit"], "d")
        self.assertAlmostEqual(trades[0]["return"], 10.5 / 10 * (1 - BUY_COST) * (1 - SELL_COST) - 1)
