import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

try:
    import numpy as np
except ModuleNotFoundError:
    raise unittest.SkipTest("the standalone market backtest requires NumPy")

from backtest_picker import BUY_COST, SELL_COST
from market_pool_backtest import (
    Stock, breakout_signals, dividend_candidates, hold_pool, news_topics, read_dividends, replay, share_multiplier,
)
from trend_pullback_backtest import Setup, scan


class MarketPoolBacktestTests(unittest.TestCase):
    def test_full_market_breakout_matches_previous_strategy(self):
        days = [str(i).zfill(3) for i in range(185)]
        rng = np.random.default_rng(12)
        closes = 100 + np.cumsum(rng.normal(0.2, 1.2, size=len(days)))
        bars = np.column_stack((closes - 0.25, closes, closes + 0.5, closes - 0.5))
        bars[[44, 101, 130]] = np.nan
        rows = {d: tuple(b) for d, b in zip(days, bars) if np.isfinite(b[1])}
        old = scan(rows)[0]["breakout"]
        stock = Stock(bars, np.ones(len(days)), np.ones(len(days)))
        self.assertTrue(old)
        self.assertEqual({days[i]: setup for i, setup in breakout_signals(stock)}, old)

    def test_dividends_after_ranking_date_cannot_make_stock_eligible(self):
        days = [(date(2019, 1, 1) + timedelta(days=i)).isoformat() for i in range(400)]
        c = np.linspace(9, 10, len(days))
        stock = Stock(np.column_stack((c, c, c, c)), np.full(len(c), 50e6), np.ones(len(c)))
        paid = {"2017-06-01": 0.1, "2018-06-01": 0.1, "2020-03-01": 0.3}
        self.assertEqual(dividend_candidates(stock, paid, days, [399]), {})
        paid["2019-06-01"] = 0.2
        self.assertAlmostEqual(dividend_candidates(stock, paid, days, [399])[399][0], 0.02)

    def test_bonus_shares_restate_earlier_cash_per_current_share(self):
        # 排名日 2020-02-04，收盘 10 元。2019-06-01 派 0.2 元并 10 转 1，2019-09-02 又 10 转 10：
        # 当时 1 股现在变成 2.2 股，所以那笔 0.2 元折合现在每股 0.2 / 2.2 元。
        # 2019-10-01 的 0.1 元派在转增之后，不用折算；2020-03-02 的转增在排名日之后，还不知道。
        days = [(date(2019, 1, 1) + timedelta(days=i)).isoformat() for i in range(400)]
        c = np.linspace(9, 10, len(days))
        stock = Stock(np.column_stack((c, c, c, c)), np.full(len(c), 50e6), np.ones(len(c)))
        paid = {"2017-06-01": 0.1, "2018-06-01": 0.1, "2019-06-01": 0.2, "2019-10-01": 0.1}
        bonuses = {"2019-06-01": 1.1, "2019-09-02": 2.0, "2020-03-02": 3.0}
        self.assertAlmostEqual(dividend_candidates(stock, paid, days, [399], bonuses)[399][0],
                               (0.2 / 2.2 + 0.1) / 10)
        self.assertAlmostEqual(dividend_candidates(stock, paid, days, [399])[399][0], 0.03)

    def test_share_multiplier_reads_bonus_and_transfer_ratios(self):
        cases = {"10转1股派1.20元(含税,扣税后1.08元)": 1.1, "10转8.00派2.00元(含税,扣税后1.00元)": 1.8,
                 "10送10.00转5.00派2.00元(含税,扣税后0.30元)": 2.5, "10送2股转2股派1.00元": 1.4,
                 "10送6.00(应补缴0.60元)": 1.6, "10转2.90": 1.29, "10派3.00元(含税,扣税后2.70元)": 1.0, None: 1.0}
        for plan, expected in cases.items():
            with self.subTest(plan=plan):
                self.assertAlmostEqual(share_multiplier(plan), expected)

    def test_read_dividends_keeps_cash_and_share_changes(self):
        rows = [
            {"SECUCODE": "600000.SH", "EX_DIVIDEND_DATE": "2026-06-09 00:00:00", "ASSIGN_PROGRESS": "实施分配",
             "PRETAX_BONUS_RMB": 1.2, "IMPL_PLAN_PROFILE": "10转1股派1.20元(含税,扣税后1.08元)"},
            {"SECUCODE": "600000.SH", "EX_DIVIDEND_DATE": "2026-08-03 00:00:00", "ASSIGN_PROGRESS": "实施分配",
             "PRETAX_BONUS_RMB": None, "IMPL_PLAN_PROFILE": "10转2.90"},
            {"SECUCODE": "600000.SH", "EX_DIVIDEND_DATE": None, "ASSIGN_PROGRESS": "董事会预案",
             "PRETAX_BONUS_RMB": 3, "IMPL_PLAN_PROFILE": "10派3元"},
        ]
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "2026-06-30.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            cash, bonuses = read_dividends(Path(folder), {})
        self.assertEqual(list(cash["sh600000"]), ["2026-06-09"])
        self.assertAlmostEqual(cash["sh600000"]["2026-06-09"], 0.12)
        self.assertEqual(sorted(bonuses["sh600000"]), ["2026-06-09", "2026-08-03"])
        self.assertAlmostEqual(bonuses["sh600000"]["2026-08-03"], 1.29)

    def test_news_after_close_only_available_on_following_session(self):
        rows = [
            ("2025-01-01T12:00:00", "source1", "baseline unrelated news"),
            ("2025-01-28T10:00:00", "source1", "半导体新闻甲"),
            ("2025-01-28T11:00:00", "source1", "半导体新闻乙"),
            ("2025-01-28T15:01:00", "source2", "半导体新闻丙"),
            ("2025-01-30T10:00:00", "source1", "other news"),
        ]
        self.assertEqual(news_topics(rows, ["2025-01-28", "2025-01-29"], {"半导体": "chips"}),
                         {0: [], 1: ["chips"]})

    def test_dropped_pool_membership_does_not_close_position(self):
        bars = np.array([[10, 10, 10, 10], [10, 11.5, 12, 9.5], [10.5, 11, 11.5, 10]])
        stock = Stock(bars, np.ones(len(bars)), np.ones(len(bars)))
        result = replay({"sh600000": stock}, {1: [("sh600000", Setup(10, 9), 100)]},
                        1, 2, {0: {"sh600000"}, 1: set()}, 0)
        self.assertEqual(result["trades"], 1)
        self.assertEqual(result["exits"], {"target": 1})

    def test_hold_pool_buys_at_next_open_and_switches_with_costs(self):
        # 第 0 天收盘选出 A，第 1 天开盘买入；第 1 天收盘名单换成 B，第 2 天开盘卖 A 买 B，之后一直拿着
        a = np.array([[10, 10, 10, 10], [10, 11, 11.5, 9.5], [12, 12, 12.5, 11.5], [12, 13, 13.5, 11.5]])
        b = np.array([[20, 20, 20, 20], [20, 20, 21, 19], [20, 22, 22.5, 19.5], [22, 24, 24.5, 21.5]])
        stocks = {code: Stock(bars, np.ones(4), np.ones(4)) for code, bars in (("A", a), ("B", b))}
        result = hold_pool(stocks, 1, 3, {0: {"A"}, 1: {"B"}, 2: {"B"}}, 0)
        expected = (1 - BUY_COST) / 10 * 12 * (1 - SELL_COST) * (1 - BUY_COST) / 20 * 24
        self.assertAlmostEqual(result["return"], expected - 1)
        self.assertEqual(result["rebalances"], 2)

    def test_hold_pool_waits_for_a_suspended_name_before_switching(self):
        # 名单换成 B 那天 A 停牌卖不掉，钱都在 A 上买不了 B；A 复牌当天开盘卖出，再买 B
        nan = [np.nan] * 4
        a = np.array([[10, 10, 10, 10], [10, 10, 10.5, 9.5], nan, [11, 11, 11.5, 10.5]])
        b = np.array([[20, 20, 20, 20], [20, 20, 21, 19], [20, 20, 21, 19], [21, 22, 22.5, 20.5]])
        stocks = {code: Stock(bars, np.ones(4), np.ones(4)) for code, bars in (("A", a), ("B", b))}
        result = hold_pool(stocks, 1, 3, {0: {"A"}, 1: {"B"}, 2: {"B"}}, 0)
        expected = (1 - BUY_COST) / 10 * 11 * (1 - SELL_COST) * (1 - BUY_COST) / 21 * 22
        self.assertAlmostEqual(result["return"], expected - 1)
