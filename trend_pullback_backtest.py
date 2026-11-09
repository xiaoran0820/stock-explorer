"""Replay trend continuation with fixed, partial, and trailing exits.

Uses the same saved A-share daily bars and trading costs as brooks_backtest.py.
Run with: python3 trend_pullback_backtest.py [snapshot.json]
"""

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

from backtest_picker import BUY_COST, SELL_COST, moving_average
from brooks_backtest import DEFAULT_SNAPSHOT, buy_and_hold, exit_order, stats


@dataclass(frozen=True)
class Setup:
    close: float
    stop: float


@dataclass
class Position:
    entry_day: str
    entry_price: float
    entry_cash: float
    quantity: float
    stop: float
    target: float | None
    fraction: float = 1.0
    pending_stop: bool = False


def breakout_setup(close, resistance, average, prior_average, stop):
    if close > resistance and close > average and average > prior_average:
        return Setup(close, stop)
    return None


def scan(rows):
    bars = sorted(rows.items())
    average = moving_average([bar[1] for _, bar in bars], 100)
    setups = {"pullback": {}, "breakout": {}}
    stops = {}
    ema = bars[0][1][1]
    last_breakout = last_touch = used_touch = -100
    for i, (day, (_, close, _, low)) in enumerate(bars):
        ema += (close - ema) * (2 / 21)
        if low <= ema:
            last_touch = i
        if i >= 19:
            stops[day] = min(bar[3] for _, bar in bars[i - 19:i + 1])
        if i < 55:
            continue
        resistance = max(bar[2] for _, bar in bars[i - 55:i])
        breakout = close > resistance
        if breakout:
            last_breakout = i
        if i < 119 or close <= average[i] or average[i] <= average[i - 20]:
            continue
        if breakout:
            stop = min(bar[3] for _, bar in bars[i - 9:i + 1])
            setups["breakout"][day] = breakout_setup(close, resistance, average[i], average[i - 20], stop)
        elif (i - last_breakout <= 30 and i - last_touch <= 4 and last_touch > used_touch
              and close > ema and close > bars[i - 1][1][2]):
            stop = min(bar[3] for _, bar in bars[i - 4:i + 1])
            setups["pullback"][day] = Setup(close, stop)
            used_touch = last_touch
    return setups, stops


def simulate(rows, setups, stops, sessions, prior, method, slip):
    cash, position, last_close = 1.0, None, None
    nav, trades, counts = [], [], Counter()
    for day in sessions:
        bar = rows.get(day)
        started_in_position = position is not None
        if bar:
            opening, close, high, low = bar
            last_close = close
            tradable = high > low
            if position and tradable:
                result = ((opening, "stop") if position.pending_stop
                          else exit_order(bar, position.stop, position.target))
                if result:
                    price, reason = result
                    fraction = position.fraction
                    if reason == "target" and method == "partial":
                        fraction = 0.5
                        position.target = None
                        counts["partial_sales"] += 1
                    cash += position.quantity * fraction * price * (1 - slip) * (1 - SELL_COST)
                    position.fraction -= fraction
                    if position.fraction and low <= position.stop:
                        # A target could precede the stop only if it filled at the open.
                        cash += position.quantity * position.fraction * position.stop * (1 - slip) * (1 - SELL_COST)
                        position.fraction = 0
                        reason = "stop"
                    if position.fraction:
                        # Keep the existing intraday stop. Raise it for the next session.
                        if position.entry_price > position.stop:
                            position.stop = position.entry_price
                            counts["stop_raises"] += 1
                    else:
                        trades.append({"entry": position.entry_day, "exit": day, "reason": reason,
                                       "return": cash / position.entry_cash - 1})
                        position = None
            if not started_in_position and tradable:
                setup = setups.get(prior[day])
                if setup and setup.stop < opening <= 2 * setup.close - setup.stop:
                    risk = opening - setup.stop
                    quantity = cash / (opening * (1 + slip)) * (1 - BUY_COST)
                    position = Position(day, opening, cash, quantity, setup.stop,
                                        opening + risk if method != "trail" else None)
                    cash = 0.0
            if position:
                if (day == position.entry_day and close <= position.stop) or (not tradable and low <= position.stop):
                    position.pending_stop = True
                next_stop = stops.get(day, position.stop)
                if method != "fixed" and next_stop > position.stop:
                    position.stop = next_stop
                    counts["stop_raises"] += 1
        fraction = position.fraction if position else 0.0
        nav.append(cash + (fraction * position.quantity * last_close if position else 0))
        counts["held_days"] += fraction
    counts["open"] += int(position is not None)
    return nav, trades, counts


def run(stocks, calendar, start, end, slip):
    sessions = [day for day in calendar if start <= day <= end]
    prior = dict(zip(calendar[1:], calendar))
    capital_days = len(stocks) * len(sessions)
    hold_sleeves, hold_counts = [], Counter()
    for rows, _, _ in stocks.values():
        nav, counts = buy_and_hold(rows, sessions, slip)
        hold_sleeves.append(nav)
        hold_counts.update(counts)
    results = {"hold": stats(list(map(mean, zip(*hold_sleeves))), [], hold_counts, capital_days)}
    for entry in ("pullback", "breakout"):
        for method in ("fixed", "partial", "trail"):
            sleeves, trades, counts = [], [], Counter()
            for rows, setups, stops in stocks.values():
                nav, closed, stock_counts = simulate(rows, setups[entry], stops, sessions, prior, method, slip)
                sleeves.append(nav)
                trades.extend(closed)
                counts.update(stock_counts)
            report = stats(list(map(mean, zip(*sleeves))), trades, counts, capital_days)
            wins = [trade["return"] for trade in trades if trade["return"] > 0]
            losses = [trade["return"] for trade in trades if trade["return"] < 0]
            report["mean_win"] = mean(wins) if wins else None
            report["mean_loss"] = mean(losses) if losses else None
            report["partial_sales"] = counts["partial_sales"]
            results[f"{entry}_{method}"] = report
    return {"start": sessions[0], "end": sessions[-1], "slippage": slip, "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", nargs="?", type=Path, default=DEFAULT_SNAPSHOT)
    args = parser.parse_args()
    data = json.loads(args.snapshot.read_text())
    calendar = data["calendar"]
    stocks = {code: (item["rows"], *scan(item["rows"]))
              for code, item in data["stocks"].items() if item.get("rows")}
    missing = sorted(data["stocks"].keys() - stocks.keys())
    if not stocks:
        parser.error("snapshot has no usable price series")
    windows = (("2020-01-01", calendar[-1], 0), ("2020-01-01", "2023-12-31", 0),
               ("2024-01-01", calendar[-1], 0), ("2020-01-01", calendar[-1], 0.0005))
    print(json.dumps({"stocks": len(stocks), "missing_stocks": missing,
                      "runs": [run(stocks, calendar, *window) for window in windows]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
