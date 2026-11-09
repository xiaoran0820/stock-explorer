import unittest

from brooks_backtest import Order, exit_order, scan, simulate


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
