"""Run the standalone daily-bar Brooks H2 experiment on a saved price snapshot.

Usage: python3 brooks_backtest.py [snapshot.json]
The snapshot contains a calendar and stocks with date-keyed [open, close, high, low] rows.
"""

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median

from backtest_picker import BUY_COST, SELL_COST

BREAKOUT_LOOKBACK = 20
MAX_PULLBACK = 20
DEFAULT_SNAPSHOT = Path(__file__).resolve().parent / ".cache" / "brooks-daily.json"


@dataclass(frozen=True)
class Order:
    trigger: float
    stop: float
    peak: float
    strong: bool


@dataclass
class Pullback:
    start: int
    floor: float
    peak: float
    low: float
    attempts: int = 0
    rearmed: bool = False


def strong_bull_bar(bar):
    opening, close, high, low = bar
    return close > opening and (high - close) <= (high - low) / 3


def scan(rows):
    """Return next-session orders and swing lows, keyed by when each became known."""
    bars = sorted(rows.items())
    swing_highs, swing_lows = [], []
    orders, confirmed_lows = {}, {}
    pullback = None
    breakout = None
    follow_through = used_impulse = -BREAKOUT_LOOKBACK - 1

    for i, (day, bar) in enumerate(bars):
        _, close, high, low = bar
        if i:
            previous_high, previous_low = bars[i - 1][1][2:]
            if pullback is None:
                rising_swings = (len(swing_highs) >= 2 and len(swing_lows) >= 2
                                 and swing_highs[-1] > swing_highs[-2]
                                 and swing_lows[-1][1] > swing_lows[-2][1])
                if (rising_swings and follow_through > used_impulse
                        and i - follow_through <= BREAKOUT_LOOKBACK
                        and high <= previous_high and low < previous_low):
                    low_index, floor = swing_lows[-1]
                    peak = max(item[2] for _, item in bars[low_index:i])
                    if floor < low and high < peak:
                        pullback = Pullback(i, floor, peak, low)
                        used_impulse = follow_through
            elif (low <= pullback.floor or high >= pullback.peak
                  or i - pullback.start >= MAX_PULLBACK):
                pullback = None
            else:
                pullback.low = min(pullback.low, low)
                if pullback.attempts == 1 and high < previous_high:
                    pullback.rearmed = True
                if high > previous_high and (pullback.attempts == 0 or pullback.rearmed):
                    pullback.attempts += 1
                    pullback.rearmed = False

        if pullback and pullback.rearmed and i - pullback.start + 1 < MAX_PULLBACK:
            orders[day] = Order(high, pullback.low, pullback.peak, strong_bull_bar(bar))

        # A swing is usable after two bars on its right have closed.
        if i >= 4:
            pivot_day, pivot = bars[i - 2]
            neighbors = [bars[j][1] for j in (i - 4, i - 3, i - 1, i)]
            if pivot[2] > max(item[2] for item in neighbors):
                swing_highs.append(pivot[2])
            if pivot[3] < min(item[3] for item in neighbors):
                swing_lows.append((i - 2, pivot[3]))
                confirmed_lows[day] = (pivot_day, pivot[3])

        if breakout is not None and close > breakout:
            follow_through = i
        breakout = None
        if i >= BREAKOUT_LOOKBACK and strong_bull_bar(bar):
            resistance = max(item[2] for _, item in bars[i - BREAKOUT_LOOKBACK:i])
            if close > resistance:
                breakout = close

    return orders, confirmed_lows


def exit_order(bar, stop, target):
    """Resolve gaps at the open. With two intraday touches assume stop first."""
    opening, _, high, low = bar
    if opening <= stop:
        return opening, "stop"
    if target is not None and opening >= target:
        return opening, "target"
    if low <= stop:
        return stop, "stop"
    if target is not None and high >= target:
        return target, "target"
    return None


@dataclass
class Position:
    entry_day: str
    entry_price: float
    entry_cash: float
    quantity: float
    stop: float
    peak: float
    pending_stop: bool = False
    candidate: tuple | None = None


def simulate(rows, orders, pivots, sessions, prior, *, strong, trail, slip):
    cash, position, last_close = 1.0, None, None
    nav, trades, counts = [], [], Counter()
    high_before = {}
    for day in sessions:
        bar = rows.get(day)
        started_in_position = position is not None
        if bar:
            opening, close, high, low = bar
            last_close = close
            tradable = high > low
            if position:
                target = None if trail else position.peak
                fill = None
                if tradable:
                    fill = ((opening, "stop") if position.pending_stop
                            else exit_order(bar, position.stop, target))
                elif low <= position.stop:
                    position.pending_stop = True
                if fill:
                    price, reason = fill
                    cash = position.quantity * price * (1 - slip) * (1 - SELL_COST)
                    trades.append({
                        "entry": position.entry_day,
                        "exit": day,
                        "entry_price": position.entry_price,
                        "exit_price": price * (1 - slip),
                        "reason": reason,
                        "return": cash / position.entry_cash - 1,
                    })
                    position = None

            if not started_in_position and tradable:
                order = orders.get(prior[day])
                if order and (not strong or order.strong) and high > order.trigger:
                    price = max(opening, order.trigger) * (1 + slip)
                    if opening > order.stop and price < order.peak and price <= high:
                        position = Position(day, price, cash, cash / price * (1 - BUY_COST),
                                            order.stop, order.peak)
                        # A close below the stop confirms the breach after entry.
                        # T+1 makes the next executable open the earliest sale.
                        position.pending_stop = close <= order.stop
                        cash = 0.0
                        high_before = {}
                        counts["entries"] += 1

            if position and trail:
                # Save the peak known before each bar. Once a higher low is
                # confirmed, it can trail the stop after price clears that peak.
                if day == position.entry_day:
                    high_water = position.peak
                high_before[day] = high_water
                high_water = max(high_water, high)
                pivot = pivots.get(day)
                if pivot and pivot[0] in high_before and pivot[0] > position.entry_day:
                    if pivot[1] > position.stop:
                        position.candidate = (pivot[1], high_before[pivot[0]])
                if position.candidate:
                    level, required_high = position.candidate
                    if low <= level:
                        position.candidate = None
                    elif high_water > required_high:
                        position.stop = level
                        position.candidate = None
                        counts["stop_raises"] += 1

        nav.append(cash if position is None else position.quantity * last_close)
        counts["held_days"] += int(position is not None)
    counts["open"] += int(position is not None)
    return nav, trades, counts


def buy_and_hold(rows, sessions, slip):
    quantity = 0.0
    nav, last_close = [], None
    held_days = 0
    for day in sessions:
        bar = rows.get(day)
        if bar:
            opening, last_close, high, low = bar
            if not quantity and high > low:
                quantity = (1 - BUY_COST) / (opening * (1 + slip))
        nav.append(quantity * last_close if quantity else 1.0)
        held_days += bool(quantity)
    return nav, Counter(held_days=held_days, open=int(bool(quantity)))


def stats(nav, trades, counts, capital_days):
    returns = [trade["return"] for trade in trades]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    peak, drawdown = 1.0, 0.0
    for value in nav:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    return {
        "return": nav[-1] - 1,
        "max_drawdown": drawdown,
        "trades": len(trades),
        "win_rate": len(wins) / len(returns) if returns else None,
        "mean_trade": mean(returns) if returns else None,
        "profit_factor": sum(wins) / -sum(losses) if losses else None,
        "exposure": counts["held_days"] / capital_days,
        "exits": dict(Counter(trade["reason"] for trade in trades)),
        "stop_raises": counts["stop_raises"],
        "open": counts["open"],
    }


def run(stocks, calendar, start, end, slip):
    prior = dict(zip(calendar[1:], calendar))
    sessions = [day for day in calendar if start <= day <= end]
    assert sessions and sessions[0] in prior
    count_days = len(sessions) * len(stocks)
    hold_sleeves, hold_counts = [], Counter()
    for rows, _, _ in stocks.values():
        nav, stock_counts = buy_and_hold(rows, sessions, slip)
        hold_sleeves.append(nav)
        hold_counts.update(stock_counts)
    results = {"hold": stats(list(map(mean, zip(*hold_sleeves))), [], hold_counts, count_days)}
    tape = {}
    for strong in (False, True):
        for trail in (False, True):
            name = ("strong" if strong else "all") + ("_trail" if trail else "_peak")
            sleeves, trades, counts = [], [], Counter()
            for code, (rows, orders, pivots) in stocks.items():
                nav, closed, stock_counts = simulate(
                    rows, orders, pivots, sessions, prior, strong=strong, trail=trail, slip=slip)
                sleeves.append(nav)
                trades.extend({"code": code, **trade} for trade in closed)
                counts.update(stock_counts)
            tape[name] = trades
            results[name] = stats(list(map(mean, zip(*sleeves))), trades, counts, count_days)

    paired = {}
    for quality in ("all", "strong"):
        returns = {}
        for method in ("peak", "trail"):
            returns[method] = {(trade["code"], trade["entry"]): trade["return"]
                               for trade in tape[f"{quality}_{method}"]}
        common = returns["peak"].keys() & returns["trail"].keys()
        differences = [returns["trail"][key] - returns["peak"][key] for key in sorted(common)]
        paired[quality] = {"count": len(common),
                           "mean_difference": mean(differences) if differences else None,
                           "median_difference": median(differences) if differences else None,
                           "trail_better": sum(value > 1e-12 for value in differences)}
    return {"start": sessions[0], "end": sessions[-1], "days": len(sessions), "slippage": slip,
            "results": results, "paired_exits": paired}, tape


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", nargs="?", default=DEFAULT_SNAPSHOT, type=Path)
    args = parser.parse_args()
    data = json.loads(args.snapshot.read_text())
    calendar = data["calendar"]
    stocks = {}
    for code, stock in data["stocks"].items():
        rows = stock["rows"]
        orders, pivots = scan(rows)
        stocks[code] = rows, orders, pivots
    runs = []
    for start, end, slip in (("2020-01-01", calendar[-1], 0),
                             ("2020-01-01", "2023-12-31", 0),
                             ("2024-01-01", calendar[-1], 0),
                             ("2020-01-01", calendar[-1], 0.0005)):
        report, tape = run(stocks, calendar, start, end, slip)
        if not runs:
            full_period_trades = tape
        runs.append(report)
    print(json.dumps({"stocks": len(stocks), "bars": sum(len(x[0]) for x in stocks.values()),
                      "runs": runs, "full_period_trades": full_period_trades}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
