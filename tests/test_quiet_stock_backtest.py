import unittest
from datetime import date, timedelta

try:
    import numpy as np
except ModuleNotFoundError:
    raise unittest.SkipTest("the quiet-stock backtest requires NumPy")

from backtest_picker import BUY_COST
from market_pool_backtest import Stock, hold_pool
from quiet_stock_backtest import (buffered_pick, month_ends, monthly_pools, path_stats, quiet_measures, rank_quiet,
                                  segment)


def weekdays(first, count):
    days, day = [], first
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def series(closes, shares=1e7):
    """A stock that trades `shares` shares a day at its close."""
    closes = np.asarray(closes, dtype=float)
    bars = np.column_stack((closes, closes, closes * 1.01, closes * 0.99))
    return Stock(bars, closes * shares, np.ones(len(closes)))


def wiggle(swing, count=300):
    """Closes that move up and down by `swing` in log terms on alternate days."""
    return 10 * np.exp(np.cumsum(np.where(np.arange(count) % 2, -swing, swing)))


class MeasureTests(unittest.TestCase):
    def test_volatility_surge_and_spike_cover_their_windows(self):
        steps = np.where(np.arange(300) % 2, -0.01, 0.01)
        steps[290] = np.log(1.09)
        closes = 10 * np.exp(np.cumsum(steps))
        stock = series(closes)
        volatility, surge, spike = quiet_measures(stock, 299)
        self.assertAlmostEqual(volatility, np.std(np.diff(np.log(closes[47:])), ddof=1))
        self.assertAlmostEqual(surge, 1.0)
        self.assertAlmostEqual(spike, 0.09)
        # Twice the shares over the last 20 sessions: 2 against a 250-session average of 1.08.
        stock.amount[-20:] *= 2
        self.assertAlmostEqual(quiet_measures(stock, 299)[1], 2 / 1.08)
        # The same jump 20 sessions earlier falls outside the spike window.
        steps[290], steps[270] = 0.01, np.log(1.09)
        self.assertLess(quiet_measures(series(10 * np.exp(np.cumsum(steps))), 299)[2], 0.02)

    def test_a_price_rise_alone_is_not_a_trading_surge(self):
        # The price doubles over the year while the share count traded stays the same.
        self.assertAlmostEqual(quiet_measures(series(np.linspace(10, 20, 300)), 299)[1], 1.0)

    def test_stocks_without_a_year_of_trading_or_enough_turnover_are_left_out(self):
        closes = wiggle(0.01)
        self.assertIsNone(quiet_measures(series(closes), 200))
        self.assertIsNone(quiet_measures(series(closes, shares=1e5), 299))  # about 1 million yuan a day
        halted = series(closes)
        halted.bars[299] = np.nan
        self.assertIsNone(quiet_measures(halted, 299))
        listed = series(closes)
        listed.bars[:50] = np.nan  # first trade at session 50: 250 sessions through 299
        self.assertIsNone(quiet_measures(listed, 299))
        listed.bars[49] = series(closes).bars[49]  # first trade at session 49
        self.assertIsNotNone(quiet_measures(listed, 299))


class SelectionTests(unittest.TestCase):
    def test_places_are_added_and_ties_go_to_lower_volatility(self):
        rows = {"A": (0.01, 0.5, 0.02), "B": (0.02, 0.4, 0.03), "C": (0.03, 0.9, 0.10)}
        self.assertEqual(rank_quiet(rows), ["A", "B", "C"])
        # Each code is first once, second once and third once.
        rows = {"C": (0.03, 1.0, 0.05), "B": (0.02, 2.0, 0.01), "A": (0.01, 1.5, 0.09)}
        self.assertEqual(rank_quiet(rows), ["A", "B", "C"])

    def test_holdings_stay_while_within_the_buffer(self):
        ranked = list("abcdefgh")
        self.assertEqual(buffered_pick(ranked, {"g", "b"}, size=3, keep=6), {"a", "b", "c"})
        self.assertEqual(buffered_pick(ranked, {"e"}, size=3, keep=6), {"a", "b", "e"})
        self.assertEqual(buffered_pick(ranked, {"f"}, size=3, keep=6), {"a", "b", "f"})  # sixth place still stays
        self.assertEqual(buffered_pick(ranked, {"z"}, size=2, keep=6), {"a", "b"})  # no longer eligible
        self.assertEqual(buffered_pick(ranked, set(), size=3, keep=6), {"a", "b", "c"})

    def test_lists_change_only_at_month_ends_and_never_see_later_prices(self):
        days = weekdays(date(2023, 1, 2), 320)
        closes = {"calm": wiggle(0.002, 320), "mid": wiggle(0.01, 320), "wild": wiggle(0.03, 320)}
        stocks = {code: series(values) for code, values in closes.items()}
        stocks["thin"] = series(wiggle(0.002, 320), shares=1e4)
        quiet, loud, universe = monthly_pools(stocks, days, size=1, keep=1)
        ends = [i for i in month_ends(days) if i >= 252]
        first, second = ends[:2]
        self.assertEqual(quiet[first - 1], frozenset())
        self.assertEqual((quiet[first], loud[first]), ({"calm"}, {"wild"}))
        self.assertEqual(universe[first], {"calm", "mid", "wild"})
        self.assertTrue(all(quiet[i] is quiet[first] for i in range(first, second)))
        self.assertEqual(quiet[second], {"calm"})
        # From the session after the first list, "calm" swings 8% a day on ten times the shares;
        # the lists up to that close stay the same.
        swings = closes["calm"][first] * wiggle(0.08, 319 - first) / 10
        noisy = series(np.concatenate([closes["calm"][:first + 1], swings]))
        noisy.amount[first + 1:] *= 10
        later = monthly_pools(dict(stocks, calm=noisy), days, size=1, keep=1)
        for selection, again in zip((quiet, loud, universe), later):
            self.assertEqual([selection[i] for i in range(first + 1)], [again[i] for i in range(first + 1)])
        self.assertEqual(later[0][second], {"mid"})


class ReportTests(unittest.TestCase):
    def test_hold_pool_records_the_value_at_each_close(self):
        a = np.array([[10, 10, 10, 10], [10, 11, 11.5, 9.5], [12, 12, 12.5, 11.5], [12, 13, 13.5, 11.5]])
        stocks = {"A": Stock(a, np.ones(4), np.ones(4))}
        values = []
        result = hold_pool(stocks, 1, 3, {0: {"A"}, 1: {"A"}, 2: {"A"}}, 0, values=values)
        self.assertEqual(len(values), 3)
        self.assertAlmostEqual(values[0], (1 - BUY_COST) / 10 * 11)
        self.assertAlmostEqual(values[-1] - 1, result["return"])

    def test_years_and_periods_are_cut_from_the_value_path(self):
        days = ["2022-12-30", "2023-06-30", "2023-12-29", "2024-06-28", "2024-12-31"]
        nav = [1.1, 1.21, 1.089, 1.2]
        result = path_stats(nav, days, 1, 4)
        self.assertEqual(result["years"].keys(), {"2023", "2024"})
        self.assertAlmostEqual(result["years"]["2023"], 0.21)
        self.assertAlmostEqual(result["years"]["2024"], 1.2 / 1.21 - 1)
        span = (date(2024, 12, 31) - date(2022, 12, 30)).days / 365.25
        self.assertAlmostEqual(result["annual_return"], 1.2 ** (1 / span) - 1)
        daily = np.array([1.1, 1.1, 0.9, 1.2 / 1.089]) - 1
        self.assertAlmostEqual(result["volatility"], np.std(daily, ddof=1) * np.sqrt(4 / span))
        early, late = result["periods"]["2020-2023"], result["periods"]["2024-2026"]
        self.assertAlmostEqual(early["return"], 0.21)
        self.assertEqual(early["max_drawdown"], 0.0)
        self.assertAlmostEqual(late["return"], 1.2 / 1.21 - 1)
        self.assertAlmostEqual(late["max_drawdown"], -0.1)
        self.assertEqual((late["from"], late["to"]), ("2024-06-28", "2024-12-31"))
        self.assertIsNone(segment(nav, days[1:], days[0], "2025-01-01", "2025-12-31"))


if __name__ == "__main__":
    unittest.main()
