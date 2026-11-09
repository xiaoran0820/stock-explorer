"""Public company profiles, dated disclosures and evidence-based research."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from math import isfinite
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup

SHANGHAI = timezone(timedelta(hours=8))
F10 = "https://emweb.securities.eastmoney.com/PC_HSF10"
DATA_CENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"
CNINFO_DIRECTORY = "https://www.cninfo.com.cn/new/data/szse_stock.json"
CNINFO_QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_PDF = "https://static.cninfo.com.cn/"
GOOGLE_NEWS = "https://news.google.com/rss/search"
SINA_NEWS = "https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/{symbol}.phtml"
PAGE_SIZE = 20
MAX_DISCLOSURE_PAGES = 3
MAX_NEWS_WINDOW = 730
EVENTS = (
    (r"定期报告|年度报告|半年度报告|季度报告|季报|业绩|经营数据", "业绩披露", "在财报原文核对收入、利润和现金流的变动解释"),
    (r"签署|合同|订单|合作|投产|扩产|产能|新建|中标|交付|收购|并购", "业务进展", "关注项目落地、收入确认及经营现金流变化"),
    (r"回购|分红|权益分派|减持|增持|定增|发行股份|融资", "股东与融资事项", "在正式公告核对金额、实施条件和进展"),
    (r"风险提示|处罚|诉讼|停产|召回|调查|问询", "风险事项", "核对涉及范围、最新进展及公司披露的财务影响"),
)


def clean(value, limit=360):
    raw = str(value or "")
    value = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True) if "<" in raw and ">" in raw else unescape(raw)
    return " ".join(value.split())[:limit]


def number(value):
    try:
        parsed = float(value)
        return parsed if isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def as_date(value):
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_window(start, end, today=None):
    today = today or datetime.now(SHANGHAI).date()
    earliest = date(1990, 1, 1)
    def parse(raw, default):
        if raw is None:
            return default
        try:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw):
                raise ValueError
            return date.fromisoformat(raw)
        except ValueError:
            raise ValueError("请输入有效的起止日期（YYYY-MM-DD）") from None
    # 每种错误给出对应的提示（之前开始日期晚于截止日期时，会提示"结束日期不能晚于今天"）
    last = parse(end, today)
    if last > today:
        raise ValueError("截止日期不能晚于今天")
    if last < earliest:
        raise ValueError("日期不能早于 1990-01-01")
    first = parse(start, last - timedelta(days=89))
    if first < earliest:
        raise ValueError("日期不能早于 1990-01-01")
    if first > last:
        raise ValueError("开始日期不能晚于截止日期")
    if (last - first).days + 1 > MAX_NEWS_WINDOW:
        raise ValueError("单次最多查询 730 天，请缩小日期范围")
    return first, last


def public_url(value, add_scheme=False):
    raw = str(value or "").strip()
    if not raw or raw in {"--", "—"}:
        return None
    if add_scheme and "://" not in raw and not raw.startswith(("/", ".")):
        raw = "https://" + raw
    try:
        parsed = urlsplit(raw)
        if (parsed.scheme in {"http", "https"} and parsed.hostname and "." in parsed.hostname
                and not parsed.username and not parsed.password and not re.search(r"[\s\\<>]", raw)):
            return raw
    except ValueError:
        pass
    return None


def read_json(response):
    if response is not None:
        try:
            body = response.json()
            if isinstance(body, dict):
                return body
        except ValueError:
            pass
    return {}


def feed(items=None, status=None, truncated=False):
    items = items or []
    return {"items": items, "status": status or ("ok" if items else "empty"), "truncated": truncated}


def company_profile(get, code, symbol):
    source_url = f"{F10}/CompanySurvey/Index?code={symbol}&type=web"
    response, _ = get(f"{F10}/CompanySurvey/CompanySurveyAjax", params={"code": symbol}, timeout=6)
    body = read_json(response)
    details = body.get("jbzl")
    if not isinstance(details, dict) or str(details.get("agdm") or body.get("SecurityCode")) != code:
        return None
    short_name = clean(details.get("agjc") or body.get("SecurityShortName"), 40)
    if not short_name:
        return None
    return {"name": clean(details.get("gsmc"), 100) or short_name, "short_name": short_name,
            "industry": clean(details.get("sshy"), 70), "business": clean(details.get("gsjj") or details.get("jyfw")),
            "website": public_url(details.get("gswz"), add_scheme=True),
            "source": "东方财富公司资料", "source_url": source_url}


REPORT_FIELDS = {
    "revenue": "TOTALOPERATEREVE", "net_profit": "PARENTNETPROFIT",
    "revenue_yoy": "TOTALOPERATEREVETZ", "profit_yoy": "PARENTNETPROFITTZ",
    "operating_cash": "NETCASH_OPERATE_PK", "cash_per_share": "MGJYXJJE",
    "cash_per_share_yoy": "MGJYXJJETZ", "gross_margin": "XSMLL",
    "debt_ratio": "ZCFZL", "roe": "ROEJQ",
}


def normalize_reports(rows, code, symbol):
    if not isinstance(rows, list):
        return []
    source_url = f"{F10}/NewFinanceAnalysis/Index?code={symbol}&type=web"
    reports = {}
    for row in rows:
        if not isinstance(row, dict) or str(row.get("SECURITY_CODE")) != code:
            continue
        period, notice, updated = map(as_date, (row.get("REPORT_DATE"), row.get("NOTICE_DATE"), row.get("UPDATE_DATE")))
        if not period or not notice or notice < period:
            continue
        available = max(notice, updated) if updated else notice
        entry = {key: number(row.get(field)) for key, field in REPORT_FIELDS.items()}
        if not any(entry[k] is not None for k in ("revenue", "net_profit", "operating_cash", "cash_per_share")):
            continue
        entry.update(period=period.isoformat(), disclosed_on=notice.isoformat(),
                     available_on=available.isoformat(), period_name=clean(row.get("REPORT_DATE_NAME"), 30),
                     currency=clean(row.get("CURRENCY"), 8) or None,
                     company_name=clean(row.get("SECURITY_NAME_ABBR"), 40),
                     source="东方财富财务分析", source_url=source_url)
        reports[(period, available)] = entry
    return [reports[key] for key in sorted(reports, reverse=True)]


def financial_reports(get, code, symbol):
    response, _ = get(DATA_CENTER, params={
        "reportName": "RPT_F10_FINANCE_MAINFINADATA", "columns": "ALL",
        "filter": f'(SECURITY_CODE="{code}")', "sortColumns": "REPORT_DATE", "sortTypes": -1,
        "pageSize": 10, "source": "WEB", "client": "WEB",
    }, timeout=7)
    result = read_json(response).get("result")
    reports = normalize_reports(result.get("data"), code, symbol) if isinstance(result, dict) else []
    if reports:
        return reports
    response, _ = get(f"{F10}/NewFinanceAnalysis/ZYZBAjaxNew", params={"code": symbol, "type": 0}, timeout=6)
    return normalize_reports(read_json(response).get("data"), code, symbol)


def company_directory(get):
    response, _ = get(CNINFO_DIRECTORY, timeout=7)
    rows = read_json(response).get("stockList")
    if not isinstance(rows, list):
        return {}
    return {str(row["code"]): (str(row["orgId"]), clean(row.get("zwjc"), 40))
            for row in rows if isinstance(row, dict) and re.fullmatch(r"[0-9]{6}", str(row.get("code")))
            and re.fullmatch(r"[a-zA-Z0-9]{1,32}", str(row.get("orgId")))}


def classify(title):
    for pattern, category, followup in EVENTS:
        if re.search(pattern, title):
            return category, followup
    return "公司动态", "关注后续正式披露，核对事件是否已实施及其影响"


def announcement_rows(body, code, start, end, broad=False):
    rows = body.get("announcements")
    total = body.get("totalAnnouncement", body.get("totalRecordNum"))
    if isinstance(rows, list):
        # CNInfo returns null announcements when a valid query finds zero results.
        pass
    elif rows is None and total == 0:
        rows = []
    else:
        return None
    items = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("secCode")) != code:
            continue
        path = str(row.get("adjunctUrl") or "")
        match = re.fullmatch(r"finalpage/([0-9]{4}-[0-9]{2}-[0-9]{2})/[0-9]+\.[Pp][Dd][Ff]", path)
        title = clean(row.get("announcementTitle"), 220)
        published = as_date(match[1]) if match else None
        if not published or not start <= published <= end or not title:
            continue
        items.append({"title": title, "date": published.isoformat(), "url": CNINFO_PDF + path,
                      "source": "巨潮资讯", "kind": "disclosure", "category": classify(title)[0]})
    # A populated response with no matching rows may be a proxy/error payload for another query.
    return items if broad or not rows or items else None


def fetch_announcements(post, code, market, start, end, org=None, name=None):
    broad = not org
    output, more = [], False
    for page in range(1, MAX_DISCLOSURE_PAGES + 1):
        response = post(CNINFO_QUERY, {
            "stock": f"{code},{org}" if org else "", "column": "sse" if market else "szse",
            "pageNum": page, "pageSize": PAGE_SIZE, "tabName": "fulltext", "plate": "",
            "seDate": f"{start.isoformat()}~{end.isoformat()}", "searchkey": name if broad else "",
            "sortName": "time", "sortType": "desc", "isHLtitle": "false",
        })
        body = read_json(response)
        page_items = announcement_rows(body, code, start, end, broad=broad)
        if page_items is None:
            return feed(dedupe(output), "partial" if output else "unavailable", bool(output))
        known = {item["url"] for item in output}
        if page_items and all(item["url"] in known for item in page_items):
            return feed(dedupe(output), "partial", True)
        output.extend(page_items)
        total = number(body.get("totalAnnouncement", body.get("totalRecordNum")))
        more = body.get("hasMore") is True or (total is not None and total > PAGE_SIZE * page)
        if not more:
            break
    return feed(dedupe(output), ("partial" if output else "unavailable") if broad else None, truncated=more)


def historical_announcements(post, code, market, org, name, start, end):
    records = fetch_announcements(post, code, market, start, end, org=org) if org else feed(status="unavailable")
    if records["status"] == "unavailable" and name:
        return fetch_announcements(post, code, market, start, end, name=name)
    return records


def parse_rss(response, start, end):
    if response is None or not response.content or len(response.content) > 2_000_000:
        return feed(status="unavailable")
    try:
        root = ElementTree.fromstring(response.content)
    except ElementTree.ParseError:
        return feed(status="unavailable")
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        return feed(status="unavailable")
    raw = channel.findall("item")
    output, recognized = [], 0
    for row in raw:
        try:
            published = parsedate_to_datetime(row.findtext("pubDate", ""))
            published = (published if published.tzinfo else published.replace(tzinfo=timezone.utc)).astimezone(SHANGHAI).date()
        except (TypeError, ValueError, OverflowError):
            continue
        source = clean(row.findtext("source"), 50) or "媒体报道"
        title = clean(row.findtext("title"), 220).removesuffix(" - " + source).strip()
        link = public_url(row.findtext("link"))
        if title and link:
            recognized += 1
            if start <= published <= end:
                output.append({"title": title, "date": published.isoformat(), "url": link,
                               "source": source, "via": "Google 新闻索引", "kind": "media", "category": classify(title)[0]})
    return feed(dedupe(output), "unavailable" if raw and not recognized else None, truncated=len(raw) >= 100)


def google_history(get, name, start, end):
    if not name:
        return feed(status="unavailable")
    # Query a slightly wider window to cover time zone boundaries, then enforce Shanghai dates locally.
    for first, last in ((start - timedelta(days=1), end + timedelta(days=1)), (start, end)):
        query = f'"{name.replace(chr(34), "")}" after:{first} before:{last}'
        response, _ = get(GOOGLE_NEWS, params={"q": query, "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"}, timeout=7)
        records = parse_rss(response, start, end)
        if records["status"] != "unavailable":
            return records
    return records


def sina_latest(get, symbol, name, code, start, end):
    response, _ = get(SINA_NEWS.format(symbol=symbol.lower()), timeout=6)
    if response is None:
        return feed(status="unavailable")
    response.encoding = "gb18030"
    listing = BeautifulSoup(response.text, "html.parser").select_one(".datelist")
    if listing is None:
        return feed(status="unavailable")
    output = []
    for link in listing.select("a[href]"):
        stamp = re.search(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", str(link.previous_sibling or ""))
        published = as_date(stamp[0]) if stamp else None
        title = link.get_text(" ", strip=True)[:220]
        url = public_url(link["href"])
        if (published and start <= published <= end and url and title
                and ((name and name in title) or re.search(rf"(?<![0-9]){code}(?![0-9])", title))):
            output.append({"title": title, "date": published.isoformat(), "url": url, "source": "新浪财经",
                           "kind": "media", "category": classify(title)[0]})
    return feed(dedupe(output))


def dedupe(items):
    seen_urls, seen_titles, result = set(), set(), []
    for item in sorted(items, key=lambda row: row["date"], reverse=True):
        title = (item["kind"], item["date"], re.sub(r"[\W_]+", "", item["title"]).lower())
        if item["url"] not in seen_urls and title not in seen_titles:
            seen_urls.add(item["url"])
            seen_titles.add(title)
            result.append(item)
    return result


def report_as_of(reports, cutoff):
    day = cutoff.isoformat()
    selected = {}
    for row in reports:
        if row["disclosed_on"] > day:
            continue
        later_update = row["available_on"] > day
        previous = selected.get(row["period"])
        # Prefer a version known by cutoff; otherwise show the platform's current row with a date label.
        if previous is None or (not later_update, row["available_on"]) > (not previous["updated_after_cutoff"], previous["available_on"]):
            selected[row["period"]] = {**row, "updated_after_cutoff": later_update}
    return sorted(selected.values(), key=lambda row: row["period"], reverse=True)


def matching_filing(report, notices):
    term = {"03-31": r"(?:第一|一)季度报告|一季报", "06-30": r"半年度报告|半年报",
            "09-30": r"(?:第三|三)季度报告|三季报", "12-31": r"年度报告|年报"}.get(report["period"][5:])
    return next((item for item in notices if term and item["date"] == report["disclosed_on"]
                 and re.search(rf"{report['period'][:4]}\s*年\s*(?:{term})", item["title"])
                 and not re.search(r"摘要|提示性|问询|更正|修订|英文|说明", item["title"])), None)


def format_money(amount, currency):
    if amount is None:
        return "暂无数据"
    if currency == "CNY":
        return f"{amount / 100_000_000:,.2f} 亿元"
    return f"{amount:,.2f} {currency or '原币'}"


def analyze_company(reports, timeline, short_name):
    insights = []
    latest = reports[0] if reports else None
    if latest:
        refs = [{"label": latest["source"], "url": latest["source_url"]}]
        if latest.get("filing_url"):
            refs.append({"label": "财报 PDF", "url": latest["filing_url"]})
        revenue, profit = latest["revenue_yoy"], latest["profit_yoy"]
        text = f"截至 {latest['period']}，累计营业总收入 {format_money(latest['revenue'], latest['currency'])}，归母净利润 {format_money(latest['net_profit'], latest['currency'])}。"
        if revenue is not None and profit is not None:
            text += f"两项同比分别为 {revenue:+.2f}% 和 {profit:+.2f}%。"
            if revenue > 0 > profit:
                text += "收入增长未同步转化为归母利润增长，可在报告中核对毛利、费用和非经常性损益。"
            elif profit > 0 > revenue:
                text += "收入与利润变化方向不同，可核对成本、业务结构及一次性项目。"
            elif revenue > 0 and profit > 0:
                text += "收入与利润同比均增长，后续可核对增长是否伴随经营现金流改善。"
            elif revenue < 0 and profit < 0:
                text += "两项同比均下降，可核对收入回落和盈利变化主要集中在哪些业务。"
        insights.append({"title": "收入与盈利", "text": text, "refs": refs})
        cash = latest["operating_cash"]
        if cash is not None:
            text = f"同期经营活动现金流净额为 {format_money(cash, latest['currency'])}。"
            prior = next((row for row in reports[1:] if row["period"][5:] == latest["period"][5:]
                          and int(latest["period"][:4]) - int(row["period"][:4]) == 1
                          and row["currency"] == latest["currency"]), None)
            if prior and prior["operating_cash"] is not None:
                change = cash - prior["operating_cash"]
                if change:
                    text += f"较上年同期{'增加' if change > 0 else '减少'} {format_money(abs(change), latest['currency'])}。"
                else:
                    text += "与上年同期持平。"
                if profit is not None and change * profit < 0:
                    text += "经营现金流与归母利润的变化方向不同，可核对回款、存货与经营性往来。"
            insights.append({"title": "经营现金流", "text": text, "refs": refs})
        elif latest["cash_per_share"] is not None:
            text = f"同期每股经营现金流为 {latest['cash_per_share']:,.2f} {latest['currency'] or '原币'}。"
            if latest["cash_per_share_yoy"] is not None:
                text += f"同比 {latest['cash_per_share_yoy']:+.2f}%。"
            text += "平台未提供现金流净额，可在财报的现金流量表中核对。"
            insights.append({"title": "经营现金流", "text": text, "refs": refs})

    # Prefer an operating event over routine meeting, capital and disclosure notices.
    events = [row for row in timeline if row["kind"] == "disclosure"
              or (short_name and short_name in row["title"])]
    focus = next((item for kind, category in (("disclosure", "业务进展"), ("disclosure", "风险事项"),
                 ("media", "业务进展"), ("media", "风险事项"), ("disclosure", "股东与融资事项"), ("disclosure", "业绩披露"))
                 for item in events if item["kind"] == kind and item["category"] == category), None)
    if focus:
        category, followup = classify(focus["title"])
        kind = "公司公告" if focus["kind"] == "disclosure" else "媒体报道"
        text = f"{focus['date']} 的{kind}涉及{category}：{focus['title']}。{followup}。"
        if latest and category != "业绩披露" and focus["date"] > latest["period"]:
            text += f"该材料发布在 {latest['period']} 期末之后，现有财务数字不足以确认该事项的影响。"
        elif not latest:
            text += "缺少截止日前可核验的财报，暂不判断财务影响。"
        event_refs = [{"label": kind, "url": focus["url"]}]
        if latest and latest.get("filing_url") != focus["url"]:
            event_refs.append({"label": "对应经营数据", "url": latest.get("filing_url") or latest["source_url"]})
        insights.append({"title": "事件与财报对读", "text": text, "refs": event_refs})
    return insights


def collect_research(get, post, cache_get, cache_set, code, market, start, end):
    symbol = f"{'SH' if market else 'SZ'}{code}"
    window = (code, start.isoformat(), end.isoformat())
    def cached(key, load):
        existing = cache_get(key)
        if existing is not None:
            return existing
        result = load()
        if result and (not isinstance(result, dict) or result.get("status") not in {"unavailable", "partial"}):
            cache_set(key, result)
        return result

    def disclosures():
        directory = cached("research_directory:all", lambda: company_directory(get))
        org, name = directory.get(code, (None, None))
        records = cached("research_disclosures:" + ":".join(window),
                         lambda: historical_announcements(post, code, market, org, name, start, end))
        return records, org, name

    with ThreadPoolExecutor(max_workers=5) as pool:
        profile_job = pool.submit(cached, f"research_profile:{code}", lambda: company_profile(get, code, symbol))
        finance_job = pool.submit(cached, f"research_financial:{code}", lambda: financial_reports(get, code, symbol))
        disclosure_job = pool.submit(disclosures)
        profile = profile_job.result()
        name = profile["short_name"] if profile else None
        if not name:
            financial = finance_job.result()
            name = financial[0]["company_name"] if financial else None
        if not name:
            _, _, name = disclosure_job.result()
        media_job = pool.submit(cached, "research_media:" + ":".join(window), lambda: google_history(get, name, start, end))
        sina_job = None
        if end >= datetime.now(SHANGHAI).date() - timedelta(days=14):
            sina_job = pool.submit(cached, "research_sina:" + ":".join(window),
                                   lambda: sina_latest(get, symbol, name, code, start, end))
        financial = finance_job.result()
        notices, org, _ = disclosure_job.result()
        media = media_job.result()
        recent = sina_job.result() if sina_job else feed(status="skipped")

    reports = report_as_of(financial, end)
    timeline = dedupe([*notices["items"], *media["items"], *recent["items"]])
    latest = reports[0] if reports else None
    if latest:
        report = matching_filing(latest, notices["items"])
        # The reporting date can predate the news window. Fetch its PDF separately, without adding it to the timeline.
        filing_day = date.fromisoformat(latest["disclosed_on"])
        if not report and org and filing_day < start:
            filings = cached(f"research_disclosures:{code}:{filing_day}:{filing_day}",
                             lambda: fetch_announcements(post, code, market, filing_day, filing_day, org=org))
            report = matching_filing(latest, filings["items"])
        if report:
            latest = {**latest, "filing_url": report["url"]}
            reports[0] = latest
    return {"code": code, "company": profile, "name": name or code,
            "start": start.isoformat(), "end": end.isoformat(), "financial": latest,
            "reports": reports[:8], "timeline": timeline,
            "insights": analyze_company(reports, timeline, name),
            "sources": {"profile": "ok" if profile else "unavailable",
                        "financial": "ok" if latest else "out_of_range" if financial else "unavailable",
                        "disclosures": notices["status"], "media": media["status"], "sina": recent["status"]},
            "truncated": {"disclosures": notices["truncated"], "media": media["truncated"]}}
