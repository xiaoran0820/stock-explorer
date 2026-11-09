import unittest
from collections import OrderedDict
from datetime import date
from unittest.mock import Mock, patch

import app
import company_research as research


def disclosure(code, day, doc, title):
    return {"secCode": code, "adjunctUrl": f"finalpage/{day}/{doc}.PDF", "announcementTitle": title}


def response(**body):
    return Mock(json=Mock(return_value=body))


class CompanyResearchTests(unittest.TestCase):
    def setUp(self):
        self.start, self.end = date(2025, 11, 1), date(2026, 2, 1)

    def test_window_validation(self):
        self.assertEqual(research.parse_window(None, None, today=self.end), (date(2025, 11, 4), self.end))
        for start, end in (("2025-12-01", "2025-11-01"), (None, "0001-01-01"),
                           ("2024-01-01", "2026-02-01"), (None, "2026-02-02"), ("2025-11-32", None)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                research.parse_window(start, end, today=self.end)

    def test_media_retries_bad_feed_and_checks_shanghai_dates(self):
        rss = b"""<rss><channel>
        <item><title>Start - Publisher</title><source>Publisher</source>
          <link>https://example.com/start</link><pubDate>Fri, 31 Oct 2025 16:00:00 GMT</pubDate></item>
        <item><title>Too late</title><link>https://example.com/late</link>
          <pubDate>Sun, 01 Feb 2026 16:00:00 GMT</pubDate></item>
        <item><title>Unsafe link</title><link>javascript:alert(1)</link>
          <pubDate>Wed, 03 Dec 2025 11:00:00 GMT</pubDate></item>
        </channel></rss>"""
        get = Mock(side_effect=[(Mock(content=b"<html>blocked</html>"), None), (Mock(content=rss), None)])
        results = research.google_history(get, "贵州茅台", self.start, self.end)
        self.assertEqual(results["status"], "ok")
        self.assertIn("after:2025-11-01 before:2026-02-01", get.call_args.kwargs["params"]["q"])
        self.assertEqual([(n["title"], n["date"], n["source"]) for n in results["items"]],
                         [("Start", "2025-11-01", "Publisher")])

    def test_announcements_keep_valid_results_after_failed_page(self):
        row = disclosure("600519", "2025-11-03", 1, "董事会决议")
        unrelated = disclosure("600000", "2025-11-03", 2, "其他股票公告")
        out_of_range = disclosure("600519", "2026-02-02", 3, "日期不符")
        post = Mock(side_effect=[response(announcements=[row, unrelated, out_of_range], hasMore=True), None])
        results = research.historical_announcements(post, "600519", 1, "org", "贵州茅台", self.start, self.end)
        self.assertEqual([call.args[1]["pageNum"] for call in post.call_args_list], [1, 2])
        self.assertEqual(results["status"], "partial")
        self.assertEqual([n["title"] for n in results["items"]], ["董事会决议"])

    def test_name_fallback_filters_wrong_codes_and_marks_coverage(self):
        row = disclosure("600519", "2025-11-03", 1, "董事会决议")
        wrong = disclosure("600000", "2025-11-03", 2, "同名匹配")
        post = Mock(side_effect=[None, response(announcements=[wrong], hasMore=True), response(announcements=[row], hasMore=False)])
        results = research.historical_announcements(post, "600519", 1, "org", "贵州茅台", self.start, self.end)
        self.assertEqual(post.call_args.args[1]["searchkey"], "贵州茅台")
        self.assertEqual(results["status"], "partial")
        self.assertEqual([n["title"] for n in results["items"]], ["董事会决议"])

    def test_financial_versions_prefer_available_rows_and_keep_reported_zero(self):
        old = {"SECURITY_CODE": "600519", "REPORT_DATE": "2025-09-30", "NOTICE_DATE": "2025-10-30",
               "UPDATE_DATE": "2025-10-31", "TOTALOPERATEREVE": 0, "PARENTNETPROFIT": "-", "CURRENCY": "CNY"}
        revised = {**old, "UPDATE_DATE": "2026-04-01", "TOTALOPERATEREVE": 120}
        later = {**old, "REPORT_DATE": "2025-12-31", "NOTICE_DATE": "2026-04-01", "TOTALOPERATEREVE": 180}
        reports = research.normalize_reports([old, revised, later], "600519", "SH600519")
        chosen, = research.report_as_of(reports, self.end)
        self.assertEqual(chosen["revenue"], 0)
        self.assertIsNone(chosen["net_profit"])
        self.assertFalse(chosen["updated_after_cutoff"])
        chosen, = research.report_as_of(research.normalize_reports([revised, later], "600519", "SH600519"), self.end)
        self.assertEqual(chosen["revenue"], 120)
        self.assertTrue(chosen["updated_after_cutoff"])

    def test_research_route_combines_sources_and_links_report_outside_news_window(self):
        report = {"SECURITY_CODE": "600519", "REPORT_DATE": "2025-09-30", "NOTICE_DATE": "2025-10-30",
                  "CURRENCY": "CNY", "TOTALOPERATEREVE": 200e8, "PARENTNETPROFIT": 40e8,
                  "TOTALOPERATEREVETZ": 5, "PARENTNETPROFITTZ": -5, "NETCASH_OPERATE_PK": 35e8}
        def get(url, **kwargs):
            if url.endswith("/CompanySurveyAjax"):
                return response(jbzl={"agdm": "600519", "agjc": "贵州茅台", "gswz": "www.moutaichina.com"}), None
            if url == research.DATA_CENTER:
                return response(result={"data": [report]}), None
            if url == research.CNINFO_DIRECTORY:
                return response(stockList=[{"code": "600519", "orgId": "org1", "zwjc": "贵州茅台"}]), None
            return Mock(content=b"<rss><channel/></rss>"), None

        def post(url, data):
            if data["seDate"] == "2025-10-30~2025-10-30":
                return response(announcements=[
                    disclosure("600519", "2025-10-30", 1, "贵州茅台2025年三季度报告摘要"),
                    disclosure("600519", "2025-10-30", 2, "贵州茅台2025年三季度报告")])
            return response(announcements=[disclosure("600519", "2025-12-03", 3, "贵州茅台签署新项目合同")])

        with patch.object(app, "_cache", OrderedDict()), patch.object(app, "http_get", get), \
             patch.object(app, "post_research_form", post), app.app.test_client() as client:
            res = client.get("/api/stock/600519/research?start=2025-11-01&end=2026-02-01")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data["company"]["website"], "https://www.moutaichina.com")
            self.assertEqual(data["financial"]["filing_url"], research.CNINFO_PDF + "finalpage/2025-10-30/2.PDF")
            self.assertEqual([item["date"] for item in data["timeline"]], ["2025-12-03"])
            self.assertEqual(data["sources"]["media"], "empty")
            self.assertIn("不足以确认该事项的影响", data["insights"][-1]["text"])
            self.assertEqual(len(data["insights"][-1]["refs"]), 2)
            self.assertEqual(client.get("/api/stock/399001/research").status_code, 400)

    def test_window_errors_explain_the_problem(self):
        # 之前开始日期晚于截止日期时，会提示"结束日期不能晚于今天"
        cases = {("2025-12-01", "2025-11-01"): "开始日期不能晚于截止日期",
                 (None, "2026-02-02"): "截止日期不能晚于今天",
                 ("2025-11-32", None): "YYYY-MM-DD", ("2024-01-01", "2026-02-01"): "730"}
        for (start, end), message in cases.items():
            with self.subTest(start=start, end=end), self.assertRaisesRegex(ValueError, message):
                research.parse_window(start, end, today=self.end)
