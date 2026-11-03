"""
股探 StockExplorer — 专业A股行情看盘 + 热点掘金
功能: 大盘指数 / 自选股 / 分时图 / 多周期K线 / 技术指标(MACD/RSI/KDJ/BOLL) /
      指数成分股 / 热点话题匹配 / 资讯搜索

数据源: 新浪财经(行情+K线+分时) + 东方财富(概念+新闻) + DuckDuckGo(网页搜索)
"""

import json
import re
import time
import math
from datetime import datetime, timedelta
from urllib.parse import quote
from difflib import SequenceMatcher
from collections import OrderedDict

import requests
import urllib3
from flask import Flask, jsonify, render_template, request

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
app = Flask(__name__)

# ═══════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn/",
}

# 新浪 API
SINA_QUOTE_URL = "https://hq.sinajs.cn/list={symbols}"
SINA_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
SINA_MINLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getMinLine"

# 东方财富 API
EM_SEARCH_URL = "https://searchadapter.eastmoney.com/api/suggest/get"
EM_CLIST_URL = "https://82.push2.eastmoney.com/api/qt/clist/get"
EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EM_NEWS_URL = "https://np-listapi.eastmoney.com/comm/news/search"

# 可选依赖
try:
    from ddgs import DDGS; HAS_DDGS = True
except ImportError:
    try:
        from duckduckgo_search import DDGS; HAS_DDGS = True
    except ImportError:
        HAS_DDGS = False

# 缓存
_cache = OrderedDict()
CACHE_TTL = {"concepts": 3600, "concept_stocks": 1800, "kline": 120, "intraday": 30,
             "search": 300, "info": 30, "index": 30}
MAX_CACHE = 500

def _cache_key(*parts): return ":".join(str(p) for p in parts)

def _cache_get(key):
    e = _cache.get(key)
    if e:
        data, ts = e
        if time.time() - ts < CACHE_TTL.get(key.split(":")[0], 60):
            return data
    return None

def _cache_set(key, data):
    if len(_cache) > MAX_CACHE:
        _cache.popitem(last=False)
    _cache[key] = (data, time.time())

# ═══════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════

def safe_float(val, divisor=1):
    if val is None or val == "-" or val == "": return 0.0
    try: return float(val) / divisor
    except (ValueError, TypeError): return 0.0

def parse_stock_input(raw: str):
    raw = raw.strip()
    if re.match(r"^\d{6}$", raw):
        if raw.startswith(("6", "9")): return 1, raw
        else: return 0, raw
    return None, None

def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def sina_symbol(market: int, code: str) -> str:
    return f"{'sh' if market == 1 else 'sz'}{code}"

def http_get(url, params=None, headers=None, timeout=10, verify=False):
    try:
        r = requests.get(url, params=params, headers=headers or HEADERS, timeout=timeout, verify=verify)
        r.raise_for_status()
        return r, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

# ═══════════════════════════════════════════════════════════════════
# 大盘指数
# ═══════════════════════════════════════════════════════════════════

INDEX_CONFIG = [
    {"code": "sh000001", "name": "上证指数",   "market": 1, "type": "index"},
    {"code": "sz399001", "name": "深证成指",   "market": 0, "type": "index"},
    {"code": "sz399006", "name": "创业板指",   "market": 0, "type": "index"},
    {"code": "sh000688", "name": "科创50",     "market": 1, "type": "index"},
    {"code": "sh000300", "name": "沪深300",    "market": 1, "type": "index"},
    {"code": "sh000905", "name": "中证500",    "market": 1, "type": "index"},
    {"code": "sz399293", "name": "创业大盘",   "market": 0, "type": "index"},
    {"code": "sh000852", "name": "中证1000",   "market": 1, "type": "index"},
]

def get_index_quotes():
    ck = _cache_key("index", "quotes")
    cached = _cache_get(ck)
    if cached: return cached

    symbols = ",".join([f"s_{c['code']}" for c in INDEX_CONFIG])
    r, err = http_get(SINA_QUOTE_URL.format(symbols=symbols))

    results = []
    if r:
        r.encoding = "gbk"
        for cfg in INDEX_CONFIG:
            pattern = rf'var hq_str_s_{cfg["code"]}="([^"]*)"'
            match = re.search(pattern, r.text)
            if match:
                f = match.group(1).split(",")
                if len(f) >= 6:
                    # 新浪指数格式: 名称,现价,涨跌额,涨跌幅(%),成交量,成交额
                    price = safe_float(f[1])
                    chg_amt = safe_float(f[2])
                    chg_pct = safe_float(f[3])  # 已是百分比形式
                    preclose = round(price - chg_amt, 4) if price and chg_amt else price
                    vol = int(safe_float(f[4])) if len(f) > 4 else 0
                    amt = safe_float(f[5]) if len(f) > 5 else 0
                    results.append({
                        "code": cfg["code"], "name": cfg["name"], "type": cfg["type"],
                        "market": cfg["market"], "price": price, "preclose": preclose,
                        "change_pct": chg_pct, "change_amt": chg_amt,
                        "high": price, "low": price, "open": preclose,
                        "volume": vol, "amount": amt,
                    })
    _cache_set(ck, results)
    return results

# ═══════════════════════════════════════════════════════════════════
# 股票行情 & K线 & 分时 (新浪)
# ═══════════════════════════════════════════════════════════════════

def search_stocks(keyword: str) -> list[dict]:
    ck = _cache_key("search", keyword)
    cached = _cache_get(ck)
    if cached: return cached

    r, err = http_get(EM_SEARCH_URL, params={"input": keyword, "type": "14",
        "token": "D43BF722C8E33BDC906FB84D85E326E8", "count": "15"})
    results = []
    if r:
        data = r.json()
        if "QuotationCodeTable" in data:
            qt = data["QuotationCodeTable"]
            items = qt.get("Data") if isinstance(qt, dict) else None
            if items and isinstance(items, list):
                for item in items:
                    code = item.get("Code", "")
                    if not re.match(r"^\d{6}$", code): continue
                    market = 1 if code.startswith(("6", "9")) else 0
                    results.append({"code": code, "market": market,
                        "name": item.get("Name", ""),
                        "full_code": f"{'SH' if market == 1 else 'SZ'}{code}"})
    _cache_set(ck, results)
    return results


def get_stock_info(market: int, code: str) -> dict:
    ck = _cache_key("info", market, code)
    cached = _cache_get(ck)
    if cached: return cached

    symbol = sina_symbol(market, code)
    r, err = http_get(SINA_QUOTE_URL.format(symbols=symbol))

    result = {"code": code, "name": "", "price": 0, "open": 0, "high": 0, "low": 0,
              "preclose": 0, "volume": 0, "amount": 0, "change_pct": 0, "change_amt": 0,
              "amplitude": 0, "market_cap": 0, "pe": 0, "turnover_rate": 0}

    if r:
        r.encoding = "gbk"
        match = re.search(r'"([^"]*)"', r.text)
        if match:
            f = match.group(1).split(",")
            if len(f) >= 10:
                price = safe_float(f[3]); preclose = safe_float(f[2])
                chg_pct = round((price - preclose) / preclose * 100, 2) if preclose > 0 else 0
                high = safe_float(f[4]); low = safe_float(f[5])
                amp = round((high - low) / preclose * 100, 2) if preclose > 0 and high > 0 else 0
                result = {"code": code, "name": f[0], "price": price,
                    "open": safe_float(f[1]), "high": high, "low": low,
                    "preclose": preclose, "volume": int(safe_float(f[8])),
                    "amount": safe_float(f[9]), "change_pct": chg_pct,
                    "change_amt": round(price - preclose, 2), "amplitude": amp,
                    "market_cap": 0, "pe": 0, "turnover_rate": 0}
    _cache_set(ck, result)
    return result


def get_kline(market: int, code: str, period: str = "daily", limit: int = 250) -> list[dict]:
    ck = _cache_key("kline", market, code, period, limit)
    cached = _cache_get(ck)
    if cached: return cached

    symbol = sina_symbol(market, code)
    scale_map = {"daily": 240, "weekly": 1200, "monthly": 7200}
    scale = scale_map.get(period, 240)

    r, err = http_get(SINA_KLINE_URL, params={"symbol": symbol, "scale": scale, "ma": "no", "datalen": limit})
    results = []
    if r:
        data = r.json()
        if isinstance(data, list):
            for item in data:
                results.append({"date": item.get("day", ""), "open": safe_float(item.get("open")),
                    "close": safe_float(item.get("close")), "high": safe_float(item.get("high")),
                    "low": safe_float(item.get("low")), "volume": int(safe_float(item.get("volume"))),
                    "amount": 0, "amplitude": 0, "change_pct": 0, "change_amt": 0, "turnover": 0})
    _cache_set(ck, results)
    return results


def get_intraday(market: int, code: str) -> dict:
    """获取当日分时数据 (1分钟线)。
    数据源: 新浪财经 (主) + 东方财富 1分钟K线 (备用)
    """
    ck = _cache_key("intraday", market, code)
    cached = _cache_get(ck)
    if cached: return cached

    symbol = sina_symbol(market, code)
    minutes = []

    # ── 主数据源: 新浪分钟线 ──
    r, err = http_get(SINA_MINLINE_URL, params={"symbol": symbol, "scale": 1, "datalen": 240})
    if r:
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            cum_vol = 0; cum_amt = 0
            for item in data:
                vol = int(safe_float(item.get("volume")))
                price = safe_float(item.get("close")) or safe_float(item.get("open"))
                cum_vol += vol; cum_amt += vol * price
                avg_price = cum_amt / cum_vol if cum_vol > 0 else price
                minutes.append({"time": item.get("time", item.get("day", "")),
                    "price": price, "volume": vol, "avg_price": round(avg_price, 2)})

    # ── 备用数据源: 东方财富 1分钟K线 ──
    if not minutes:
        em_market = {1: 1, 0: 0}  # 东方财富: 1=沪市, 0=深市
        secid = f"{em_market.get(market, 0)}.{code}"
        r2, err2 = http_get(EM_KLINE_URL, params={
            "secid": secid, "klt": 1, "fqt": 0,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "end": "20500101", "lmt": 240,
        })
        if r2:
            try:
                em_data = r2.json()
                klines = em_data.get("data", {}).get("klines", []) or []
                if isinstance(klines, list):
                    cum_vol = 0; cum_amt = 0
                    for line in klines:
                        parts = line.split(",")
                        if len(parts) >= 7:
                            # f51=时间, f52=开, f53=收, f54=高, f55=低, f56=量, f57=额
                            time_str = parts[0].split(" ")[-1] if " " in parts[0] else parts[0]  # 取 HH:MM
                            price = safe_float(parts[2])  # 收盘价 (f53)
                            vol = int(safe_float(parts[5]))   # 成交量 (f56)
                            amt = safe_float(parts[6])         # 成交额 (f57)
                            cum_vol += vol; cum_amt += amt
                            avg_price = cum_amt / cum_vol if cum_vol > 0 else price
                            minutes.append({"time": time_str, "price": price,
                                "volume": vol, "avg_price": round(avg_price, 2)})
            except Exception:
                pass

    # 同时获取昨日收盘价 & 判断交易日
    info = get_stock_info(market, code)
    preclose = info.get("preclose", 0)

    # 从数据中提取实际交易日期
    trade_date = datetime.now().strftime("%Y-%m-%d")
    for m in minutes:
        t = m.get("time", "")
        if " " in t:
            trade_date = t.split(" ")[0]
            break

    # 判断是否为当日数据（非交易时段显示最近交易日数据）
    today_str = datetime.now().strftime("%Y-%m-%d")
    is_today = (trade_date == today_str)

    result = {
        "code": code, "name": info.get("name", ""), "preclose": preclose,
        "minutes": minutes, "trade_date": trade_date, "is_today": is_today,
    }
    _cache_set(ck, result)
    return result

# ═══════════════════════════════════════════════════════════════════
# 技术指标计算
# ═══════════════════════════════════════════════════════════════════

def calc_ma(data, period):
    result = []
    for i in range(len(data)):
        if i < period - 1: result.append(None)
        else: result.append(round(sum(data[i-period+1:i+1]) / period, 2))
    return result


def calc_ema(data, period):
    result = []
    k = 2 / (period + 1)
    for i in range(len(data)):
        if i == 0: result.append(data[0])
        else: result.append(round(data[i] * k + result[-1] * (1 - k), 4))
    return result


def calc_macd(closes, fast=12, slow=26, signal=9):
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    dif = [round(ema_fast[i] - ema_slow[i], 4) for i in range(len(closes))]
    dea = calc_ema(dif, signal)
    macd = [round((dif[i] - dea[i]) * 2, 4) for i in range(len(closes))]
    return {"dif": dif, "dea": dea, "macd": macd}


def calc_rsi(closes, period=14):
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    rsi = [None] * len(closes)
    if len(gains) >= period:
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        rsi[period] = round(100 - 100 / (1 + avg_gain / avg_loss), 2) if avg_loss != 0 else 100.0

        for i in range(period + 1, len(closes)):
            avg_gain = (avg_gain * (period - 1) + gains[i-1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i-1]) / period
            rsi[i] = round(100 - 100 / (1 + avg_gain / avg_loss), 2) if avg_loss != 0 else 100.0

    return rsi


def calc_kdj(highs, lows, closes, n=9, m1=3, m2=3):
    k_vals, d_vals, j_vals = [], [], []
    k, d = 50.0, 50.0

    for i in range(len(closes)):
        if i < n - 1:
            k_vals.append(None); d_vals.append(None); j_vals.append(None)
            continue
        low_n = min(lows[i-n+1:i+1])
        high_n = max(highs[i-n+1:i+1])
        rsv = (closes[i] - low_n) / (high_n - low_n) * 100 if high_n != low_n else 50
        k = (k * (m1 - 1) + rsv) / m1
        d = (d * (m2 - 1) + k) / m2
        j = 3 * k - 2 * d
        k_vals.append(round(k, 2)); d_vals.append(round(d, 2)); j_vals.append(round(j, 2))

    return {"k": k_vals, "d": d_vals, "j": j_vals}


def calc_boll(closes, period=20, std_dev=2):
    mb = calc_ma(closes, period)
    up, dn = [], []

    for i in range(len(closes)):
        if i < period - 1:
            up.append(None); dn.append(None)
            continue
        window = closes[i-period+1:i+1]
        avg = sum(window) / period
        variance = sum((x - avg) ** 2 for x in window) / period
        std = math.sqrt(variance)
        up.append(round(avg + std_dev * std, 2))
        dn.append(round(avg - std_dev * std, 2))

    return {"mb": mb, "up": up, "dn": dn}


def calc_all_indicators(kline_data):
    """从K线数据计算全部技术指标。"""
    if len(kline_data) < 30:
        return {}

    closes = [d["close"] for d in kline_data]
    highs = [d["high"] for d in kline_data]
    lows = [d["low"] for d in kline_data]
    volumes = [d["volume"] for d in kline_data]

    # 均线
    mas = {f"ma{p}": calc_ma(closes, p) for p in [5, 10, 20, 60, 120, 250]}

    return {
        "ma": mas,
        "macd": calc_macd(closes),
        "rsi": calc_rsi(closes),
        "kdj": calc_kdj(highs, lows, closes),
        "boll": calc_boll(closes),
        "vol_ma": {"ma5": calc_ma(volumes, 5), "ma10": calc_ma(volumes, 10)},
    }

# ═══════════════════════════════════════════════════════════════════
# 指数成分股 & 概念板块 (东方财富)
# ═══════════════════════════════════════════════════════════════════

def get_index_constituents(index_code: str, top_n: int = 50) -> list[dict]:
    """获取指数成分股。"""
    ck = _cache_key("index_stocks", index_code)
    cached = _cache_get(ck)
    if cached: return cached

    # 东财成分股API: fs=b:sh000001
    r, err = http_get(EM_CLIST_URL, params={
        "fid": "f3", "po": "1", "pz": str(top_n), "pn": "1", "np": "1", "fltt": "2",
        "invt": "2", "fs": f"b:{index_code}",
        "fields": "f12,f14,f2,f3,f4,f5,f6,f7,f20,f15,f16,f17,f18",
    })
    results = []
    if r:
        data = r.json()
        if data.get("data") and data["data"].get("diff"):
            for item in data["data"]["diff"]:
                results.append({"code": item.get("f12", ""), "name": item.get("f14", ""),
                    "price": safe_float(item.get("f2")),  # f2 is already in 元, no divisor needed
                    "change_pct": safe_float(item.get("f3"), 100),
                    "volume": int(safe_float(item.get("f5"))),
                    "amount": safe_float(item.get("f6")),
                    "market_cap": safe_float(item.get("f20")),
                    "high": safe_float(item.get("f15"), 100),
                    "low": safe_float(item.get("f16"), 100),
                    "open": safe_float(item.get("f17"), 100),
                    "preclose": safe_float(item.get("f18"), 100)})

    # 降级: 用新浪指数成分页抓取
    if not results:
        results = _get_constituents_from_sina(index_code)

    _cache_set(ck, results)
    return results


def _get_constituents_from_sina(index_code: str) -> list[dict]:
    """从新浪获取指数成分股（备用方案）。"""
    # 常见指数的成分股（核心标的），API不可用时的fallback
    fallback = {
        "sh000001": ["600519","601398","601288","600036","601857","601166","600900",
                      "601318","600030","601088","601628","600887","601668","600276"],
        "sz399001": ["000858","002594","000333","002415","000001","002230","000651",
                      "000568","000063","002714","300750","002475","000725"],
        "sz399006": ["300750","300059","300124","300274","300015","300014","300033",
                      "300760","300408","300413","300122","300142"],
        "sh000688": ["688981","688036","688008","688111","688012","688396","688009",
                      "688561","688126","688256","688185","688303"],
        "sh000300": ["600519","601398","601288","600036","601857","601166","600900",
                      "000858","002594","000333","300750"],
        "sh000905": ["002074","002340","002353","002384","002408","002410","002422"],
    }
    codes = fallback.get(index_code, [])
    results = []
    for code in codes:
        mkt = 1 if code.startswith(("6", "9")) else 0
        info = get_stock_info(mkt, code)
        if info and info.get("price", 0) > 0:
            results.append({"code": code, "name": info["name"], "price": info["price"],
                "change_pct": info["change_pct"], "volume": info["volume"],
                "market_cap": info["market_cap"], "amount": info["amount"],
                "high": info["high"], "low": info["low"],
                "open": info["open"], "preclose": info["preclose"]})
    return results


def get_all_concepts() -> list[dict]:
    ck = _cache_key("concepts", "all")
    cached = _cache_get(ck)
    if cached: return cached

    r, err = http_get(EM_CLIST_URL, params={
        "fid": "f3", "po": "1", "pz": "10000", "pn": "1", "np": "1", "fltt": "2",
        "invt": "2", "fs": "m:90+t:3", "fields": "f12,f14",
    })
    results = []
    if r:
        data = r.json()
        if data.get("data") and data["data"].get("diff"):
            for item in data["data"]["diff"]:
                results.append({"code": item.get("f12", ""), "name": item.get("f14", "")})
    _cache_set(ck, results)
    return results


def search_concepts(keyword: str, top_n: int = 10) -> list[dict]:
    all_concepts = get_all_concepts()
    kw = keyword.lower(); scored = []
    for c in all_concepts:
        nl = c["name"].lower()
        if kw in nl: scored.append({**c, "score": 1.0 + 1.0 / max(len(nl), 1)})
        else:
            sim = similarity(kw, nl)
            if sim > 0.35: scored.append({**c, "score": sim})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_n]


def get_concept_stocks(board_code: str, top_n: int = 30) -> list[dict]:
    ck = _cache_key("concept_stocks", board_code)
    cached = _cache_get(ck)
    if cached: return cached

    r, err = http_get(EM_CLIST_URL, params={
        "fid": "f3", "po": "1", "pz": str(top_n), "pn": "1", "np": "1", "fltt": "2",
        "invt": "2", "fs": f"b:{board_code}",
        "fields": "f12,f14,f2,f3,f5,f6,f7,f20",
    })
    results = []
    if r:
        data = r.json()
        if data.get("data") and data["data"].get("diff"):
            for item in data["data"]["diff"]:
                results.append({"code": item.get("f12", ""), "name": item.get("f14", ""),
                    "price": safe_float(item.get("f2")),  # f2 is already in 元, no divisor needed
                    "change_pct": safe_float(item.get("f3"), 100),
                    "volume": int(safe_float(item.get("f5"))),
                    "amount": safe_float(item.get("f6")),
                    "turnover": safe_float(item.get("f7"), 100),
                    "market_cap": safe_float(item.get("f20"))})
    _cache_set(ck, results)
    return results

# ═══════════════════════════════════════════════════════════════════
# 热门人气股 (东方财富活跃股排行)
# ═══════════════════════════════════════════════════════════════════


def get_popular_stocks(top_n: int = 30) -> list[dict]:
    """获取热门人气股（按成交量排序，沪深A股）。"""
    ck = _cache_key("popular", str(top_n))
    cached = _cache_get(ck)
    if cached:
        return cached

    # 沪深A股全市场: 深市主板+创业板 + 沪市主板+科创板
    # fid=f5 按成交量排序（成交量越大越活跃）
    r, err = http_get(EM_CLIST_URL, params={
        "fid": "f5", "po": "1", "pz": str(top_n), "pn": "1", "np": "1", "fltt": "2",
        "invt": "2", "fs": "m:0+t:6+f:!2,m:0+t:13+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2",
        "fields": "f12,f14,f2,f3,f5,f6,f7,f20",
    })
    results = []
    if r:
        data = r.json()
        if data.get("data") and data["data"].get("diff"):
            for item in data["data"]["diff"]:
                results.append({
                    "code": item.get("f12", ""),
                    "name": item.get("f14", ""),
                    "price": safe_float(item.get("f2")),  # f2 已是元，不需要除100
                    "change_pct": safe_float(item.get("f3"), 100),
                    "volume": int(safe_float(item.get("f5"))),
                    "amount": safe_float(item.get("f6")),
                    "turnover": safe_float(item.get("f7"), 100),
                    "market_cap": safe_float(item.get("f20")),
                })

    # 补全没有行情数据的股票
    for s in results:
        if s["price"] == 0:
            mkt = 1 if s["code"].startswith(("6", "9")) else 0
            try:
                info = get_stock_info(mkt, s["code"])
                if info:
                    s["price"] = info.get("price", 0)
                    s["change_pct"] = info.get("change_pct", 0)
                    s["volume"] = info.get("volume", 0)
                    s["amount"] = info.get("amount", 0)
                    s["turnover"] = info.get("turnover_rate", 0)
            except Exception:
                pass

    _cache_set(ck, results)
    return results


# ═══════════════════════════════════════════════════════════════════
# 新闻 & 热点
# ═══════════════════════════════════════════════════════════════════

def search_eastmoney_news(keyword: str, page_size: int = 10) -> list[dict]:
    try:
        r, err = http_get(EM_NEWS_URL, params={"client": "web", "keyword": keyword,
            "pageIndex": 1, "pageSize": page_size})
        if r:
            data = r.json()
            results = []
            items = data.get("data", {}).get("list", []) if isinstance(data.get("data"), dict) else []
            for item in items:
                results.append({"title": re.sub(r"</?em>", "", item.get("title", "")),
                    "summary": re.sub(r"</?em>", "", item.get("summary", "")),
                    "url": item.get("url", ""), "date": item.get("date", ""), "source": "东方财富"})
            return results
    except Exception as e:
        print(f"[EM News] {e}")
    return []


def search_web(keyword: str, max_results: int = 8) -> list[dict]:
    if not HAS_DDGS: return []
    try:
        with DDGS() as ddgs:
            results = []
            for r in ddgs.text(f"{keyword} 股票 A股", max_results=max_results):
                results.append({"title": r.get("title", ""), "summary": r.get("body", ""),
                    "url": r.get("href", ""), "source": "web"})
            return results
    except Exception as e:
        print(f"[Web] {e}")
    return []


def get_hot_news() -> dict:
    """获取热门资讯和热门话题。"""
    ck = _cache_key("hotnews", "1")
    cached = _cache_get(ck)
    if cached: return cached

    # 热门话题关键词
    hot_topics = ["人工智能", "芯片半导体", "新能源", "机器人", "低空经济", "固态电池",
                  "华为产业链", "自动驾驶", "数据要素", "创新药", "消费电子", "光伏"]

    # 并行搜索热门话题
    all_news = []
    for topic in hot_topics[:6]:
        news = search_eastmoney_news(topic, 2)
        all_news.extend(news)

    web_news = search_web("A股 今日热点 概念板块", 6) if HAS_DDGS else []

    result = {"hot_topics": hot_topics, "em_news": all_news[:15], "web_news": web_news}
    _cache_set(ck, result)
    return result

# ═══════════════════════════════════════════════════════════════════
# 核心匹配引擎
# ═══════════════════════════════════════════════════════════════════

def _extract_keywords(query: str) -> list[str]:
    parts = re.split(r"[,，\s、；;]+", query)
    parts = [p.strip() for p in parts if p.strip()]
    keywords = [query.strip()]
    for p in parts:
        if p not in keywords: keywords.append(p)
    text = query.strip()
    if len(text) >= 4: keywords.extend([text[:4], text[-4:]])
    if len(text) >= 3: keywords.extend([text[:3], text[-3:]])
    if len(text) >= 2: keywords.extend([text[:2], text[-2:]])
    seen = set(); result = []
    for kw in keywords:
        if kw not in seen and len(kw) >= 2: seen.add(kw); result.append(kw)
    return result[:8]


def match_query_to_stocks(query: str) -> dict:
    concept_stocks = {}
    concepts = []
    try: concepts = search_concepts(query, top_n=8)
    except Exception: pass

    for concept in concepts[:6]:
        try:
            stocks = get_concept_stocks(concept["code"], top_n=25)
            for s in stocks:
                sid = s["code"]
                if sid not in concept_stocks:
                    concept_stocks[sid] = {**s, "matched_via": concept["name"],
                        "relevance": round(concept["score"], 3)}
                elif concept["score"] > concept_stocks[sid]["relevance"]:
                    concept_stocks[sid]["relevance"] = round(concept["score"], 3)
                    concept_stocks[sid]["matched_via"] = concept["name"]
        except Exception: continue

    keywords = _extract_keywords(query)
    name_matched = {}
    for kw in keywords:
        try:
            matches = search_stocks(kw)
            for s in matches:
                sid = s["code"]
                if sid in name_matched: name_matched[sid]["kw_count"] += 1
                else: name_matched[sid] = {"code": s["code"], "name": s["name"], "price": 0,
                    "change_pct": 0, "volume": 0, "amount": 0, "turnover": 0,
                    "market_cap": 0, "matched_via": kw, "relevance": 0.50, "kw_count": 1}
        except Exception: continue

    for sid, stock in name_matched.items():
        if sid not in concept_stocks:
            bonus = min(0.3, (stock["kw_count"] - 1) * 0.15)
            stock["relevance"] = min(0.95, 0.50 + bonus)
            if stock["kw_count"] > 1: stock["matched_via"] = f"多关键词({stock['kw_count']}词)"
            concept_stocks[sid] = stock

    for sid, stock in concept_stocks.items():
        if stock["price"] == 0:
            mkt = 1 if sid.startswith(("6", "9")) else 0
            try:
                info = get_stock_info(mkt, sid)
                if info:
                    stock["price"] = info.get("price", 0)
                    stock["change_pct"] = info.get("change_pct", 0)
                    stock["volume"] = info.get("volume", 0)
                    stock["market_cap"] = info.get("market_cap", 0)
                    stock["turnover"] = info.get("turnover_rate", 0)
            except Exception: continue

    def rank_key(s):
        return s["relevance"] * (1 + abs(s.get("change_pct", 0)) * 0.05)

    ranked = sorted(concept_stocks.values(), key=rank_key, reverse=True)[:20]
    return {"concepts": [{"name": c["name"], "score": round(c["score"], 3)} for c in concepts[:6]],
        "stocks": ranked, "total_related": len(concept_stocks)}

# ═══════════════════════════════════════════════════════════════════
# Flask 路由
# ═══════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


# ─── 大盘指数 ───

@app.route("/api/index/quotes")
def api_index_quotes():
    return jsonify(get_index_quotes())


@app.route("/api/index/<index_code>/constituents")
def api_index_constituents(index_code):
    top_n = request.args.get("top", 50, type=int)
    results = get_index_constituents(index_code, top_n=top_n)
    return jsonify(results)


# ─── 股票搜索 & 行情 ───

@app.route("/api/stock/search")
def api_stock_search():
    keyword = request.args.get("keyword", "").strip()
    if not keyword: return jsonify([])
    return jsonify(search_stocks(keyword))


@app.route("/api/stock/<stock_code>/info")
def api_stock_info(stock_code):
    market, code = parse_stock_input(stock_code)
    if market is None:
        stocks = search_stocks(stock_code)
        if not stocks: return jsonify({"error": f"未找到: {stock_code}"}), 404
        market, code = stocks[0]["market"], stocks[0]["code"]
    info = get_stock_info(market, code)
    if not info.get("name"): return jsonify({"error": "获取行情失败"}), 500
    return jsonify(info)


@app.route("/api/stock/<stock_code>/chart")
def api_stock_chart(stock_code):
    period = request.args.get("period", "daily")
    market, code = parse_stock_input(stock_code)
    if market is None:
        stocks = search_stocks(stock_code)
        if not stocks: return jsonify({"error": f"未找到: {stock_code}"}), 404
        market, code = stocks[0]["market"], stocks[0]["code"]

    kline = get_kline(market, code, period=period)
    if not kline: return jsonify({"error": "获取K线失败"}), 500

    indicators = calc_all_indicators(kline) if len(kline) >= 30 else {}
    mas = indicators.pop("ma", {})
    # 合并基础MA到mas
    if not mas:
        closes = [d["close"] for d in kline]
        for p in [5, 10, 20, 60]:
            ma_vals = [None] * len(closes)
            for i in range(p-1, len(closes)):
                ma_vals[i] = round(sum(closes[i-p+1:i+1]) / p, 2)
            mas[f"ma{p}"] = ma_vals

    return jsonify({"data": kline, "mas": mas, "indicators": indicators})


@app.route("/api/stock/<stock_code>/intraday")
def api_stock_intraday(stock_code):
    market, code = parse_stock_input(stock_code)
    if market is None:
        stocks = search_stocks(stock_code)
        if not stocks: return jsonify({"error": f"未找到: {stock_code}"}), 404
        market, code = stocks[0]["market"], stocks[0]["code"]
    result = get_intraday(market, code)
    return jsonify(result)


# ─── 多股票批量行情 ───

@app.route("/api/stocks/batch")
def api_stocks_batch():
    codes_str = request.args.get("codes", "")
    if not codes_str: return jsonify([])
    codes = codes_str.split(",")[:30]
    results = []
    for code in codes:
        code = code.strip()
        if not re.match(r"^\d{6}$", code): continue
        mkt = 1 if code.startswith(("6", "9")) else 0
        info = get_stock_info(mkt, code)
        if info.get("name"): results.append(info)
    return jsonify(results)


# ─── 热门人气股 ───

@app.route("/api/stocks/popular")
def api_stocks_popular():
    top_n = request.args.get("top", 30, type=int)
    return jsonify(get_popular_stocks(top_n=top_n))


# ─── 话题匹配 ───

@app.route("/api/match")
def api_match():
    query = request.args.get("query", "").strip()
    if not query: return jsonify({"error": "请输入搜索关键词"}), 400

    match_result = match_query_to_stocks(query)
    em_news = search_eastmoney_news(query, 8)
    web_news = search_web(query) if HAS_DDGS else []

    return jsonify({
        "query": query, "concepts": match_result["concepts"],
        "stocks": match_result["stocks"], "total_related": match_result["total_related"],
        "em_news": em_news, "web_news": web_news,
    })


# ─── 新闻 ───

@app.route("/api/news/search")
def api_news_search():
    keyword = request.args.get("keyword", "").strip()
    if not keyword: return jsonify({"em_news": [], "web_news": []})
    return jsonify({"em_news": search_eastmoney_news(keyword), "web_news": search_web(keyword)})


@app.route("/api/news/hot")
def api_hot_news():
    return jsonify(get_hot_news())


# ─── 启动 ───

if __name__ == "__main__":
    print("=" * 55)
    print("  📈 股探 StockExplorer v2.0 已启动")
    print("  大盘指数 · 自选股 · 分时图 · 技术指标 · 成分股 · 热点掘金")
    print("  打开浏览器: http://localhost:8080")
    print("=" * 55)
    app.run(debug=False, host="0.0.0.0", port=8080)
