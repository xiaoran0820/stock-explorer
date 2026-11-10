import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

try:
    import numpy as np
except ModuleNotFoundError:
    raise unittest.SkipTest("the standalone market backtest requires NumPy")

import market_pool_backtest
from backtest_picker import BUY_COST, SELL_COST
from execution import ACCOUNT, PARTICIPATION
from market_pool_backtest import (
    Stock, all_symbol_trades, breakout_signals, build_signals, compare_trades, dividend_candidates, hold_pool,
    limit_masks, news_topics, read_dividends, replay, share_multiplier, slot_pools, turnover_rate,
)
from trend_pullback_backtest import Setup, scan

DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
NAN = [np.nan] * 4


def stock(*bars):
    rows = np.array(bars, dtype=float)
    return Stock(rows, np.full(len(rows), 1e9), np.ones(len(rows)))


def one_signal(code="sh600000", stop=9.5, turnover=1e9):
    """A breakout that closed at 10 on day 0, to be bought at the day 1 open."""
    return {1: [(code, Setup(10, stop), turnover)]}


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


class ExecutionRuleTests(unittest.TestCase):
    """Order handling in replay and hold_pool under the ideal and conservative rules."""

    def test_entry_is_planned_for_the_session_after_the_breakout_close(self):
        rng = np.random.default_rng(12)
        closes = 100 + np.cumsum(rng.normal(0.2, 1.2, size=185))
        bars = np.column_stack((closes - 0.25, closes, closes + 0.5, closes - 0.5))
        series = Stock(bars, np.ones(len(bars)), np.ones(len(bars)))
        breakouts = [(i, setup) for i, setup in breakout_signals(series) if i + 1 < len(bars)]
        signals, _ = build_signals({"sh600000": series})
        self.assertTrue(breakouts)
        self.assertEqual(sorted(signals), [i + 1 for i, _ in breakouts])
        for i, setup in breakouts:
            self.assertEqual(signals[i + 1][0][1], setup)
            self.assertEqual(setup.close, closes[i])

    def test_entry_day_stop_waits_for_the_next_session(self):
        # Trading below the stop after the entry does not count; only the close does.
        dipped = stock([10, 10, 10.2, 9.8], [10.1, 10.0, 10.3, 9.3], [10.0, 10.2, 10.4, 9.9])
        result = replay({"sh600000": dipped}, one_signal(), 1, 2, None, 0)
        self.assertEqual((result["trades"], result["open"]), (0, 1))
        # A close below the stop on the entry day is sold at the next open (T+1).
        closed_below = stock([10, 10, 10.2, 9.8], [10.1, 9.4, 10.3, 9.3], [9.6, 9.7, 9.8, 9.5])
        result = replay({"sh600000": closed_below}, one_signal(), 1, 2, None, 0)
        self.assertEqual(result["exits"], {"stop": 1})
        self.assertAlmostEqual(result["mean_trade"], (1 - BUY_COST) / 10.1 * 9.6 * (1 - SELL_COST) - 1)

    def test_suspended_position_is_kept_until_it_trades_again(self):
        bars = stock([10, 10, 10.2, 9.8], [10.1, 10.2, 10.3, 9.9], NAN, [9.0, 9.1, 9.3, 8.9])
        result = replay({"sh600000": bars}, one_signal(), 1, 3, None, 0)
        # Held through the suspension, then sold at the reopening price below the stop.
        self.assertEqual(result["exits"], {"stop": 1})
        self.assertAlmostEqual(result["mean_trade"], (1 - BUY_COST) / 10.1 * 9.0 * (1 - SELL_COST) - 1)

    def test_conservative_rules_skip_a_limit_up_open(self):
        bars = stock([10, 10, 10.2, 9.8], [11, 10.6, 11, 10.4], [10.6, 10.8, 11, 10.5])
        stocks = {"sh600000": bars}
        ideal = replay(stocks, one_signal(stop=9), 1, 2, None, 0)
        careful = replay(stocks, one_signal(stop=9), 1, 2, None, 0, blocks=limit_masks(stocks, DAYS[:3]))
        self.assertEqual((ideal["fills"], ideal["open"]), (1, 1))
        self.assertEqual((careful["fills"], careful["open"], careful["return"]), (0, 0, 0))
        # The same +10% open is below the 20% limit of a ChiNext stock.
        chinext = {"sz300750": bars}
        result = replay(chinext, one_signal("sz300750", stop=9), 1, 2, None, 0, blocks=limit_masks(chinext, DAYS[:3]))
        self.assertEqual(result["fills"], 1)

    def test_conservative_rules_hold_a_stop_through_a_limit_down_close(self):
        bars = stock([10, 10, 10.2, 9.8], [10.1, 10.2, 10.3, 9.9], [9.8, 9.18, 9.9, 9.18], [9.0, 9.1, 9.3, 8.9])
        stocks = {"sh600000": bars}
        ideal = replay(stocks, one_signal(), 1, 3, None, 0)
        careful = replay(stocks, one_signal(), 1, 3, None, 0, blocks=limit_masks(stocks, DAYS))
        shares = (1 - BUY_COST) / 10.1
        self.assertAlmostEqual(ideal["mean_trade"], shares * 9.5 * (1 - SELL_COST) - 1)
        # Locked at limit-down on day 2, so the stop is sold at the day 3 open instead.
        self.assertAlmostEqual(careful["mean_trade"], shares * 9.0 * (1 - SELL_COST) - 1)
        self.assertEqual(careful["exits"], {"stop": 1})

    def test_limit_masks_use_the_previous_traded_close_and_one_raw_tick(self):
        # The Beijing exchange rounds limits down: 5.2601 * 1.3 = 6.838 becomes 6.83.
        bars = np.array([[5.0, 5.2601, 5.3, 5.0], NAN, [6.83, 6.83, 6.83, 6.5]])
        for scale in (1, 2):  # adjusted prices are raw prices times the factor
            series = Stock(bars * scale, np.ones(3), np.full(3, float(scale)))
            no_buy, no_sale = limit_masks({"bj920000": series}, ["2024-11-01", "2024-11-04", "2024-11-05"])["bj920000"]
            self.assertEqual((list(no_buy), list(no_sale)), ([False, False, True], [False] * 3))
        # ChiNext moved from a 10% to a 20% limit on 2020-08-24.
        bars = np.array([[10, 10, 10, 10], [11, 11, 11, 10.5], [12.1, 12, 12.1, 11.5]], dtype=float)
        series = Stock(bars, np.ones(3), np.ones(3))
        no_buy, _ = limit_masks({"sz300750": series}, ["2020-08-20", "2020-08-21", "2020-08-24"])["sz300750"]
        self.assertEqual(list(no_buy), [False, True, False])

    def test_capacity_cap_trims_an_entry_to_a_share_of_turnover(self):
        bars = stock([10, 10, 10.2, 9.8], [10.1, 10.2, 10.3, 9.9])
        turnover = ACCOUNT / 2  # 5% of it is 2.5% of the account, under the 5% slot
        result = replay({"sh600000": bars}, one_signal(turnover=turnover), 1, 1, None, 0, account=ACCOUNT)
        spent = PARTICIPATION * turnover / ACCOUNT
        self.assertEqual(result["capacity_trimmed"], 1)
        self.assertAlmostEqual(result["return"], spent * ((1 - BUY_COST) / 10.1 * 10.2 - 1))

    def test_portfolio_value_reaching_zero_finishes_the_run(self):
        bars = stock([10, 10, 10.2, 9.8], [10.1, 10.2, 10.3, 9.9], [10.2, 10.3, 10.4, 10.1])
        with mock.patch.object(market_pool_backtest, "SLOTS", 1):  # one position takes all the cash
            result = replay({"sh600000": bars}, one_signal(), 1, 2, None, 0, {"sh600000": 2})
        self.assertEqual(result["return"], -1)
        self.assertEqual(result["exits"], {"delisted_zero_recovery": 1})
        self.assertEqual(result["exposure"], 0.5)

    def test_open_positions_are_reported_at_their_last_close(self):
        bars = stock([10, 10, 10.2, 9.8], [10.1, 10.2, 10.3, 9.9], [10.2, 9.8, 10.3, 9.7])
        result = replay({"sh600000": bars}, one_signal(), 1, 2, None, 0)
        cost = 1 / 20
        value = cost * (1 - BUY_COST) / 10.1 * 9.8
        self.assertEqual((result["trades"], result["win_rate"], result["open"], result["open_losing"]),
                         (0, None, 1, 1))
        self.assertAlmostEqual(result["unrealized_return"], value - cost)
        self.assertAlmostEqual(result["realized_return"], 0)
        self.assertAlmostEqual(result["return"], value - cost)

    def test_every_signal_check_follows_the_conservative_rules_too(self):
        # A limit-up open cannot be bought, so the conservative check has no fill at all.
        opened_at_limit = stock([10, 10, 10.2, 9.8], [11, 10.6, 11, 10.4], [10.6, 10.8, 11, 10.5])
        stocks = {"sh600000": opened_at_limit}
        args = (stocks, one_signal(stop=9), DAYS[:3], 1, 2, 0, {})
        self.assertEqual(all_symbol_trades(*args)["stocks_with_fills"], 1)
        self.assertEqual(all_symbol_trades(*args, limit_masks(stocks, DAYS[:3]))["stocks_with_fills"], 0)
        # A stop hit on a limit-down close is sold at the next open instead.
        locked = stock([10, 10, 10.2, 9.8], [10.1, 10.2, 10.3, 9.9], [9.8, 9.18, 9.9, 9.18], [9.0, 9.1, 9.3, 8.9])
        stocks = {"sh600000": locked}
        args = (stocks, one_signal(), DAYS, 1, 3, 0, {})
        shares = (1 - BUY_COST) / 10.1
        self.assertAlmostEqual(all_symbol_trades(*args)["mean_trade"], shares * 9.5 * (1 - SELL_COST) - 1)
        self.assertAlmostEqual(all_symbol_trades(*args, limit_masks(stocks, DAYS))["mean_trade"],
                               shares * 9.0 * (1 - SELL_COST) - 1)

    def test_replay_log_receives_each_closed_trade(self):
        bars = stock([10, 10, 10.2, 9.8], [10.1, 10.2, 10.3, 9.9], [9.8, 9.18, 9.9, 9.18], [9.0, 9.1, 9.3, 8.9])
        log = []
        replay({"sh600000": bars}, one_signal(), 1, 3, None, 0, log=log)
        self.assertEqual([(t["code"], t["entry"], t["exit"], t["reason"]) for t in log], [("sh600000", 1, 2, "stop")])

    def test_trade_comparison_names_refused_entries_and_changed_exits(self):
        days = ["d0", "d1", "d2", "d3"]
        masks = {"A": (np.array([False, True, False, False]), np.zeros(4, bool)),
                 "B": (np.zeros(4, bool), np.zeros(4, bool))}
        # A's entry opened at limit-up; B's first entry was lost for another reason (a slot was still busy).
        ideal = [{"code": "A", "entry": 1, "return": 0.1}, {"code": "B", "entry": 1, "return": 0.03},
                 {"code": "B", "entry": 2, "return": -0.05}]
        careful = [{"code": "B", "entry": 2, "return": -0.08}, {"code": "B", "entry": 3, "return": 0.02}]
        self.assertEqual(compare_trades(ideal, careful, masks, days),
                         {"shared": 1, "ideal_only": 2, "conservative_only": 1,
                          "refused_limit_up": ["d1"], "exit_changed": 1})


class BaselineTests(unittest.TestCase):
    def test_slot_baseline_spends_the_same_slot_as_the_rule(self):
        bars = stock([10, 10, 10, 10], [10, 11, 11.5, 9.5], [11, 12, 12.5, 10.5])
        pools = {0: frozenset({"sh600000"}), 1: frozenset({"sh600000"})}
        stocks = {"sh600000": bars}
        slot = hold_pool(stocks, 1, 2, pools, 0, slots=20)
        rule = replay(stocks, one_signal(stop=5), 1, 2, pools, 0)
        self.assertAlmostEqual(slot["return"], ((1 - BUY_COST) / 10 * 12 - 1) / 20)
        self.assertAlmostEqual(rule["return"], slot["return"])
        # Without slots the whole portfolio goes into the one-name pool.
        self.assertAlmostEqual(hold_pool(stocks, 1, 2, pools, 0)["return"], (1 - BUY_COST) / 10 * 12 - 1)

    def test_slot_baseline_buys_newcomers_without_trimming_winners(self):
        a = stock([10, 10, 10, 10], [10, 20, 21, 9.5], [20, 20, 21, 19], [20, 22, 23, 19])
        b = stock([10, 10, 10, 10], [10, 10, 11, 9], [10, 10, 11, 9], [10, 11, 12, 9])
        pools = {0: frozenset({"A"}), 1: frozenset({"A", "B"}), 2: frozenset({"A", "B"})}
        result = hold_pool({"A": a, "B": b}, 1, 3, pools, 0, slots=2)
        # Day 1: half the money into A. Day 2: A has doubled, yet only B is bought, with the cash left.
        self.assertAlmostEqual(result["return"], 0.5 * (1 - BUY_COST) * (22 + 11) / 10 - 1)

    def test_turnover_is_half_the_traded_value_over_the_average_value(self):
        self.assertAlmostEqual(turnover_rate({"traded": 3.0}, [1.0, 2.0]), 1.0)
        self.assertEqual(turnover_rate({"traded": 0.0}, [0.0, 0.0]), 0.0)

    def test_slot_pools_keep_names_and_fill_free_places_by_turnover(self):
        activity = {code: (np.array(values, dtype=float), None) for code, values in
                    {"A": [5, 5, 5], "B": [9, 9, 1], "C": [7, 7, 7], "D": [1, 1, 9]}.items()}
        selection = {0: {"A", "B", "C"}, 1: {"A", "B", "C", "D"}, 2: {"A", "C", "D"}}
        self.assertEqual(slot_pools(selection, activity, 1, 3, slots=2),
                         {0: {"B", "C"}, 1: {"B", "C"}, 2: {"C", "D"}})

    def test_exposure_baseline_keeps_the_rest_in_cash(self):
        bars = stock([10, 10, 10, 10], [10, 11, 11.5, 9.5], [11, 12, 12.5, 10.5])
        pools = {0: frozenset({"sh600000"}), 1: frozenset({"sh600000"})}
        result = hold_pool({"sh600000": bars}, 1, 2, pools, 0, invest=0.4)
        self.assertAlmostEqual(result["return"], 0.4 * ((1 - BUY_COST) / 10 * 12 - 1))

    def test_conservative_baseline_waits_for_a_limit_up_name(self):
        bars = stock([10, 10, 10, 10], [11, 10.8, 11, 10.5], [10.9, 11.5, 11.8, 10.8])
        stocks = {"sh600000": bars}
        pools = {0: frozenset({"sh600000"}), 1: frozenset({"sh600000"})}
        self.assertAlmostEqual(hold_pool(stocks, 1, 2, pools, 0)["return"], (1 - BUY_COST) / 11 * 11.5 - 1)
        careful = hold_pool(stocks, 1, 2, pools, 0, blocks=limit_masks(stocks, DAYS[:3]))
        self.assertAlmostEqual(careful["return"], (1 - BUY_COST) / 10.9 * 11.5 - 1)

    def test_conservative_baseline_cannot_sell_on_a_limit_down_close(self):
        a = stock([10, 10, 10, 10], [10, 10, 10.5, 9.5], [9.5, 9.0, 9.6, 9.0], [9.2, 9.3, 9.4, 9.1])
        b = stock([20, 20, 20, 20], [20, 20, 21, 19], [20, 20, 21, 19], [21, 22, 22.5, 20.5])
        stocks = {"sh600000": a, "sh600001": b}
        pools = {0: frozenset({"sh600000"}), 1: frozenset({"sh600001"}), 2: frozenset({"sh600001"})}
        result = hold_pool(stocks, 1, 3, pools, 0, blocks=limit_masks(stocks, DAYS))
        expected = (1 - BUY_COST) / 10 * 9.2 * (1 - SELL_COST) * (1 - BUY_COST) / 21 * 22
        self.assertAlmostEqual(result["return"], expected - 1)
        self.assertGreater(result["turnover"], 0)
