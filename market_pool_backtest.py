"""Replay the 55-day breakout / 1R trade across historical candidate pools.

Each pool also reports buy_and_hold: the same pool held at equal weight with no
entry or exit rules, the baseline the trading rule has to beat.

Requires NumPy. By default the runner reads the verified local data in
.cache/market-data. Run python3 market_pool_backtest.py to reproduce the report.
"""

import argparse
import csv
import json
import re
import tarfile
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import isfinite, prod
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from backtest_picker import BUY_COST, SELL_COST
from brooks_backtest import exit_order, stats
from trend_pullback_backtest import Setup

ROOT = Path(__file__).resolve().parent / ".cache" / "market-data"
SHANGHAI = ZoneInfo("Asia/Shanghai")
SLOTS = 20
FEATURES = {"open", "close", "high", "low", "amount", "factor"}
STOCK_CODE = re.compile(r"(?:sh(?:60|68)|sz(?:00|30)|bj(?:43|82|83|87|88|92))[0-9]{4}$")
# These three were suspended before BSE opened and remained suspended until transfer.
SUSPENDED_BSE_TRANSFERS = {"bj832317", "bj833874", "bj833994"}
TOPIC_WORDS = {
    "A01": ("种业", "种子", "粮食种植", "玉米种植", "小麦种植"),
    "A03": ("生猪", "猪价", "养殖", "肉鸡"),
    "B06": ("煤炭", "动力煤", "焦煤"),
    "B07": ("油气开采", "原油", "油价", "天然气开采"),
    "B09": ("有色矿", "铜矿", "锂矿", "稀土矿", "黄金矿"),
    "C14": ("乳制品", "食品加工", "休闲食品"),
    "C15": ("白酒", "啤酒", "饮料"),
    "C25": ("炼油", "炼化", "成品油"),
    "C26": ("化工", "磷肥", "化肥", "农药", "制冷剂"),
    "C27": ("创新药", "仿制药", "原料药", "医药", "疫苗"),
    "C31": ("钢铁", "钢价", "钢材", "螺纹钢"),
    "C32": ("有色金属", "电解铝", "铜价", "稀土", "碳酸锂"),
    "C34": ("工业母机", "机床", "减速器", "工业机器人"),
    "C35": ("人形机器人", "工程机械", "挖掘机", "半导体设备", "医疗器械"),
    "C36": ("汽车", "智能驾驶", "无人驾驶", "汽车零部件"),
    "C37": ("航空航天", "商业航天", "造船", "轨道交通"),
    "C38": ("光伏", "风电设备", "固态电池", "动力电池", "储能", "电网设备"),
    "C39": ("半导体", "存储芯片", "芯片", "算力", "光模块", "消费电子", "面板"),
    "D44": ("电力", "水电", "核电", "火电"),
    "D45": ("燃气", "城市燃气"),
    "E48": ("基建", "水利工程"),
    "F52": ("零售", "免税店", "超市"),
    "G55": ("航运", "集运", "海运"),
    "G56": ("航空运输", "航空公司", "机票"),
    "I63": ("电信运营商", "通信运营商", "卫星通信"),
    "I64": ("互联网平台", "跨境电商", "电商"),
    "I65": ("大模型", "人工智能", "工业软件", "网络安全", "云计算"),
    "J66": ("银行", "银行业", "净息差"),
    "J67": ("券商", "证券行业", "证券公司"),
    "J68": ("保险", "险资", "保费"),
    "K70": ("房地产", "楼市", "商品房"),
    "N77": ("环保", "污水处理", "固废"),
    "Q84": ("医疗服务", "民营医院"),
    "R87": ("影视", "电影", "票房", "短剧"),
}


@dataclass
class Stock:
    bars: np.ndarray
    amount: np.ndarray
    factor: np.ndarray


def code_key(value):
    value = value.strip().lower()
    if "." in value:
        code, exchange = value.split(".", 1)
        if code in ("sh", "sz", "bj"):
            return code + exchange
        return exchange + code
    return value


def load_market(archive, start, end):
    """Read only the needed Qlib fields and date range. Treat the tar as data."""
    dates, listings = [], {}
    stocks, fields = {}, {}
    last_code = None

    def commit(code):
        if FEATURES <= fields.keys():
            bars = np.column_stack([fields[field] for field in ("open", "close", "high", "low")])
            stocks[code] = Stock(bars, fields["amount"] * 1_000, fields["factor"])

    with tarfile.open(archive, mode="r|gz") as files:
        for member in files:
            if not member.isfile():
                continue
            tail = member.name.split("/")[-3:]
            if tail[-2:] == ["calendars", "day.txt"]:
                dates = files.extractfile(member).read().decode().split()
                first, last = bisect_left(dates, start), bisect_right(dates, end)
            elif tail[-2:] == ["instruments", "all.txt"]:
                for line in files.extractfile(member).read().decode().splitlines():
                    code, first_day, last_day = line.split()[:3]
                    listings.setdefault(code.lower(), []).append((first_day, last_day))
            elif len(tail) == 3 and tail[0] == "features" and STOCK_CODE.fullmatch(tail[1]):
                _, code, filename = tail
                name = filename.removesuffix(".day.bin")
                if name not in FEATURES:
                    continue
                if code != last_code:
                    if last_code:
                        commit(last_code)
                    fields, last_code = {}, code
                raw = np.frombuffer(files.extractfile(member).read(), dtype="<f4")
                offset = int(raw[0])
                lo, hi = max(first, offset), min(last, offset + len(raw) - 1)
                values = np.full(last - first, np.nan, dtype=np.float32)
                if lo < hi:
                    values[lo - first:hi - first] = raw[lo - offset + 1:hi - offset + 1]
                fields[name] = values
    if last_code:
        commit(last_code)
    if not dates or not stocks or not listings:
        raise ValueError("archive is missing calendar, instrument list, or daily features")

    sessions = dates[first:last]
    for code in list(stocks):
        intervals = listings.get(code, ())
        active = np.zeros(len(sessions), dtype=bool)
        for begin, finish in intervals:
            active[bisect_left(sessions, begin):bisect_right(sessions, finish)] = True
        stock = stocks[code]
        opening, close, high, low = stock.bars.T
        valid = (active & np.isfinite(stock.bars).all(axis=1) & (low > 0)
                 & (low <= np.minimum(opening, close)) & (high >= np.maximum(opening, close))
                 & (stock.amount > 0))
        if code.startswith("bj"):
            valid[:bisect_left(sessions, "2021-11-15")] = False
        stock.bars[~valid] = np.nan
        stock.amount[~valid] = 0
        stock.factor[~valid] = np.nan
        if not valid.any():
            del stocks[code]
    return sessions, stocks, listings


def reconcile_bse(stocks, path):
    if not path.exists():
        return {}
    with path.open() as stream:
        aliases = {"bj" + row["旧代码"]: "bj" + row["新代码"] for row in csv.DictReader(stream)}
    for old, new in aliases.items():
        if old not in stocks:
            continue
        earlier = stocks.pop(old)
        later = stocks.get(new)
        if later is None:
            stocks[new] = earlier
            continue
        valid = np.isfinite(earlier.bars[:, 1])
        later_valid = np.isfinite(later.bars[:, 1])
        overlap = valid & later_valid
        if overlap.any():
            scale = np.median(later.bars[overlap, 1] / earlier.bars[overlap, 1])
        else:
            scale = later.factor[np.flatnonzero(later_valid)[0]] / earlier.factor[np.flatnonzero(valid)[-1]]
        keep = valid & ~later_valid
        if keep.any():
            if not np.isfinite(scale) or scale <= 0:
                raise ValueError(f"no compatible BSE price scale for {old} -> {new}")
            later.bars[keep] = earlier.bars[keep] * scale
            later.factor[keep] = earlier.factor[keep] * scale
            later.amount[keep] = earlier.amount[keep]
    return aliases


PLAN_NOTE = re.compile(r"[(（][^)）]*[)）]")
PLAN_BASE = re.compile(r"\s*([0-9]+(?:\.[0-9]+)?)\s*[送转派]")
BONUS_SHARES = re.compile(r"[送转]增?\s*([0-9]+(?:\.[0-9]+)?)")


def share_multiplier(plan):
    """Shares held after the ex-date per share before it: 10送1转3派2元 -> 1.4."""
    text = PLAN_NOTE.sub("", plan or "")
    base = PLAN_BASE.match(text)
    bonus = sum(float(amount) for amount in BONUS_SHARES.findall(text))
    return 1 + bonus / float(base[1]) if base and bonus and float(base[1]) > 0 else 1.0


def read_dividends(directory, aliases):
    """Implemented distributions keyed by ex-date.

    Returns (cash, bonuses): cash paid per share held before the ex-date, and the
    share multiplier from 送股/转增 on that date (10转4股派2元 -> 1.4)."""
    dividends, bonuses = defaultdict(dict), defaultdict(dict)
    for path in sorted(directory.glob("????-??-??.json")):
        for row in json.loads(path.read_text()):
            ex = row["EX_DIVIDEND_DATE"]
            if row["ASSIGN_PROGRESS"] != "实施分配" or not ex:
                continue
            code = code_key(row["SECUCODE"])
            code = aliases.get(code, code)
            cash = row["PRETAX_BONUS_RMB"]
            if cash is not None and float(cash) > 0:
                dividends[code][ex[:10]] = float(cash) / 10
            multiplier = share_multiplier(row.get("IMPL_PLAN_PROFILE"))
            if multiplier > 1:
                bonuses[code][ex[:10]] = multiplier
    return dividends, bonuses


def dividend_candidates(stock, dividends, days, month_ends, bonuses=None):
    """Yield = cash paid in the last 365 days, per share held on the ranking day,
    / unadjusted close on the ranking day."""
    paid = sorted(dividends)
    return {i: (metrics["yield"], metrics["volatility"]) for i in month_ends
            if (metrics := dividend_metrics(stock, dividends, paid, days, i, bonuses))["eligible"]}


DIVIDEND_MIN_TURNOVER = 30_000_000
DIVIDEND_POOL_SIZE = 50


def dividend_metrics(stock, dividends, paid, days, i, bonuses=None):
    close = stock.bars[i, 1]
    priced = bool(np.isfinite(close) and stock.factor[i] > 0)
    turnover = float(np.median(stock.amount[i - 19:i + 1])) if i >= 19 else None
    today = date.fromisoformat(days[i])
    cutoffs = [(today - timedelta(days=365 * n)).isoformat() for n in (1, 2, 3)]
    boundaries = [bisect_right(paid, day) for day in (days[i], *cutoffs)]
    years = sum(left > right for left, right in zip(boundaries, boundaries[1:]))
    # Cash was paid per share held before its ex-date. Each 送股/转增 from that
    # date through the ranking day split those shares, so restate the cash per
    # share held now; bonus issues after the ranking day are not known yet.
    splits = sorted(bonuses or ())
    cash = 0.0
    for ex in paid[boundaries[1]:boundaries[0]]:
        later = splits[bisect_left(splits, ex):bisect_right(splits, days[i])]
        cash += dividends[ex] / prod(bonuses[split] for split in later)
    yield_ = float(cash / (close / stock.factor[i])) if priced else None
    closes = stock.bars[max(0, i - 252):i + 1, 1]
    closes = closes[np.isfinite(closes)]
    volatility = float(np.std(np.diff(np.log(closes)), ddof=1)) if i >= 252 and len(closes) >= 231 else None
    eligible = (priced and years == 3 and turnover is not None and turnover >= DIVIDEND_MIN_TURNOVER
                and volatility is not None and volatility > 0)
    return {"priced": priced, "turnover": turnover, "years": years, "yield": yield_,
            "volatility": volatility, "eligible": eligible}


def rank_low_volatility(scores):
    if not scores:
        return None, []
    median_yield = float(np.median([y for _, y, _ in scores]))
    ranked = sorted((row for row in scores if row[1] >= median_yield), key=lambda r: (r[2], -r[1], r[0]))
    return median_yield, ranked


def low_volatility_pool(scores):
    _, ranked = rank_low_volatility(scores)
    return {code for code, _, _ in ranked[:DIVIDEND_POOL_SIZE]}


def read_industries(path):
    """CSV of exchange-prefixed symbol, effective date, industry label."""
    histories = defaultdict(list)
    with path.open() as stream:
        for row in csv.DictReader(stream):
            histories[code_key(row["symbol"])].append((row["effective_date"], row["industry"]))
    return {code: sorted(events) for code, events in histories.items()}


def industry_at(history, day):
    index = bisect_right(history, (day, chr(0x10FFFF)))
    return history[index - 1][1] if index else None


def news_topics(articles, days, vocabulary):
    """Count unique texts and sources through 15:00 CST on each historical date."""
    if not vocabulary:
        return {}
    pattern = re.compile("|".join(re.escape(word) for word in sorted(vocabulary, key=len, reverse=True)))
    events = []
    for published, source, text in articles:
        stamp = datetime.fromisoformat(published)
        stamp = stamp.astimezone(SHANGHAI) if stamp.tzinfo else stamp.replace(tzinfo=SHANGHAI)
        normalized = re.sub(r"\W+", "", text)
        matches = {vocabulary[word] for word in pattern.findall(text)}
        events.append((stamp, source, normalized, matches))
    events.sort(key=lambda event: event[0])
    available, output, cursor = deque(), {}, 0
    for i, day in enumerate(days):
        cutoff = datetime.combine(date.fromisoformat(day), time(15), SHANGHAI)
        while cursor < len(events) and events[cursor][0] <= cutoff:
            available.append(events[cursor])
            cursor += 1
        while available and available[0][0] <= cutoff - timedelta(days=25):
            available.popleft()
        if not events or cutoff < events[0][0] + timedelta(days=25) or cutoff.date() > events[-1][0].date():
            continue
        recent, past = defaultdict(set), defaultdict(set)
        recent_texts, past_texts = set(), set()
        sources = defaultdict(set)
        recent_start = cutoff - timedelta(days=5)
        for stamp, source, text, tags in available:
            is_recent = stamp > recent_start
            (recent_texts if is_recent else past_texts).add(text)
            for topic in tags:
                if is_recent:
                    recent[topic].add(text)
                    sources[topic].add(source)
                else:
                    past[topic].add(text)
        expected = len(recent_texts) / max(1, len(past_texts))
        hot = [(len(texts) / (1 + len(past[topic]) * expected), len(texts), topic)
               for topic, texts in recent.items()
               if len(texts) >= 3 and len(sources[topic]) >= 2 and len(texts) > len(past[topic]) * expected]
        output[i] = [topic for _, _, topic in sorted(hot, key=lambda x: (-x[0], -x[1], x[2]))[:5]]
    return output


def breakout_signals(stock):
    """Vectorized equivalent of trend_pullback_backtest.scan's breakout branch."""
    indices = np.flatnonzero(np.isfinite(stock.bars[:, 1]))
    if len(indices) < 120:
        return ()
    opening, close, high, low = stock.bars[indices].astype(np.float64).T
    windows = np.lib.stride_tricks.sliding_window_view
    sma = windows(close, 100).mean(axis=1)
    resistance = windows(high, 55).max(axis=1)
    stops = windows(low, 10).min(axis=1)
    where = np.flatnonzero((close[119:] > resistance[64:-1])
                           & (close[119:] > sma[20:]) & (sma[20:] > sma[:-20])) + 119
    return [(indices[i], Setup(float(close[i]), float(stops[i - 9]))) for i in where]


def eligible_for_entry(stock, index, setup):
    opening, close, high, low = map(float, stock.bars[index])
    return high > low and setup.stop < opening <= 2 * setup.close - setup.stop


def build_signals(stocks):
    signals = defaultdict(list)
    activity = {}
    for code, stock in stocks.items():
        turnover = np.zeros(len(stock.amount), dtype=np.float32)
        heat = np.zeros_like(turnover)
        windows = np.lib.stride_tricks.sliding_window_view
        if len(turnover) >= 25:
            turnover[19:] = np.median(windows(stock.amount, 20), axis=1)
            recent = windows(stock.amount, 5).mean(axis=1)[20:]
            prior = windows(stock.amount, 20).mean(axis=1)[:-5]
            np.divide(recent, prior, out=heat[24:], where=prior > 0)
        activity[code] = turnover, heat
        for index, setup in breakout_signals(stock):
            if index + 1 < len(stock.amount):
                signals[index + 1].append((code, setup, float(turnover[index])))
    for group in signals.values():
        group.sort(key=lambda item: (-item[2], item[0]))
    return signals, activity


def monthly_dividend_pools(stocks, dividends, days, bonuses=None):
    month_ends = [i for i in range(len(days) - 1) if days[i][:7] != days[i + 1][:7]]
    scores = defaultdict(list)
    for code in stocks.keys() & dividends.keys():
        candidates = dividend_candidates(stocks[code], dividends[code], days, month_ends, (bonuses or {}).get(code))
        for i, (yield_, volatility) in candidates.items():
            scores[i].append((code, yield_, volatility))
    selected = {}
    pool = set()
    for i in range(len(days)):
        if i in month_ends:
            pool = low_volatility_pool(scores[i])
        selected[i] = pool
    return selected


def hot_stock_pools(stocks, activity, histories, topics, days):
    selected = {}
    covered = stocks.keys() & histories.keys()
    for i, hot in topics.items():
        groups = defaultdict(list)
        for code in covered:
            turnover, heat = activity[code]
            amount, acceleration = turnover[i], heat[i]
            if amount < 30_000_000 or acceleration <= 1 or not np.isfinite(stocks[code].bars[i, 1]):
                continue
            theme = industry_at(histories[code], days[i])
            if theme in hot:
                groups[theme].append((float(acceleration), float(amount), code))
        selected[i] = {code for group in groups.values()
                       for _, _, code in sorted(group, key=lambda x: (-x[0], -x[1], x[2]))[:10]}
    return selected


@dataclass
class Holding:
    quantity: float
    cost: float
    stop: float
    target: float
    close: float
    pending: bool


def replay(stocks, signals, start, end, selection, slip, delistings=None):
    cash, nav, trades, counts = 1.0, [], [], Counter()
    holding = {}
    delistings = delistings or {}
    for i in range(start, end + 1):
        previous_nav = nav[-1] if nav else 1.0
        # Funds and slots committed at the open. Proceeds from later exits are
        # available next session, so intraday events cannot fund opening orders.
        available = min(SLOTS - len(holding), len(signals.get(i, ())))
        opening_cash = cash
        existing = tuple(holding)
        for code in existing:
            position = holding[code]
            if delistings.get(code, len(stocks[code].amount)) <= i:
                trades.append({"return": -1.0, "reason": "delisted_zero_recovery"})
                del holding[code]
                continue
            bar = tuple(map(float, stocks[code].bars[i]))
            opening, close, high, low = bar
            if not isfinite(close):
                continue
            position.close = close
            if high <= low:
                position.pending |= low <= position.stop
                continue
            fill = (opening, "stop") if position.pending else exit_order(bar, position.stop, position.target)
            if fill:
                price, reason = fill
                proceeds = position.quantity * price * (1 - slip) * (1 - SELL_COST)
                cash += proceeds
                trades.append({"return": proceeds / position.cost - 1, "reason": reason})
                del holding[code]
        allowed = selection.get(i - 1, ()) if selection is not None else None
        for code, setup, _ in signals.get(i, ()):
            if not available or opening_cash <= 1e-12:
                break
            if code in existing or (allowed is not None and code not in allowed):
                continue
            opening, close, high, low = map(float, stocks[code].bars[i])
            if not eligible_for_entry(stocks[code], i, setup):
                continue
            budget = min(opening_cash, previous_nav / SLOTS)
            quantity = budget / (opening * (1 + slip)) * (1 - BUY_COST)
            holding[code] = Holding(quantity, budget, setup.stop, 2 * opening - setup.stop,
                                    close, close <= setup.stop)
            cash -= budget
            opening_cash -= budget
            available -= 1
        invested = sum(p.quantity * p.close for p in holding.values())
        nav.append(cash + invested)
        counts["held_days"] += invested / nav[-1]
    counts["open"] = len(holding)
    return stats(nav, trades, counts, len(nav))


def rebalance_pool(stocks, holding, marks, cash, pool, i, slip, resize):
    """Trade toward equal weight in pool at the open of session i. Returns (cash, retry).

    resize rebalances every tradable member; otherwise only names left behind by an
    earlier session are traded: members not bought yet and former members not sold."""
    prices = {}
    for code in holding.keys() | pool:
        opening, _, high, low = map(float, stocks[code].bars[i])
        if high > low:  # traded with a range today; NaN compares False
            prices[code] = opening
    for code in [code for code in holding if code not in pool and code in prices]:
        cash += holding.pop(code) * prices[code] * (1 - slip) * (1 - SELL_COST)
    if pool:
        goal = (cash + sum(quantity * prices.get(code, marks[code]) for code, quantity in holding.items())) / len(pool)
        if resize:  # trim overweight names first so the proceeds fund the rest
            for code in pool & holding.keys() & prices.keys():
                excess = holding[code] - goal / prices[code]
                if excess > 0:
                    holding[code] -= excess
                    cash += excess * prices[code] * (1 - slip) * (1 - SELL_COST)
        wanted = {code: goal - holding.get(code, 0.0) * prices[code] for code in pool & prices.keys()
                  if resize or code not in holding}
        wanted = {code: amount for code, amount in wanted.items() if amount > 0}
        scale = min(1.0, cash / sum(wanted.values())) if wanted else 0.0
        for code, amount in wanted.items():
            spend = amount * scale
            if spend <= 0:
                continue
            holding[code] = holding.get(code, 0.0) + spend / (prices[code] * (1 + slip)) * (1 - BUY_COST)
            cash -= spend
    retry = any(code not in pool for code in holding) or any(code not in holding for code in pool)
    return cash, retry


def hold_pool(stocks, start, end, selection, slip, delistings=None):
    """Baseline: hold the pool itself at equal weight, with no entry or exit rules.

    The pool known at the previous close is bought at the next open. Positions
    drift until the pool changes, then the portfolio is rebalanced at that open.
    Costs, slippage, tradability (high > low) and delisting write-offs match
    replay; a name that cannot trade is retried at each following open."""
    delistings = delistings or {}
    cash, holding, marks = 1.0, {}, {}
    target, retry, nav, counts = None, False, [], Counter()
    for i in range(start, end + 1):
        for code in [code for code in holding if delistings.get(code, len(stocks[code].amount)) <= i]:
            del holding[code]
        pool = selection.get(i - 1, frozenset())
        if pool != target or retry:
            counts["rebalances"] += pool != target
            cash, retry = rebalance_pool(stocks, holding, marks, cash, pool, i, slip, resize=pool != target)
            target = pool
        for code in holding:
            close = float(stocks[code].bars[i, 1])
            if isfinite(close):
                marks[code] = close
        invested = sum(quantity * marks[code] for code, quantity in holding.items())
        nav.append(cash + invested)
        counts["held_days"] += invested / nav[-1] if nav[-1] > 0 else 0.0
    report = stats(nav, [], counts, len(nav))
    return {"return": report["return"], "max_drawdown": report["max_drawdown"],
            "exposure": report["exposure"], "rebalances": counts["rebalances"]}


def load_stock_metadata(path):
    with path.open() as stream:
        metadata = {code_key(row["symbol"]): row for row in csv.DictReader(stream)}
    for exchange, code_key_zh, delist_key in (("sh", "公司代码", "暂停上市日期"),
                                              ("sz", "证券代码", "终止上市日期")):
        file = path.parent / f"delist-{exchange}.csv"
        if not file.exists():
            continue
        with file.open() as stream:
            for row in csv.DictReader(stream):
                code = exchange + row[code_key_zh]
                if not STOCK_CODE.fullmatch(code) or not row[delist_key]:
                    continue
                listing = metadata.setdefault(code, {"list_date": row["上市日期"].replace("-", "")})
                listing["delist_date"] = row[delist_key].replace("-", "")
    return metadata


def iso_day(day):
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def mask_before_listing(stocks, metadata, days):
    for code, stock in stocks.items():
        row = metadata.get(code)
        if row and row["list_date"]:
            first = bisect_left(days, iso_day(row["list_date"]))
            stock.bars[:first] = np.nan
            stock.amount[:first] = 0
            stock.factor[:first] = np.nan


def all_symbol_trades(stocks, signals, days, start, end, slip, delistings):
    """Check every ticker independently with the previous execution rules."""
    by_code = defaultdict(dict)
    for i, group in signals.items():
        if start <= i <= end:
            for code, setup, _ in group:
                by_code[code][i] = setup
    trades, n_open, counted_codes, delisted = [], 0, set(), 0
    for code, planned in by_code.items():
        stock = stocks[code]
        position = None
        delisting = delistings.get(code, len(days))
        for i in range(min(planned), end + 1):
            if position:
                if i >= delisting:
                    trades.append(-1.0)
                    delisted += 1
                    position = None
                    break
                opening, close, high, low = map(float, stock.bars[i])
                if high > low:
                    sale = (opening, "stop") if position.pending else exit_order(
                        (opening, close, high, low), position.stop, position.target)
                    if sale:
                        trades.append(position.quantity * sale[0] * (1 - slip) * (1 - SELL_COST) - 1)
                        position = None
                elif isfinite(low) and low <= position.stop:
                    position.pending = True
                continue
            setup = planned.get(i)
            if setup and eligible_for_entry(stock, i, setup):
                opening, close, _, _ = map(float, stock.bars[i])
                position = Holding((1 - BUY_COST) / (opening * (1 + slip)), 1, setup.stop,
                                   2 * opening - setup.stop, close, close <= setup.stop)
                counted_codes.add(code)
        n_open += position is not None
    wins = [value for value in trades if value > 0]
    losses = [value for value in trades if value < 0]
    return {"stocks_with_fills": len(counted_codes), "closed_trades": len(trades), "delisted_exits": delisted,
            "open_trades": n_open, "win_rate": len(wins) / len(trades) if trades else None,
            "mean_trade": sum(trades) / len(trades) if trades else None,
            "profit_factor": sum(wins) / -sum(losses) if losses else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT)
    parser.add_argument("--start", default="2020-01-02")
    parser.add_argument("--end", default="2026-11-06")
    args = parser.parse_args()
    root = args.data
    warmup = (date.fromisoformat(args.start) - timedelta(days=460)).isoformat()
    days, stocks, _ = load_market(root / "qlib_bin.tar.gz", warmup, args.end)
    aliases = reconcile_bse(stocks, root / "bse-code-map.csv")
    start, end = bisect_left(days, args.start), len(days) - 1
    if start >= end:
        parser.error("price archive has no coverage for the requested dates")
    metadata = load_stock_metadata(root / "stocks.csv")
    mask_before_listing(stocks, metadata, days)
    coverage = {}
    for exchange in ("sh", "sz", "bj"):
        expected = {code for code, row in metadata.items() if code.startswith(exchange)
                    and row["list_date"] and iso_day(row["list_date"]) <= days[end]
                    and (not row["delist_date"] or iso_day(row["delist_date"]) >= days[start])}
        usable = {code for code, stock in stocks.items()
                  if code.startswith(exchange) and np.isfinite(stock.bars[start:, 1]).any()}
        never_traded = (expected - usable) & SUSPENDED_BSE_TRANSFERS
        coverage[exchange] = {"metadata_expected": len(expected), "with_bars": len(usable),
                              "missing": sorted(expected - usable - never_traded),
                              "suspended_until_transfer": sorted(never_traded),
                              "extra": sorted(usable - expected)}
    delistings = {code: bisect_left(days, iso_day(row["delist_date"])) for code, row in metadata.items()
                  if row["delist_date"] and code in stocks}
    signals, activity = build_signals(stocks)
    dividends, bonuses = read_dividends(root / "dividends", aliases)
    steady = monthly_dividend_pools(stocks, dividends, days, bonuses) if dividends else None
    aggressive, topics, archive = None, {}, None
    industry_file = root / "industry-history.csv"
    news_file = root / "articles.csv"
    if industry_file.exists() and news_file.exists():
        with news_file.open() as stream:
            articles = [(row["published"], row["source"], row["text"]) for row in csv.DictReader(stream)]
        if articles:
            archive = {"count": len(articles), "from": min(row[0] for row in articles),
                       "through": max(row[0] for row in articles)}
        histories = read_industries(industry_file)
        words = {word: theme for theme, terms in TOPIC_WORDS.items() for word in terms}
        topics = news_topics(articles, days, words)
        aggressive = hot_stock_pools(stocks, activity, histories, topics, days)
    report = {"coverage": coverage, "start": days[start], "end": days[end], "slots": SLOTS,
              "news_archive": archive,
              "all_signals": all_symbol_trades(stocks, signals, days, start, end, 0, delistings), "runs": []}
    windows = [(start, end)]
    if topics:
        common = (max(start, min(topics) + 1), min(end, max(topics)))
        if common[0] <= common[1] and common not in windows:
            windows.append(common)
    for first, last in windows:
        for slip in (0, 0.0005):
            entry = {"start": days[first], "end": days[last], "slippage": slip}
            for name, pool in (("all", None), ("steady", steady), ("aggressive", aggressive)):
                if name == "steady" and pool is None:
                    continue
                if name == "aggressive" and (pool is None or first - 1 not in pool or last - 1 not in pool):
                    continue
                entry[name] = replay(stocks, signals, first, last, pool, slip, delistings)
                if pool is not None:
                    entry[name]["mean_pool_size"] = float(np.mean([len(pool.get(i - 1, ())) for i in range(first, last + 1)]))
                    entry[name]["buy_and_hold"] = hold_pool(stocks, first, last, pool, slip, delistings)
            report["runs"].append(entry)
    report["hot_topics"] = {days[i]: values for i, values in topics.items()}
    report["pools"] = {
        "steady_month_end": {days[i]: sorted(pool) for i, pool in (steady or {}).items()
                             if i + 1 < len(days) and days[i][:7] != days[i + 1][:7]},
        "aggressive_daily": {days[i]: sorted(pool) for i, pool in (aggressive or {}).items()},
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
