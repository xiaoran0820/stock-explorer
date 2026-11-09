"""回测选股：用前复权日线对一批股票做历史回测，按策略表现排名。

两种规则都在收盘后给出信号、下一个交易日开盘成交，不使用未来数据：
- 双均线：MA5 上穿 MA20（金叉）买入，下穿（死叉）卖出。
- 动量趋势：收盘价高于 20 个交易日前、并且站在 20 日均线上方时持有，任一条件不满足就卖出。

收益按比例计算（不受 100 股一手的限制，便于不同价位的股票横向比较），
并扣除交易费用：佣金万分之三、印花税万分之五（仅卖出）、过户费十万分之一。
"""
from concurrent.futures import ThreadPoolExecutor
from math import isfinite
import re

TENCENT_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
BUY_COST = 0.0003 + 0.00001            # 佣金 + 过户费
SELL_COST = 0.0003 + 0.0005 + 0.00001  # 佣金 + 印花税 + 过户费
WINDOWS = {"half": (120, "近半年"), "year": (250, "近一年"), "two_years": (500, "近两年")}  # 交易日数
WARMUP = 25    # 回测区间之前多取的 K 线，用来算出区间第一天的均线和动量
MIN_DAYS = 60  # 区间内至少要有这么多个交易日才回测
# 每只股票只取一次最长区间的数据，切换半年/一年/两年时直接截取，不重复请求
MAX_BARS = max(days for days, _ in WINDOWS.values()) + WARMUP
MAX_SOURCE_FAILURES = 3  # 腾讯连续失败这么多次（多半是被限流），本次回测余下的股票直接用备用数据源
SHORT_MA, LONG_MA, MOMENTUM_DAYS = 5, 20, 20
STRATEGIES = {
    "ma_cross": {"name": "双均线", "rule": f"MA{SHORT_MA} 上穿 MA{LONG_MA}（金叉）买入，下穿（死叉）卖出"},
    "momentum": {"name": "动量趋势",
                 "rule": f"收盘价高于 {MOMENTUM_DAYS} 个交易日前、且站在 {LONG_MA} 日均线上方时持有，否则卖出"},
}


def number(value):
    try:
        parsed = float(value)
        return parsed if isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


# ─── 行情数据 ───

def parse_tencent_kline(body, symbol):
    """腾讯财经返回 [日期, 开盘, 收盘, 最高, 最低, 成交量, ...]；有复权事件时放在 qfqday，否则在 day。"""
    data = body.get("data") if isinstance(body, dict) else None
    node = data.get(symbol) if isinstance(data, dict) else None
    rows = (node.get("qfqday") or node.get("day")) if isinstance(node, dict) else None
    bars = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, list) or len(row) < 5 or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", str(row[0])):
            continue
        open_, close, high, low = (number(value) for value in row[1:5])
        if all(value is not None and value > 0 for value in (open_, close, high, low)):
            bars[row[0]] = {"date": row[0], "open": open_, "close": close, "high": high, "low": low}
    return [bars[day] for day in sorted(bars)]


def fetch_daily_bars(get, market, code, count):
    """最近 count 根前复权日线。交易时段内最后一根是盘中数据。"""
    symbol = f"{'sh' if market == 1 else 'sz'}{code}"
    response, _ = get(TENCENT_KLINE, params={"param": f"{symbol},day,,,{count},qfq"}, timeout=8)
    try:
        body = response.json() if response is not None else None
    except ValueError:
        body = None
    return parse_tencent_kline(body, symbol)


# ─── 买卖规则：返回每天收盘后的目标仓位（1 持有 / 0 空仓） ───

def moving_average(values, window):
    return [sum(values[i + 1 - window:i + 1]) / window if i >= window - 1 else None for i in range(len(values))]


def ma_cross_positions(closes, short=SHORT_MA, long=LONG_MA):
    """金叉后持有、死叉后空仓。前后两天均线都算得出来、并且真的穿越了才算信号，
    所以均线刚算出来时短线已经在上方，不会凭空买入。"""
    fast, slow = moving_average(closes, short), moving_average(closes, long)
    positions, holding = [], 0
    for i in range(len(closes)):
        if i and None not in (fast[i - 1], slow[i - 1], fast[i], slow[i]):
            if fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]:
                holding = 1
            elif fast[i - 1] > slow[i - 1] and fast[i] <= slow[i]:
                holding = 0
        positions.append(holding)
    return positions


def momentum_positions(closes, days=MOMENTUM_DAYS, window=LONG_MA):
    average = moving_average(closes, window)
    return [int(i >= days and average[i] is not None and close > closes[i - days] and close > average[i])
            for i, close in enumerate(closes)]


# ─── 回测 ───

def run_backtest(bars, positions, start):
    """回测 bars[start:]（start ≥ 1，之前的 K 线只用来算指标）。
    第 i 天开盘执行第 i-1 天收盘时的目标仓位，返回收益、回撤和交易统计。"""
    nav = peak = 1.0
    max_drawdown, held_days = 0.0, 0
    entry, trade_returns = None, []
    for i in range(start, len(bars)):
        bar, previous = bars[i], bars[i - 1]
        if positions[i - 1] and entry is None:      # 开盘买入，持有到收盘
            nav *= (1 - BUY_COST) * bar["close"] / bar["open"]
            entry = bar
        elif not positions[i - 1] and entry is not None:  # 开盘卖出
            nav *= bar["open"] / previous["close"] * (1 - SELL_COST)
            trade_returns.append(bar["open"] / entry["open"] * (1 - BUY_COST) * (1 - SELL_COST) - 1)
            entry = None
        elif entry is not None:
            nav *= bar["close"] / previous["close"]
        held_days += entry is not None
        peak = max(peak, nav)
        max_drawdown = min(max_drawdown, nav / peak - 1)
    trades = len(trade_returns)
    return {"total_return": nav - 1, "max_drawdown": max_drawdown, "trades": trades,
            "win_rate": sum(r > 0 for r in trade_returns) / trades if trades else None,
            "exposure": held_days / (len(bars) - start), "position_open": entry is not None}


def signal_state(positions, bars):
    """最新收盘后的信号：buy 新出现买入、hold 持有中、sell 新出现卖出、flat 空仓。"""
    last = positions[-1]
    before = positions[-2] if len(positions) > 1 else 0
    signal = "buy" if last and not before else "hold" if last else "sell" if before else "flat"
    since = len(positions) - 1
    while since > 0 and positions[since - 1] == last:
        since -= 1
    return {"signal": signal, "since": bars[since]["date"], "as_of": bars[-1]["date"]}


def backtest_stock(bars, days):
    """对一只股票跑两种规则；区间内交易日太少时返回 None。"""
    start = max(WARMUP, len(bars) - days)
    if len(bars) - start < MIN_DAYS:
        return None
    closes = [bar["close"] for bar in bars]
    # 对照：区间第一天开盘买入后一直持有（同样扣买入费用）
    benchmark = (1 - BUY_COST) * bars[-1]["close"] / bars[start]["open"] - 1
    results = {}
    for key, positions in (("ma_cross", ma_cross_positions(closes)), ("momentum", momentum_positions(closes))):
        stats = run_backtest(bars, positions, start)
        stats.update(signal_state(positions, bars))
        stats["excess_return"] = stats["total_return"] - benchmark
        # 入选：策略赚钱、跑赢持有不动，并且最新信号仍是持有
        stats["picked"] = stats["total_return"] > 0 and stats["excess_return"] > 0 and stats["signal"] in {"buy", "hold"}
        results[key] = stats
    return {"start": bars[start]["date"], "end": bars[-1]["date"], "days": len(bars) - start,
            "last_close": bars[-1]["close"], "benchmark_return": benchmark, "results": results}


def latest_signals(bars):
    """两种规则在最新收盘后的信号（不需要完整回测区间，持仓分析用）；数据不够算 20 日均线时返回 None。"""
    if len(bars) < LONG_MA + 1:
        return None
    closes = [bar["close"] for bar in bars]
    return {key: signal_state(positions, bars)
            for key, positions in (("ma_cross", ma_cross_positions(closes)), ("momentum", momentum_positions(closes)))}


def load_bars(get, cache_get, cache_set, stocks, fallback=None, workers=4):
    """并发取每只股票最近 MAX_BARS 根日线，返回 [(stock, {"bars": [...], "adjusted": bool}), ...]。

    fallback(market, code, count) 是备用数据源（不复权），腾讯取不到时使用，此时 adjusted=False。
    """
    source = {"failures": 0}

    def fetch(stock):
        bars = []
        if source["failures"] < MAX_SOURCE_FAILURES:
            try:
                bars = fetch_daily_bars(get, stock["market"], stock["code"], MAX_BARS)
            except Exception:  # 单只股票出错不影响其他股票
                bars = []
            source["failures"] = 0 if bars else source["failures"] + 1
        if bars or fallback is None:
            return {"bars": bars, "adjusted": True}
        try:
            return {"bars": fallback(stock["market"], stock["code"], MAX_BARS) or [], "adjusted": False}
        except Exception:
            return {"bars": [], "adjusted": False}

    def load(stock):
        key = f"pick_kline:{stock['code']}"
        data = cache_get(key)
        if data is None:
            data = fetch(stock)
            if data["bars"]:
                cache_set(key, data)
        return stock, data

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(load, stocks))


def pick_stocks(get, cache_get, cache_set, stocks, window, fallback=None, workers=4):
    """取行情并回测。返回 (结果, 取不到行情的股票, 数据太短跳过的股票)。"""
    days = WINDOWS[window][0]
    rows, failed, skipped = [], [], []
    for stock, data in load_bars(get, cache_get, cache_set, stocks, fallback, workers):
        brief = {"code": stock["code"], "name": stock.get("name", "")}
        if not data["bars"]:
            failed.append(brief)
            continue
        result = backtest_stock(data["bars"][-(days + WARMUP):], days)
        if result is None:
            skipped.append(brief)
        else:
            rows.append({**stock, **result, "adjusted": data["adjusted"]})
    return rows, failed, skipped
