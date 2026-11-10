"""我的持仓：输入校验、文件存储、接口和持仓分析。"""
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import app as market
import backtest_picker as picker
from holdings import HoldingFileError, HoldingStore, parse_holding
from test_picker import WAVY, factor_text, make_bars, tencent_response


def sina_quote_text(symbol, name, preclose, price):
    fields = [name] + ["0"] * 31
    fields[2], fields[3], fields[30], fields[31] = str(preclose), str(price), "2026-11-09", "15:00:00"
    return f'var hq_str_{symbol}="{",".join(fields)}";'


class ParseHoldingTests(unittest.TestCase):
    TODAY = date(2026, 11, 9)

    def test_valid_record_is_cleaned(self):
        record = parse_holding({"code": " 600519 ", "buy_date": "2026-10-30", "buy_price": "1392.5",
                                "shares": "100", "name": "  贵州  茅台 "}, today=self.TODAY)
        self.assertEqual(record, {"code": "600519", "name": "贵州 茅台", "buy_date": "2026-10-30",
                                  "buy_price": 1392.5, "shares": 100})

    def test_optional_fields_may_be_blank(self):
        record = parse_holding({"code": "000001", "buy_date": "2026-11-09", "buy_price": "", "shares": None},
                               today=self.TODAY)
        self.assertEqual((record["buy_price"], record["shares"]), (None, None))

    def test_invalid_input_explains_the_problem(self):
        cases = [
            ({"code": "830799", "buy_date": "2026-11-02"}, "沪深 A 股"),
            ({"code": "600519", "buy_date": "2026-11-10"}, "不能晚于今天"),
            ({"code": "600519", "buy_date": "2026/11/02"}, "YYYY-MM-DD"),
            ({"code": "600519", "buy_date": "2026-11-02", "buy_price": "-1"}, "大于 0"),
            ({"code": "600519", "buy_date": "2026-11-02", "buy_price": "abc"}, "数字"),
            ({"code": "600519", "buy_date": "2026-11-02", "shares": "1.5"}, "正整数"),
            (["not", "a", "dict"], "格式"),
        ]
        for payload, message in cases:
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, message):
                parse_holding(payload, today=self.TODAY)


class HoldingStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "data" / "holdings.json"
        self.store = HoldingStore(self.path)

    def test_add_remove_and_persist(self):
        self.assertEqual(self.store.load(), [])
        entry = self.store.add({"code": "600519", "name": "贵州茅台", "buy_date": "2026-10-30"})
        self.assertRegex(entry["id"], r"^[0-9a-f]{32}$")
        self.assertEqual([r["code"] for r in HoldingStore(self.path).load()], ["600519"])  # 重新打开文件，记录还在
        self.assertTrue(self.store.remove(entry["id"]))
        self.assertFalse(self.store.remove(entry["id"]))
        self.assertEqual(self.store.load(), [])

    def test_corrupted_file_is_never_overwritten(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{oops", encoding="utf-8")
        with self.assertRaises(HoldingFileError):
            self.store.add({"code": "600519"})
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{oops")


class HoldingsApiTests(unittest.TestCase):
    def setUp(self):
        market._cache.clear()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = HoldingStore(Path(directory.name) / "holdings.json")
        patcher = patch.object(market, "holding_store", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = market.app.test_client()

    @staticmethod
    def fake_get(url, params=None, headers=None, timeout=8, retries=1):
        if "sinajs" in url:  # 新浪实时行情：昨收 1400，现价 1450
            return Mock(text=sina_quote_text("sh600519", "贵州茅台", 1400, 1450)), None
        if "getKLineData" in url:  # 新浪不复权日线，用来估算买入价
            bars = [{"day": "2026-10-29", "open": "1380", "high": "1400", "low": "1375", "close": "1390", "volume": "100"},
                    {"day": "2026-10-30", "open": "1390", "high": "1399", "low": "1385", "close": "1392.05", "volume": "100"}]
            return Mock(json=Mock(return_value=bars)), None
        if "gtimg" in url:  # 腾讯不复权日线，用来算策略信号
            return tencent_response(params["param"].split(",")[0], WAVY), None
        if "qfq.js" in url:  # 新浪前复权因子
            return Mock(text=factor_text("sh600519")), None
        return None, "offline"

    def test_add_estimates_missing_price_and_rejects_forms(self):
        with patch.object(market, "http_get", side_effect=self.fake_get):
            response = self.client.post("/api/holdings", json={"code": "600519", "buy_date": "2026-11-01", "shares": 100})
        self.assertEqual(response.status_code, 201)
        entry = response.get_json()
        # 11-01 是周六，按之前最近一个交易日 10-30 的收盘价估算
        self.assertEqual((entry["name"], entry["buy_price"], entry["price_estimated_on"]), ("贵州茅台", 1392.05, "2026-10-30"))
        self.assertEqual(entry["price_estimate"], "close")
        self.assertEqual(self.client.post("/api/holdings", data={"code": "600519"}).status_code, 415)
        self.assertEqual(self.client.post("/api/holdings", json={"code": "600519", "buy_date": "2099-01-01"}).status_code, 400)

    def test_buy_today_uses_live_price_not_previous_close(self):
        # 新浪日线收盘后才有当天这一根；当天买入要用实时现价，不能拿到上一个交易日的收盘价
        with patch.object(market, "http_get", side_effect=self.fake_get):
            response = self.client.post("/api/holdings", json={"code": "600519", "buy_date": "2026-11-09"})
        entry = response.get_json()
        self.assertEqual((entry["buy_price"], entry["price_estimate"], entry["price_estimated_on"]), (1450, "live", "2026-11-09"))

    def test_overview_combines_quotes_and_signals_then_delete(self):
        entry = self.store.add({"code": "600519", "name": "贵州茅台", "buy_date": "2026-10-30", "buy_price": 1400.0,
                                "shares": 200, "price_estimated_on": None})
        with patch.object(market, "http_get", side_effect=self.fake_get):
            data = self.client.get("/api/holdings").get_json()
        record = data["records"][0]
        self.assertEqual(record["price"], 1450)
        self.assertAlmostEqual(record["return_rate"], 1450 / 1400 - 1)
        self.assertAlmostEqual(record["profit"], 50 * 200)
        self.assertTrue(data["signals"]["600519"]["adjusted"])
        self.assertEqual(set(data["signals"]["600519"]["signals"]), {"ma_cross", "momentum"})
        self.assertEqual(self.client.delete(f"/api/holdings/{entry['id']}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/holdings/{entry['id']}").status_code, 404)
        self.assertEqual(self.client.delete("/api/holdings/not-an-id").status_code, 400)

    def test_corrupted_file_reports_error(self):
        self.store.path.write_text("not json", encoding="utf-8")
        response = self.client.get("/api/holdings")
        self.assertEqual(response.status_code, 500)
        self.assertIn("持仓文件", response.get_json()["error"])

    def test_suspended_stock_uses_previous_close(self):
        text = sina_quote_text("sz000001", "平安银行", 11.5, 0)  # 现价 0：停牌或未开盘
        with patch.object(market, "http_get", return_value=(Mock(text=text), None)):
            quote = market.sina_quotes(["000001"])["000001"]
        self.assertEqual((quote["price"], quote["price_is_preclose"]), (11.5, True))

    def test_latest_signals_need_enough_bars(self):
        self.assertIsNone(picker.latest_signals(make_bars([10] * 20)))
        self.assertEqual(set(picker.latest_signals(WAVY)), {"ma_cross", "momentum"})


if __name__ == "__main__":
    unittest.main()
