import unittest
from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch

import app as website
from steady_strategy import breakout_reading, closed_bars, entry_open_passed, pool_period, verified_previous_session
from test_picker import factor_text, tencent_response
from trend_pullback_backtest import scan


def rising_bars():
    first = date(2026, 1, 1)
    return [{"date": (first + timedelta(days=i)).isoformat(), "open": 100 + i, "close": 101 + i,
             "high": 101.5 + i, "low": 99 + i} for i in range(121)]


class SteadyStrategyTests(unittest.TestCase):
    def test_uses_only_completed_unique_daily_bars(self):
        bars = rising_bars()
        latest = date.fromisoformat(bars[-1]["date"])
        open_session = datetime(latest.year, latest.month, latest.day, 14, 30)
        self.assertEqual(closed_bars(bars, open_session), bars[:-1])
        self.assertEqual(closed_bars([bars[-1], *bars], open_session.replace(hour=15)), bars)

    def test_breakout_levels_match_backtest_and_target_uses_actual_open(self):
        bars = rising_bars()
        rows = {b["date"]: tuple(b[key] for key in ("open", "close", "high", "low")) for b in bars}
        setups, _ = scan(rows)
        last, prior = bars[-1]["date"], bars[-2]["date"]
        reading = breakout_reading(bars, prior, prior)
        self.assertEqual(reading["setup"], last in setups["breakout"])
        self.assertEqual(reading["stop"], setups["breakout"][last].stop)
        previous = reading["previous_plan"]
        self.assertTrue(previous["filled"])
        self.assertEqual(previous["target"], 2 * bars[-1]["open"] - setups["breakout"][prior].stop)
        self.assertNotIn("previous_plan", breakout_reading(bars, prior, "2026-01-01"))

    def test_market_calendar_and_pool_expiry(self):
        self.assertEqual(verified_previous_session([], "2026-11-06", "2026-11-09"), "2026-11-06")
        self.assertIsNone(verified_previous_session([], "2026-11-05", "2026-11-09"))
        snapshot = {"as_of": "2026-10-30", "yield_floor": 0.03, "turnover_floor": 30_000_000}
        self.assertFalse(pool_period(snapshot, datetime(2026, 11, 9))["stale"])
        self.assertTrue(pool_period(snapshot, datetime(2026, 12, 1))["stale"])

    def test_pool_route_and_stock_fallback(self):
        row = {"code": "600900", "exchange": "sh", "name": "长江电力",
               "selection": {"selected": True, "rank": 1},
               "archived": {"as_of": "2026-11-06", "enough": True}}
        snapshot = {"as_of": "2026-10-30", "members": ["sh600900"], "stocks": {"sh600900": row},
                    "archive_through": "2026-11-06", "sessions": ["2026-11-06"],
                    "yield_floor": 0.03, "turnover_floor": 30_000_000}
        client = website.app.test_client()
        with patch.object(website, "steady_snapshot", return_value=snapshot), \
             patch.object(website, "steady_live_bars", return_value=([], "腾讯财经前复权日线")):
            self.assertEqual(client.get("/api/steady/pool").json["stocks"][0]["code"], "600900")
            stock = client.get("/api/stock/600900/steady").json
            self.assertTrue(stock["archived"])
            self.assertEqual(stock["signal"]["as_of"], "2026-11-06")
        with patch.object(website, "steady_snapshot", return_value=None):
            self.assertEqual(client.get("/api/steady/pool").status_code, 503)

    def test_entry_plan_expires_once_next_session_opens(self):
        friday = "2026-11-06"
        self.assertFalse(entry_open_passed(friday, datetime(2026, 11, 6, 16, 0)))  # 当天收盘后
        self.assertFalse(entry_open_passed(friday, datetime(2026, 11, 8, 20, 0)))  # 周末
        self.assertFalse(entry_open_passed(friday, datetime(2026, 11, 9, 9, 29)))  # 周一开盘前
        self.assertTrue(entry_open_passed(friday, datetime(2026, 11, 9, 9, 30)))
        self.assertTrue(entry_open_passed(friday, datetime(2026, 11, 10, 8, 0)))

    def test_archived_plan_is_marked_expired_after_next_open(self):
        # 实时日线取不到时退回本地归档；归档是 11-06 的信号，到 11-09 晚上，下一交易日的开盘早已过去
        row = {"code": "600025", "exchange": "sh", "name": "华能水电",
               "selection": {"selected": True, "rank": 35},
               "archived": {"as_of": "2026-11-06", "enough": True, "setup": True}}
        snapshot = {"as_of": "2026-10-30", "members": ["sh600025"], "stocks": {"sh600025": row},
                    "archive_through": "2026-11-06", "sessions": ["2026-11-06"],
                    "yield_floor": 0.03, "turnover_floor": 30_000_000}

        class MondayEvening(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 11, 9, 19, 0, tzinfo=tz)

        client = website.app.test_client()
        with patch.object(website, "steady_snapshot", return_value=snapshot), \
             patch.object(website, "steady_live_bars", return_value=([], "腾讯财经前复权日线")), \
             patch.object(website, "datetime", MondayEvening):
            stock = client.get("/api/stock/600025/steady").json
        self.assertTrue(stock["archived"])
        self.assertTrue(stock["signal"]["entry_passed"])
        self.assertNotIn("entry_passed", row["archived"])  # 快照带缓存，不能改到缓存里的原对象

    def test_live_bars_are_adjusted_with_sina_factors(self):
        bars = rising_bars()
        factors = (("1900-01-01", 1.25), (bars[60]["date"], 1.0))

        def fake_get(url, params=None, headers=None, timeout=8, retries=1):
            if "qfq.js" in url:
                return Mock(text=factor_text("sh600900", factors)), None
            if "gtimg" in url:
                return tencent_response("sh600900", bars), None
            return None, "offline"

        now = datetime(2026, 6, 1, 10, 0, tzinfo=website.CHINA_TZ)
        website._cache.clear()
        with patch.object(website, "http_get", side_effect=fake_get):
            live, source = website.steady_live_bars(1, "600900", now)
        self.assertEqual(len(live), 121)
        self.assertAlmostEqual(live[0]["close"], bars[0]["close"] / 1.25)
        self.assertEqual((live[60]["close"], live[-1]["close"]), (bars[60]["close"], bars[-1]["close"]))
        self.assertEqual(source, "腾讯财经日线，按新浪复权因子等比前复权")
        # 日线缓存过期后重新取日线，复权因子直接用缓存，不再请求新浪
        for key in [key for key in website._cache if key.startswith("steady_bars:")]:
            del website._cache[key]
        with patch.object(website, "http_get", side_effect=fake_get) as mocked:
            self.assertEqual(website.steady_live_bars(1, "600900", now)[0], live)
        self.assertTrue(mocked.called)
        self.assertFalse(any("qfq.js" in call.args[0] for call in mocked.call_args_list))
        # 没有复权因子就不用实时日线，接口会改用本地归档
        website._cache.clear()
        no_factors = lambda url, **kwargs: (None, "offline") if "qfq.js" in url else fake_get(url, **kwargs)
        with patch.object(website, "http_get", side_effect=no_factors):
            self.assertEqual(website.steady_live_bars(1, "600900", now), ([], None))
        website._cache.clear()
