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
    """腾讯不复权日线的返回格式。"""
    response = Mock()
    response.json.return_value = {"code": 0, "data": {symbol: {"day": [
        [b["date"], str(b["open"]), str(b["close"]), str(b["high"]), str(b["low"]), "1000"] for b in bars]}}}
    return response


def factor_text(symbol, factors=(("1900-01-01", 1.0),)):
    """新浪前复权因子文件：一段 JSON，后面跟一行签名注释。默认没有除权，因子都是 1。"""
    rows = ",".join(f'{{"d":"{day}", "f":"{value}"}}' for day, value in factors)
    return f'var {symbol}qfq={{"total":{len(factors)},"data":[{rows}]}}\n/* K8TroKQUGT+KcYCGPHgU */'


WAVY = make_bars([10 + (i % 30) * 0.2 for i in range(300)])


def fake_source(factors=(("1900-01-01", 1.0),), bars=WAVY):
    """按网址返回腾讯不复权日线或新浪复权因子。"""
    def get(url, params=None, timeout=8, **_):
        if "qfq.js" in url:
            return Mock(text=factor_text(url.rsplit("/", 2)[-2], factors)), None
        return tencent_response(params["param"].split(",")[0], bars), None
    return Mock(side_effect=get)


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

    def test_tencent_bars_come_from_unadjusted_day_only(self):
        # 只读不复权的 day；腾讯减法前复权的 qfqday 即使出现也不用，否则会再乘一遍新浪因子
        body = {"data": {"sh600000": {"qfqday": [["2025-01-02", "1", "1", "1", "1", "100"]],
                                      "day": [["2025-01-02", "10", "11", "11", "9", "100"]]}}}
        self.assertEqual(picker.parse_tencent_kline(body, "sh600000")[0]["close"], 11)
        self.assertEqual(picker.parse_tencent_kline({"data": {"sh600000": {"qfqday": body["data"]["sh600000"]["qfqday"]}}},
                                                    "sh600000"), [])


class AdjustmentTests(unittest.TestCase):
    def test_factor_file_is_parsed_in_date_order(self):
        text = ('var sh601088qfq={"total":3,"data":[{"d":"2026-10-26", "f":"1.0000000000000000"},'
                '{"d":"2026-07-13", "f":"1.0199106054449000"},{"d":"1900-01-01", "f":"1.0455265021435000"}]}\n'
                '/* K8TroKQUGT+KcYCGPHgU++IsFJz6aBGJNr */')
        self.assertEqual(picker.parse_qfq_factors(text),
                         [("1900-01-01", 1.0455265021435), ("2026-07-13", 1.0199106054449), ("2026-10-26", 1.0)])
        broken = ("<html><title>404 Not Found</title></html>", "", None,
                  'var x={"data":[{"d":"bad", "f":"1"},{"d":"2026-01-01", "f":"0"},{"d":"2026-01-02"}]}')
        for text in broken:
            self.assertEqual(picker.parse_qfq_factors(text), [])

    def test_proportional_adjustment_counts_a_dividend_once(self):
        # 10 元买入，涨到 20 元后每股分红 2 元、除息价 18 元：持有收益是 (18 + 2) / 10 - 1 = 100%。
        # 腾讯式减法复权会把起点压到 10 - 2 = 8 元，算成 18 / 8 - 1 = 125%。
        raw = make_bars([10, 20, 18], start=date(2026, 1, 1))
        factors = [("1900-01-01", 20 / 18), ("2026-01-03", 1.0)]
        adjusted = picker.forward_adjust(raw, factors)
        self.assertAlmostEqual(adjusted[-1]["close"] / adjusted[0]["close"] - 1, 1.0)
        self.assertEqual(adjusted[-1]["close"], 18)  # 最后一根保持真实成交价
        self.assertEqual(raw[0]["close"], 10)        # 不改动原始数据
        # 因子表里已经列出未来的除权日时，最后一根仍是真实成交价
        later = picker.forward_adjust(raw, factors + [("2026-02-02", 0.9)])
        self.assertEqual(later[-1]["close"], 18)
        self.assertIsNone(picker.forward_adjust(raw, []))
        self.assertIsNone(picker.forward_adjust(raw, [("2026-01-02", 1.0)]))  # 覆盖不到最早一根

    def test_factor_url_uses_the_exchange_prefix(self):
        get = Mock(return_value=(None, "offline"))
        for market_id, code, symbol in ((1, "600519", "sh600519"), (0, "000001", "sz000001"), (0, "430047", "bj430047"),
                                        (0, "920002", "bj920002")):
            self.assertEqual(picker.fetch_qfq_factors(get, market_id, code), [])
            self.assertEqual(get.call_args.args[0], picker.SINA_QFQ_FACTORS.format(symbol=symbol))

    def test_beijing_codes(self):
        for code, expected in (("920002", True), ("bj430047", True), ("830799", True), ("870204", True),
                               ("600519", False), ("300750", False), ("000001", False), ("688981", False)):
            with self.subTest(code=code):
                self.assertEqual(picker.beijing_stock(code), expected)

    def test_source_label(self):
        rows = [{"source": "tencent", "adjusted": True}, {"source": "tencent", "adjusted": True}]
        self.assertEqual(picker.bars_source_label(rows), "腾讯财经日线，按新浪复权因子等比前复权")
        rows[1] = {"source": "sina", "adjusted": False}
        self.assertEqual(picker.bars_source_label(rows),
                         "腾讯财经日线（1 只改用新浪日线），按新浪复权因子等比前复权；1 只复权因子暂不可用，按不复权价计算")
        self.assertEqual(picker.bars_source_label([{"source": "sina", "adjusted": False}]),
                         "新浪财经日线（腾讯行情暂不可用），复权因子暂不可用，按不复权价计算")


class PickStocksTests(unittest.TestCase):
    def setUp(self):
        self.cache = {}
        self.stocks = [{"code": "300750", "market": 0, "name": "宁德时代"},
                       {"code": "600519", "market": 1, "name": "贵州茅台"}]

    def run_pick(self, get, fallback=None):
        return picker.pick_stocks(get, self.cache.get, self.cache.__setitem__, self.stocks, "year",
                                  fallback=fallback, workers=1)

    def test_uses_adjusted_bars_once_for_all_windows(self):
        get = fake_source()
        rows, failed, skipped = self.run_pick(get)
        self.assertEqual(([row["code"] for row in rows], failed, skipped), (["300750", "600519"], [], []))
        self.assertTrue(all(row["adjusted"] and row["source"] == "tencent" and row["days"] == 250 for row in rows))
        self.assertEqual(get.call_args_list[0].kwargs["params"]["param"], "sz300750,day,,,525,")  # 不复权
        self.assertEqual(get.call_args_list[1].args[0], picker.SINA_QFQ_FACTORS.format(symbol="sz300750"))
        picker.pick_stocks(get, self.cache.get, self.cache.__setitem__, self.stocks, "half", workers=1)
        self.assertEqual(get.call_count, 4)  # 每只一次日线、一次因子；换区间直接用缓存，不重复请求

    def test_bars_are_adjusted_with_sina_factors(self):
        # 第 200 根那天除权：之前每根按 0.9 折算，之后保持原价
        self.run_pick(fake_source(factors=(("1900-01-01", 1 / 0.9), (WAVY[200]["date"], 1.0))))
        bars = self.cache["pick_kline:300750"]["bars"]
        self.assertAlmostEqual(bars[0]["close"], WAVY[0]["close"] * 0.9)
        self.assertAlmostEqual(bars[199]["open"], WAVY[199]["open"] * 0.9)
        self.assertEqual((bars[200]["close"], bars[-1]["close"]), (WAVY[200]["close"], WAVY[-1]["close"]))

    def test_falls_back_to_unadjusted_source(self):
        rows, failed, _ = self.run_pick(Mock(return_value=(None, "HTTPError: 501")), Mock(return_value=WAVY))
        self.assertEqual((len(rows), failed), (2, []))
        self.assertFalse(any(row["adjusted"] for row in rows))
        self.assertTrue(all(row["source"] == "sina" for row in rows))
        self.assertEqual(self.cache, {})  # 没复权的日线不缓存，下次重新尝试
        _, failed, _ = picker.pick_stocks(Mock(return_value=(None, "offline")), {}.get, lambda *_: None,
                                          self.stocks, "year", workers=1)
        self.assertEqual(len(failed), 2)

    def test_factors_are_cached_apart_from_daily_bars(self):
        get = fake_source()
        self.run_pick(get)
        for key in [key for key in self.cache if key.startswith("pick_kline:")]:
            del self.cache[key]  # 日线缓存先过期，复权因子还在
        self.run_pick(get)
        urls = [call.args[0] for call in get.call_args_list]
        self.assertEqual(urls.count(picker.TENCENT_KLINE), 4)
        self.assertEqual(sum("qfq.js" in url for url in urls), 2)

    def test_fallback_bars_with_a_missing_price_are_dropped(self):
        # 新浪日线把缺失的开盘价记成 0；第 50 根正好是近一年回测的起点，留着会除以 0
        broken = [dict(bar) for bar in WAVY]
        broken[50]["open"] = 0.0
        rows, failed, _ = self.run_pick(Mock(return_value=(None, "HTTPError: 501")), Mock(return_value=broken))
        self.assertEqual((len(rows), failed), (2, []))
        self.assertEqual(picker.positive_bars(broken), broken[:50] + broken[51:])

    def test_sina_bars_are_adjusted_when_tencent_is_blocked(self):
        def get(url, params=None, timeout=8, **_):
            return (Mock(text=factor_text("sz300750")), None) if "qfq.js" in url else (None, "HTTPError: 501")
        rows, failed, _ = self.run_pick(Mock(side_effect=get), Mock(return_value=WAVY))
        self.assertEqual(failed, [])
        self.assertTrue(all(row["adjusted"] and row["source"] == "sina" for row in rows))

    def test_stops_calling_a_blocked_source(self):
        self.stocks = [{"code": f"30000{i}", "market": 0, "name": str(i)} for i in range(6)]
        get = Mock(return_value=(None, "HTTPError: 501"))
        rows, _, _ = self.run_pick(get, Mock(return_value=WAVY))
        urls = [call.args[0] for call in get.call_args_list]
        self.assertEqual(len(rows), 6)
        self.assertEqual(urls.count(picker.TENCENT_KLINE), picker.MAX_SOURCE_FAILURES)
        self.assertEqual(sum("qfq.js" in url for url in urls), picker.MAX_SOURCE_FAILURES)


class PicksRouteTests(unittest.TestCase):
    def setUp(self):
        market._cache.clear()
        self.client = market.app.test_client()

    @staticmethod
    def fake_get(url, params=None, headers=None, timeout=8, retries=1):
        if "qfq.js" in url:
            return Mock(text=factor_text("sz300750")), None
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
            self.assertEqual(data["data_source"], "腾讯财经日线，按新浪复权因子等比前复权")
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
