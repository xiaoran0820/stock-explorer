"""回测选股：买卖规则、回测计算、数据源降级和 /api/picks 接口。"""
import unittest
from datetime import date, timedelta
from unittest.mock import Mock, patch

import app as market
import backtest_picker as picker


def make_bars(closes, opens=None, start=date(2025, 1, 1)):
    opens = opens or closes
    return [{"date": (start + timedelta(days=i)).isoformat(), "open": float(o), "close": float(c),
             "high": float(max(o, c)), "low": float(min(o, c))} for i, (o, c) in enumerate(zip(opens, closes))]


def tencent_response(symbol, bars):
    response = Mock()
    response.json.return_value = {"code": 0, "data": {symbol: {"qfqday": [
        [b["date"], str(b["open"]), str(b["close"]), str(b["high"]), str(b["low"]), "1000"] for b in bars]}}}
    return response


WAVY = make_bars([10 + (i % 30) * 0.2 for i in range(300)])


class StrategyTests(unittest.TestCase):
    def test_ma_cross_needs_a_real_cross(self):
        # MA1 就是收盘价，MA2 是两天均价：收盘价比前一天高时 MA1 > MA2
        self.assertEqual(picker.ma_cross_positions([10, 9, 8, 9, 10, 9, 8], short=1, long=2), [0, 0, 0, 1, 1, 0, 0])
        # 均线刚算出来时短线已经在上方，但并没有发生穿越，不应该买入
        self.assertEqual(picker.ma_cross_positions([1, 2, 3, 4, 5], short=1, long=2), [0, 0, 0, 0, 0])

    def test_momentum_requires_rise_and_price_above_average(self):
        self.assertEqual(picker.momentum_positions([10, 11, 12, 11, 13], days=2, window=2), [0, 0, 1, 0, 1])

    def test_latest_signal(self):
        bars = make_bars([1, 1, 1])
        self.assertEqual(picker.signal_state([0, 0, 1], bars)["signal"], "buy")
        hold = picker.signal_state([0, 1, 1], bars)
        self.assertEqual((hold["signal"], hold["since"]), ("hold", bars[1]["date"]))
        self.assertEqual(picker.signal_state([1, 1, 0], bars)["signal"], "sell")
        self.assertEqual(picker.signal_state([0, 0, 0], bars)["signal"], "flat")


class BacktestTests(unittest.TestCase):
    def test_trades_at_next_open_and_charges_costs(self):
        bars = make_bars(closes=[10, 10, 12, 13, 14], opens=[10, 10, 11, 12, 14])
        stats = picker.run_backtest(bars, [0, 1, 1, 0, 0], start=1)
        # 第 1 天收盘出买入信号 → 第 2 天开盘 11 元买；第 3 天收盘出卖出信号 → 第 4 天开盘 14 元卖
        expected = (1 - picker.BUY_COST) * (1 - picker.SELL_COST) * 14 / 11 - 1
        self.assertAlmostEqual(stats["total_return"], expected)
        self.assertEqual((stats["trades"], stats["win_rate"], stats["position_open"]), (1, 1.0, False))
        self.assertAlmostEqual(stats["exposure"], 0.5)

    def test_signal_on_last_day_is_not_traded(self):
        stats = picker.run_backtest(make_bars([10, 10, 10, 20]), [0, 0, 0, 1], start=1)
        self.assertEqual((stats["total_return"], stats["trades"]), (0, 0))

    def test_max_drawdown(self):
        bars = make_bars(closes=[10, 10, 12, 9, 13], opens=[10, 10, 10, 12, 9])
        self.assertAlmostEqual(picker.run_backtest(bars, [1] * 5, start=1)["max_drawdown"], 9 / 12 - 1)

    def test_strategy_that_avoids_a_crash_is_picked(self):
        # 上涨 → 大跌 → 再上涨：动量规则在下跌途中离场，应该跑赢一直持有
        closes = ([10 + 0.1 * i for i in range(90)] + [18.9 - 0.25 * i for i in range(30)]
                  + [11.6 + 0.12 * i for i in range(80)])
        result = picker.backtest_stock(make_bars(closes), days=175)
        momentum = result["results"]["momentum"]
        self.assertEqual(result["days"], 175)
        self.assertGreater(momentum["excess_return"], 0)
        self.assertEqual(momentum["signal"], "hold")
        self.assertTrue(momentum["picked"])

    def test_short_history_is_skipped(self):
        self.assertIsNone(picker.backtest_stock(make_bars([10] * 70), days=250))

    def test_tencent_rows_are_cleaned(self):
        body = {"data": {"sz000001": {"day": [
            ["2025-01-03", "10", "11", "11", "9", "100"],
            ["2025-01-02", "9", "10", "10", "8", "100", {"nd": "2024"}],  # 带分红信息的行
            ["2025-01-02", "9", "10", "10", "8", "100"],                 # 重复日期
            ["bad-date", "1", "1", "1", "1"],
            ["2025-01-04", "0", "11", "11", "9", "100"],                 # 价格为 0
        ]}}}
        self.assertEqual([bar["date"] for bar in picker.parse_tencent_kline(body, "sz000001")],
                         ["2025-01-02", "2025-01-03"])
        self.assertEqual(picker.parse_tencent_kline({"data": "error"}, "sz000001"), [])


class PickStocksTests(unittest.TestCase):
    def setUp(self):
        self.cache = {}
        self.stocks = [{"code": "300750", "market": 0, "name": "宁德时代"},
                       {"code": "600519", "market": 1, "name": "贵州茅台"}]

    def run_pick(self, get, fallback=None):
        return picker.pick_stocks(get, self.cache.get, self.cache.__setitem__, self.stocks, "year",
                                  fallback=fallback, workers=1)

    def test_uses_adjusted_bars_once_for_all_windows(self):
        get = Mock(side_effect=lambda url, params=None, timeout=8:
                   (tencent_response(params["param"].split(",")[0], WAVY), None))
        rows, failed, skipped = self.run_pick(get)
        self.assertEqual(([row["code"] for row in rows], failed, skipped), (["300750", "600519"], [], []))
        self.assertTrue(all(row["adjusted"] and row["days"] == 250 for row in rows))
        self.assertEqual(get.call_args_list[0].kwargs["params"]["param"], "sz300750,day,,,525,qfq")
        picker.pick_stocks(get, self.cache.get, self.cache.__setitem__, self.stocks, "half", workers=1)
        self.assertEqual(get.call_count, 2)  # 换区间直接用缓存，不重复请求

    def test_falls_back_to_unadjusted_source(self):
        rows, failed, _ = self.run_pick(Mock(return_value=(None, "HTTPError: 501")), Mock(return_value=WAVY))
        self.assertEqual((len(rows), failed), (2, []))
        self.assertFalse(any(row["adjusted"] for row in rows))
        _, failed, _ = picker.pick_stocks(Mock(return_value=(None, "offline")), {}.get, lambda *_: None,
                                          self.stocks, "year", workers=1)
        self.assertEqual(len(failed), 2)

    def test_stops_calling_a_blocked_source(self):
        self.stocks = [{"code": f"30000{i}", "market": 0, "name": str(i)} for i in range(6)]
        get = Mock(return_value=(None, "HTTPError: 501"))
        self.run_pick(get, Mock(return_value=WAVY))
        self.assertEqual(get.call_count, picker.MAX_SOURCE_FAILURES)


class PicksRouteTests(unittest.TestCase):
    def setUp(self):
        market._cache.clear()
        self.client = market.app.test_client()

    @staticmethod
    def fake_get(url, params=None, headers=None, timeout=8, retries=1):
        return tencent_response(params["param"].split(",")[0], WAVY), None

    def test_hot_topics(self):
        snapshot = {"topics": [{"name": "固态电池", "code": "BK1173", "change_pct": 2.5, "kind": "board"}]}
        members = [{"code": "300750", "name": "宁德时代", "price": 300.0, "change_pct": 1.0, "market_cap": 1e12}]
        with patch.object(market, "get_hot_news", return_value=snapshot), \
             patch.object(market, "get_concept_stocks", return_value=members) as constituents, \
             patch.object(market, "http_get", side_effect=self.fake_get):
            response = self.client.get("/api/picks?scope=hot&window=year")
            data = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(data["topics"], [{"name": "固态电池", "change_pct": 2.5}])
            stock = data["stocks"][0]
            self.assertEqual((stock["code"], stock["market"], stock["topics"]), ("300750", 0, ["固态电池"]))
            self.assertEqual(set(stock["results"]), {"ma_cross", "momentum"})
            self.assertEqual(data["data_source"], "腾讯财经前复权日线")
            self.assertFalse(data["cached"])
            self.assertTrue(self.client.get("/api/picks?scope=hot&window=year").get_json()["cached"])
        constituents.assert_called_once_with("BK1173", top_n=market.PICK_PER_TOPIC)

    def test_user_topic_and_validation(self):
        matched = {"concepts": [{"name": "机器人概念", "score": 1.0}], "total_related": 1, "match_status": "ok",
                   "stocks": [{"code": "002050", "name": "三花智控", "matched_via": "机器人概念", "relevance": 1.0}]}
        with patch.object(market, "match_query_to_stocks", return_value=matched) as match, \
             patch.object(market, "http_get", side_effect=self.fake_get):
            data = self.client.get("/api/picks?scope=topic&query=机器人&window=half").get_json()
        match.assert_called_once_with("机器人")
        self.assertEqual((data["stocks"][0]["topics"], data["window_days"]), (["机器人概念"], 120))
        for query in ("scope=topic", "scope=all", "window=10y"):
            self.assertEqual(self.client.get(f"/api/picks?{query}").status_code, 400)

    def test_busy_board_source_is_not_reported_as_no_match(self):
        for status, message in (("unavailable", "稍后重试"), ("ok", "没有找到")):
            matched = {"concepts": [{"name": "固态电池", "score": 1.0}], "stocks": [], "total_related": 0,
                       "match_status": status}
            with self.subTest(status=status), patch.object(market, "match_query_to_stocks", return_value=matched):
                response = self.client.get("/api/picks?scope=topic&query=固态电池")
                self.assertEqual(response.status_code, 503)
                self.assertIn(message, response.get_json()["error"])

    def test_unavailable_quotes_return_503(self):
        snapshot = {"topics": [{"name": "固态电池", "code": "BK1173", "change_pct": 2.5}]}
        with patch.object(market, "get_hot_news", return_value=snapshot), \
             patch.object(market, "get_concept_stocks", return_value=[{"code": "300750", "name": "宁德时代"}]), \
             patch.object(market, "http_get", return_value=(None, "offline")):
            response = self.client.get("/api/picks?scope=hot")
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
