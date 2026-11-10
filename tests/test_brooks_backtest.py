import unittest

from backtest_picker import BUY_COST
from brooks_backtest import Order, buy_and_hold, exit_order, run, scan, simulate


class BrooksBacktestTests(unittest.TestCase):
    def test_weak_first_attempt_still_counts_and_h2_order_precedes_trigger(self):
        ranges = [
            (100, 95), (102, 97), (104, 100), (105, 101), (103, 99), (101, 96),
            (102, 98), (103, 99), (104, 100), (106, 103), (104, 102), (103, 101),
            (102, 99), (103, 100), (104, 101), (105, 102), (105, 103), (104, 102),
            (103, 101), (105, 102), (110, 105), (112, 108), (109, 105), (110, 106),
            (108, 104), (109, 105),
        ]
        rows = {f"{i:02d}": (low, (high + low) / 2, high, low)
                for i, (high, low) in enumerate(ranges)}
        rows["20"] = (105, 109.8, 110, 105)
        rows["21"] = (108, 111.5, 112, 108)
        rows["24"] = (105, 107.75, 108, 104)
        orders, lows = scan(rows)
        self.assertEqual(orders, {"24": Order(108, 104, 112, True)})
        self.assertEqual(scan({day: bar for day, bar in rows.items() if day < "25"})[0], orders)
        self.assertEqual(lows["20"], ("18", 101))
        rows["21"] = (108, 109, 112, 108)
        self.assertEqual(scan(rows)[0], {})

    def test_entry_stop_breach_waits_for_next_executable_open(self):
        rows = {
            "b": (9.5, 8.5, 12.5, 8),
            "c": (8, 8, 8, 8),
            "d": (8.1, 8.5, 9, 8),
        }
        _, trades, counts = simulate(
            rows, {"a": Order(10, 9, 12, True)}, {},
            "bcd", dict(zip("bcd", "abc")), strong=True, trail=False, slip=0)
        self.assertEqual(counts["entries"], 1)
        self.assertEqual((trades[0]["entry"], trades[0]["exit"]), ("b", "d"))
        self.assertEqual(trades[0]["entry_price"], 10)
        self.assertEqual(trades[0]["exit_price"], 8.1)
        self.assertEqual(trades[0]["reason"], "stop")

    def test_gap_past_target_skips_entry_and_order_does_not_carry(self):
        rows = {"b": (13, 12, 13, 11), "c": (10, 11, 11, 10)}
        _, trades, counts = simulate(
            rows, {"a": Order(10, 9, 12, True)}, {},
            "bc", {"b": "a", "c": "b"}, strong=True, trail=False, slip=0)
        self.assertEqual(trades, [])
        self.assertEqual(counts["entries"], 0)

    def test_open_resolves_exit_otherwise_stop_takes_precedence(self):
        self.assertEqual(exit_order((10, 11, 13, 8), 9, 12), (9, "stop"))
        self.assertEqual(exit_order((13, 11, 13, 8), 9, 12), (13, "target"))
        self.assertEqual(exit_order((8, 11, 13, 8), 9, 12), (8, "stop"))

    def test_structure_stop_moves_only_after_confirmed_low_and_new_high(self):
        rows = {
            "b": (10, 11, 11, 9.5),
            "c": (11, 11.5, 12, 10.6),
            "d": (11, 10.8, 11.5, 10.4),
            "e": (11, 11.4, 11.8, 10.5),
            "f": (11, 11.8, 12.4, 10.6),
            "g": (11.8, 12.8, 13, 10.5),
            "h": (11.8, 10.3, 12, 10.1),
        }
        _, trades, counts = simulate(
            rows, {"a": Order(10, 9, 12.5, True)}, {"f": ("d", 10.4)},
            "bcdefgh", dict(zip("bcdefgh", "abcdefg")), strong=True, trail=True, slip=0)
        self.assertEqual(counts["stop_raises"], 1)
        self.assertEqual(trades[0]["exit"], "h")
        self.assertEqual(trades[0]["exit_price"], 10.4)

    def test_limit_up_open_cannot_be_bought_under_conservative_rules(self):
        rows = {"a": (9.8, 10, 10.1, 9.7), "b": (11, 10.8, 11, 10.5)}
        args = (rows, {"a": Order(10.5, 9.5, 12, True)}, {}, "b", {"b": "a"})
        _, _, ideal = simulate(*args, strong=True, trail=False, slip=0)
        _, _, careful = simulate(*args, strong=True, trail=False, slip=0, blocks={"b": (True, False)})
        self.assertEqual((ideal["entries"], careful["entries"]), (1, 0))

    def test_buy_and_hold_waits_for_an_open_below_limit_up(self):
        rows = {"a": (11, 10.8, 11, 10.5), "b": (10.9, 11.5, 11.8, 10.8)}
        _, ideal = buy_and_hold(rows, "ab", 0)
        nav, careful = buy_and_hold(rows, "ab", 0, {"a": (True, False)})
        self.assertEqual((ideal["held_days"], careful["held_days"]), (2, 1))
        self.assertEqual(nav[0], 1.0)
        self.assertAlmostEqual(nav[1], (1 - BUY_COST) / 10.9 * 11.5)

    def test_limit_down_close_delays_the_stop_under_conservative_rules(self):
        rows = {"b": (10, 10.2, 10.5, 9.9), "c": (9.8, 9.18, 9.9, 9.18), "d": (9.0, 9.1, 9.3, 8.9)}
        args = (rows, {"a": Order(10, 9.5, 12, True)}, {}, "bcd", dict(zip("bcd", "abc")))
        _, ideal, _ = simulate(*args, strong=True, trail=False, slip=0)
        _, careful, _ = simulate(*args, strong=True, trail=False, slip=0, blocks={"c": (False, True)})
        self.assertEqual([(t["exit"], t["exit_price"]) for t in ideal], [("c", 9.5)])
        self.assertEqual([(t["exit"], t["exit_price"]) for t in careful], [("d", 9.0)])

    def test_paired_exit_report_counts_trades_closed_in_one_version_only(self):
        # The structure target at 12 closes the peak version; the trailing version is still open.
        rows = {"a": (9.8, 10, 10.1, 9.7), "b": (10, 10.5, 10.8, 9.9), "c": (11, 12.2, 12.5, 10.9)}
        report, _ = run({"600000": (rows, {"a": Order(10, 9.5, 12, True)}, {})}, ["a", "b", "c"], "b", "c", 0)
        self.assertEqual(report["paired_exits"]["all"]["count"], 0)
        self.assertEqual(report["paired_exits"]["all"]["unmatched"], {"peak": 1, "trail": 0})
        trail = report["results"]["all_trail"]
        self.assertEqual((trail["trades"], trail["open"]), (0, 1))
        self.assertAlmostEqual(trail["unrealized_return"], trail["return"])
