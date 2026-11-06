import os
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from io import BytesIO
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, Mock

import app as market


class MarketTests(unittest.TestCase):
    def setUp(self):
        market._cache.clear()
        market._commentary_cache.clear()

    def test_float_eastmoney_percent_not_rescaled(self):
        response = Mock()
        response.json.return_value = {'data': {'diff': [{
            'f12': '000001', 'f14': '测试公司', 'f2': 12.3,
            'f3': 5.67, 'f7': 2.5, 'f5': 10000,
        }]}}
        with patch.object(market, 'http_get', return_value=(response, None)):
            stock = market.get_popular_stocks(1)[0]
        self.assertEqual(stock['price'], 12.3)
        self.assertEqual(stock['change_pct'], 5.67)
        self.assertEqual(stock['turnover'], 2.5)

    def test_search_falls_back_on_jsonp_or_html(self):
        broken = Mock()
        broken.json.side_effect = ValueError('not JSON')
        fallback = Mock(text='var suggestvalue="贵州茅台,11,600519,sh600519,贵州茅台,,;";')
        with patch.object(market, 'http_get', side_effect=[(broken, None), (fallback, None)]):
            result = market.search_stocks('茅台')
        self.assertEqual(result[0]['code'], '600519')
        self.assertEqual(result[0]['name'], '贵州茅台')

    def test_topics_are_derived_and_ranked_from_fetched_boards(self):
        boards = [dict(name='测试板块甲', code='BK1001', change_pct=1.2, amount=1e9, source='东方财富', kind='board'),
                  dict(name='测试板块乙', code='BK1002', change_pct=6.2, amount=1e9, source='东方财富', kind='board')]
        snapshot = market.build_hot_snapshot(boards, [])
        self.assertEqual(snapshot['hot_topics'], ['测试板块乙', '测试板块甲'])
        self.assertEqual(snapshot['status'], 'partial')
        self.assertNotIn('人工智能', snapshot['hot_topics'])

    def test_ranked_hot_topics_survive_missing_quote_fields(self):
        with patch.object(market, "http_get", return_value=(None, "offline")), \
             patch.object(market, "em_list", side_effect=[([], 0), ([{"f12": "BK0896", "f14": "白酒"}], 1)]):
            boards = market.fetch_trending_boards()
        topic = market.build_hot_snapshot(boards, [])["topics"][0]
        self.assertEqual(topic["name"], "白酒")
        self.assertIsNone(topic["change_pct"])
        self.assertIsNone(topic["amount"])

    def test_total_source_failure_keeps_last_success(self):
        old = {'topics': [{'name': '历史板块'}], 'hot_topics': ['历史板块'], 'news': [],
               'updated_at': '2026-11-04T15:00:00+08:00', 'fetched_at': 1}
        with patch.object(market, '_hot_snapshot', old), \
             patch.object(market, 'fetch_trending_boards', return_value=[]), \
             patch.object(market, 'fetch_market_news', return_value=[]):
            market.refresh_hot_news()
            self.assertEqual(market._hot_snapshot, old)
            self.assertFalse(market._hot_refreshing.is_set())

    def test_partial_failure_retains_topic_timestamp(self):
        old = {'topics': [{'name': '历史板块'}], 'hot_topics': ['历史板块'], 'news': [],
               'updated_at': '2026-11-04T15:00:00+08:00', 'fetched_at': 1}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(market, 'HOT_NEWS_FILE', Path(directory) / 'cache.json'), \
             patch.object(market, '_hot_snapshot', old), \
             patch.object(market, 'fetch_trending_boards', return_value=[]), \
             patch.object(market, 'fetch_market_news', return_value=[{'title': '新资讯', 'url': 'https://example.com'}]):
            market.refresh_hot_news()
            self.assertTrue(market._hot_snapshot['topics_stale'])
            self.assertEqual(market._hot_snapshot['topics_updated_at'], old['updated_at'])
            self.assertEqual(market._hot_snapshot['news'][0]['title'], '新资讯')

    def test_startup_loads_saved_topics_before_partial_refresh(self):
        saved = {"topics": [{"name": "历史板块"}], "hot_topics": ["历史板块"], "news": [],
                 "updated_at": "2026-11-04T15:00:00+08:00", "fetched_at": 1}
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "cache.json"
            file.write_text(json.dumps(saved), encoding="utf-8")
            with patch.object(market, "HOT_NEWS_FILE", file), \
                 patch.object(market, "_hot_snapshot", None), \
                 patch.object(market, "fetch_trending_boards", return_value=[]), \
                 patch.object(market, "fetch_market_news", return_value=[{"title": "新资讯"}]):
                market.refresh_hot_news()
                self.assertEqual(market._hot_snapshot["hot_topics"], ["历史板块"])
                self.assertTrue(market._hot_snapshot["topics_stale"])

    def test_reading_uses_matching_dates_and_distinguishes_rules(self):
        info = dict(name='测试股票', price=12, change_pct=2, high=12.4, low=11.8,
                    quote_time='2026-11-03 15:00:00')
        bars = [dict(close=10, volume=100, date=f'2026-10-{day:02d}') for day in range(1, 26)]
        bars.append(dict(close=999, volume=99999, date='2026-11-04'))
        reading = market.make_stock_reading(info, bars)
        self.assertEqual(reading['mode'], 'rules')
        self.assertIn('MA5 10.00 / MA20 10.00', str(reading['points']))
        self.assertIn('尚未接入 AI', reading['notice'])
        self.assertNotIn('999', reading['summary'])

    def test_missing_quotes_do_not_produce_analysis(self):
        result = market.make_stock_reading({'price': 0}, [])
        self.assertEqual(result['mode'], 'unavailable')
        self.assertFalse(result['points'])

    def test_commentary_does_not_call_model_without_configuration(self):
        info = dict(name='测试', price=10, change_pct=0, quote_time='2026-11-05 15:00:00')
        with patch.dict(os.environ, {'STOCK_AI_API_KEY':'', 'STOCK_AI_MODEL':''}), \
             patch.object(market, 'get_stock_info', return_value=info), \
             patch.object(market, 'get_kline', return_value=[]), \
             patch.object(market.requests, 'post') as post:
            response = market.app.test_client().get('/api/stock/000001/commentary')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['mode'], 'rules')
        post.assert_not_called()

    def test_ai_failure_returns_labeled_reading(self):
        info = dict(name='测试', price=10, change_pct=0, quote_time='2026-11-05 15:00:00')
        with patch.dict(os.environ, {'STOCK_AI_API_KEY':'test-only', 'STOCK_AI_MODEL':'test-model'}), \
             patch.object(market, 'get_stock_info', return_value=info), \
             patch.object(market, 'get_kline', return_value=[]), \
             patch.object(market.requests, 'post', side_effect=RuntimeError('unavailable')):
            response = market.app.test_client().get('/api/stock/000001/commentary')
        self.assertEqual(response.json['mode'], 'rules')
        self.assertIn('AI 服务暂不可用', response.json['notice'])

    def test_invalid_stock_code_is_rejected(self):
        response = market.app.test_client().get('/api/stock/not-a-stock/commentary')
        self.assertEqual(response.status_code, 400)


    def test_catalogue_paginates_and_deduplicates(self):
        rows = [{"f12": f"BK{n:04d}", "f14": "白酒" if n == 209 else f"板块{n}"}
                for n in range(210)]
        with patch.object(market, "em_list", side_effect=lambda fs, page, size, fields, sort:
                          (rows[(page - 1) * size:page * size], len(rows))) as upstream:
            result = market.search_concepts("白酒")
            self.assertEqual(result[0]["code"], "BK0209")
            self.assertEqual(upstream.call_count, 3)
            self.assertEqual(len(market.get_all_concepts()), 210)
            self.assertEqual(upstream.call_count, 3)

    def test_incomplete_catalogue_is_not_cached(self):
        row = {"f12": "BK0896", "f14": "白酒"}
        with patch.object(market, "em_list", side_effect=lambda fs, page, **opts:
                          ([row], 110) if page == 1 else ([], 0)), \
             patch.object(market, "fetch_concept_directory", return_value=[]):
            self.assertEqual(market.get_all_concepts(), [{"code": "BK0896", "name": "白酒"}])
            self.assertIsNone(market._cache_get("concepts:all"))

    def test_match_filters_ineligible_securities_and_batches_only_displayed_rows(self):
        stocks = [{"code": "399997", "name": "指数"}, {"code": "161725", "name": "基金"}]
        stocks += [{"code": f"600{i:03d}", "name": f"股票{i}"} for i in range(32)]
        with patch.object(market, "search_concepts", return_value=[]), \
             patch.object(market, "search_stocks", return_value=stocks), \
             patch.object(market, "batch_quotes", return_value={}) as batch:
            result = market.match_query_to_stocks("白酒")
        self.assertEqual(result["total_related"], 32)
        self.assertEqual(len(result["stocks"]), 20)
        self.assertEqual(len(batch.call_args.args[0]), 20)

    def test_html_kline_failure_is_not_cached_and_volume_is_in_lots(self):
        bad = Mock()
        bad.json.side_effect = ValueError("HTML")
        good = Mock()
        good.json.return_value = [{"day": "2026-11-05", "open": 1446.7, "close": 1452,
                                  "high": 1466, "low": 1430, "volume": 4671220}]
        with patch.object(market, "http_get", side_effect=[(bad, None), (good, None)]) as upstream:
            self.assertEqual(market.get_kline(1, "600519"), [])
            self.assertEqual(market.get_kline(1, "600519")[0]["volume"], 46712)
            self.assertEqual(upstream.call_count, 2)

    def test_trends_preserve_lots_and_only_latest_session(self):
        response = Mock()
        response.json.return_value = {"data": {"preClose": 10, "trends": [
            "2026-11-04 15:00,10,10,10,10,13,13000,10.01",
            "2026-11-05 09:31,10,10.5,10.5,10,20,20700,10.35",
        ]}}
        with patch.object(market, "http_get", return_value=(response, None)) as upstream:
            data = market.get_intraday(1, "600519")
        self.assertEqual(upstream.call_count, 1)
        self.assertEqual(data["trade_date"], "2026-11-05")
        self.assertEqual(data["minutes"][0]["volume"], 20)
        self.assertEqual(data["minutes"][0]["avg_price"], 10.35)

    def test_cni_sample_uses_latest_date_and_retains_leading_zeroes(self):
        from openpyxl import Workbook
        book = Workbook()
        sheet = book.active
        sheet.append(["日期", "样本代码", "样本简称"])
        sheet.append(["2026-10-30", 2, "旧样本"])
        sheet.append(["2026-11-05", 1, "新样本"])
        buffer = BytesIO()
        book.save(buffer)
        self.assertEqual(market.parse_cni_samples(buffer.getvalue()), ({"000001": "新样本"}, "2026-11-05"))

    def test_news_rejects_non_article_jsonp_and_does_not_cache_failure(self):
        bad = Mock(text='callback({"result":{"passportWeb":[]}});')
        good = Mock(text='callback({"result":{"cmsArticleWebOld":[{"title":"<em>白酒</em>资讯","url":"https://example.com/article"}]}});')
        for response in (bad, good):
            response.json.side_effect = ValueError("JSONP")
        with patch.object(market, "http_get", side_effect=[(bad, None), (good, None)]):
            with self.assertRaises(market.SourceUnavailable):
                market.search_eastmoney_news("白酒")
            self.assertEqual(market.search_eastmoney_news("白酒")[0]["title"], "白酒资讯")

    def test_flat_rsi_and_invalid_route_parameters(self):
        self.assertEqual(market.calc_rsi([12] * 20)[-1], 50)
        with market.app.test_client() as client:
            self.assertEqual(client.get("/api/stock/600519/chart?period=invalid").status_code, 400)
            self.assertEqual(client.get("/api/index/BK0896/constituents").status_code, 400)

    def test_commentary_merges_inflight_generation(self):
        started, release = Event(), Event()
        def generate(*args):
            started.set()
            if not release.wait(2):
                raise TimeoutError
            return {"mode": "ai", "summary": "测试"}
        with patch.object(market, "_generate_stock_commentary", side_effect=generate) as provider, \
             ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(market.get_stock_commentary, 1, "600519")
            self.assertTrue(started.wait(1))
            second = pool.submit(market.get_stock_commentary, 1, "600519")
            release.set()
            self.assertEqual(first.result()["summary"], second.result()["summary"])
            self.assertEqual(provider.call_count, 1)


if __name__ == '__main__':
    unittest.main()
