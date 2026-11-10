import unittest

from backtest_picker import BUY_COST, SELL_COST
from brooks_backtest import stats
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

    def test_limit_up_open_cannot_be_bought_under_conservative_rules(self):
        rows = {"a": (10, 10, 10.2, 9.8), "b": (11, 10.8, 11, 10.5), "c": (10.8, 11, 11.2, 10.7)}
        args = (rows, {"a": Setup(10, 9)}, {}, "bc", {"b": "a", "c": "b"}, "fixed", 0)
        _, _, ideal = simulate(*args)
        _, _, careful = simulate(*args, {"b": (True, False)})
        self.assertEqual((ideal["open"], careful["open"]), (1, 0))

    def test_limit_down_close_delays_the_stop_under_conservative_rules(self):
        rows = {"a": (10, 10, 10.2, 9.8), "b": (10.1, 10.2, 10.3, 9.9),
                "c": (9.8, 9.18, 9.9, 9.18), "d": (9.0, 9.1, 9.3, 8.9)}
        args = (rows, {"a": Setup(10, 9.5)}, {}, "bcd", dict(zip("bcd", "abc")), "fixed", 0)
        _, ideal, _ = simulate(*args)
        _, careful, _ = simulate(*args, {"c": (False, True)})
        self.assertEqual([(t["exit"], t["reason"]) for t in ideal], [("c", "stop")])
        self.assertEqual([(t["exit"], t["reason"]) for t in careful], [("d", "stop")])
        self.assertAlmostEqual(careful[0]["return"], 9.0 / 10.1 * (1 - BUY_COST) * (1 - SELL_COST) - 1)

    def test_position_open_at_the_end_reports_unrealized_return(self):
        rows = {"a": (10, 10, 10.2, 9.8), "b": (10.1, 10.2, 10.3, 9.9), "c": (10.2, 9.8, 10.3, 9.7)}
        nav, trades, counts = simulate(rows, {"a": Setup(10, 9.5)}, {}, "bc", {"b": "a", "c": "b"}, "fixed", 0)
        report = stats(nav, trades, counts, 2)
        self.assertEqual((report["trades"], report["win_rate"], report["open"], report["open_losing"]),
                         (0, None, 1, 1))
        self.assertAlmostEqual(report["unrealized_return"], (1 - BUY_COST) / 10.1 * 9.8 - 1)
        self.assertAlmostEqual(report["realized_return"], 0)
