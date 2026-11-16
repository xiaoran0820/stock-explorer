"""Replay the quiet-stock portfolio: low volatility, no trading surge, no recent spike.

At each month-end every stock that has traded for at least a year and trades at
least 30 million yuan a day (20-day median) is ranked three ways, lowest first:
- volatility: standard deviation of daily log returns over the last year;
- surge: average shares traded over the last 20 sessions against the last 250,
  with shares estimated as amount / adjusted close, so a price move alone does
  not look like heavier trading;
- spike: the largest one-day gain over the last 20 sessions.
The three places are added. The portfolio holds the 50 stocks with the lowest
sum at equal weight: the list is set at the month-end close, bought at the next
open and rebalanced to equal weight whenever it changes. A holding stays while
it ranks within the best 80; free places go to the best-ranked stocks not held.

The same procedure from the other end of the ranking gives the "loud" portfolio.
Both are compared with the whole eligible universe at equal weight and with the
dividend low-volatility pool of market_pool_backtest, under the ideal and the
conservative fill rules of execution.py, with 0 and 0.05% slippage per side.
Requires NumPy. Run python3 quiet_stock_backtest.py > .cache/quiet-stock-results.json.
"""

import argparse
import json
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import date, timedelta
from math import sqrt
from pathlib import Path

import numpy as np

from execution import CONSERVATIVE, MODES
from market_pool_backtest import (DIVIDEND_MIN_TURNOVER, ROOT, hold_pool, iso_day, limit_masks, load_market,
                                  load_stock_metadata, mask_before_listing, monthly_dividend_pools, read_dividends,
                                  reconcile_codes)

SIZE = 50  # names held
KEEP = 80  # a holding stays while it ranks within this many places
HISTORY = 250  # sessions a stock must have traded before it is ranked
WINDOW = 20  # sessions behind the surge and spike measures
PERIODS = {"2020-2023": ("2020-01-01", "2023-12-31"), "2024-2026": ("2024-01-01", "2026-12-31")}


def month_ends(days):
    return [i for i in range(len(days) - 1) if days[i][:7] != days[i + 1][:7]]


def quiet_measures(stock, i):
    """(volatility, surge, spike) at the close of session i.

    None when the stock is not eligible: no trade that session, less than a year
    of trading, or a 20-day median turnover under 30 million yuan."""
    if i < 252 or not np.isfinite(stock.bars[i, 1]):
        return None
    amount = stock.amount[i - HISTORY + 1:i + 1].astype(np.float64)
    if np.median(amount[-WINDOW:]) < DIVIDEND_MIN_TURNOVER:
        return None
    closes = stock.bars[i - 252:i + 1, 1].astype(np.float64)
    traded = closes[np.isfinite(closes)]
    if len(traded) < 231 or not np.isfinite(stock.bars[:i - HISTORY + 1, 1]).any():
        return None
    volatility = float(np.std(np.diff(np.log(traded)), ddof=1))
    prices = stock.bars[i - HISTORY + 1:i + 1, 1].astype(np.float64)
    shares = np.zeros(HISTORY)
    np.divide(amount, prices, out=shares, where=np.isfinite(prices))
    surge = float(shares[-WINDOW:].mean() / shares.mean())
    recent = closes[-WINDOW - 1:]
    recent = recent[np.isfinite(recent)]
    spike = float(np.max(recent[1:] / recent[:-1] - 1))
    return (volatility, surge, spike) if volatility > 0 else None


def rank_quiet(rows):
    """Codes from quietest to loudest by the sum of their places on the three
    measures, lowest first; ties go to the lower volatility, then the code."""
    total = Counter()
    for k in range(3):
        for place, code in enumerate(sorted(rows, key=lambda code: (rows[code][k], code))):
            total[code] += place
    return sorted(rows, key=lambda code: (total[code], rows[code][0], code))


def buffered_pick(ranked, held, size=SIZE, keep=KEEP):
    """Holdings still within the first `keep` places stay; free places go to the
    best-ranked codes not held."""
    chosen = set(held) & set(ranked[:keep])
    for code in ranked:
        if len(chosen) >= size:
            break
        chosen.add(code)
    return frozenset(chosen)


def monthly_pools(stocks, days, size=SIZE, keep=KEEP):
    """Selections keyed by session, as hold_pool reads them: the quiet portfolio,
    the loud one and the whole eligible universe, each set at a month-end close
    and kept until the next month-end."""
    ends = set(month_ends(days))
    quiet, loud, universe = {}, {}, {}
    current = (frozenset(), frozenset(), frozenset())
    for i in range(len(days)):
        if i in ends:
            rows = {code: values for code, stock in stocks.items() if (values := quiet_measures(stock, i))}
            ranked = rank_quiet(rows)
            current = (buffered_pick(ranked, current[0], size, keep),
                       buffered_pick(ranked[::-1], current[1], size, keep), frozenset(rows))
        quiet[i], loud[i], universe[i] = current
    return quiet, loud, universe


def segment(nav, sessions, origin, begin, finish):
    """Return, annualized return, volatility and max drawdown from begin to finish.

    nav[k] is the portfolio value at the close of sessions[k]; origin is the
    session before sessions[0], when the value was 1."""
    lo, hi = bisect_left(sessions, begin), bisect_right(sessions, finish)
    if lo >= hi:
        return None
    base = nav[lo - 1] if lo else 1.0
    values = np.array([base, *nav[lo:hi]], dtype=float)
    daily = values[1:] / values[:-1] - 1
    years = (date.fromisoformat(sessions[hi - 1]) - date.fromisoformat(sessions[lo - 1] if lo else origin)).days / 365.25
    total = float(values[-1] / base - 1)
    return {"from": sessions[lo], "to": sessions[hi - 1], "return": total,
            "annual_return": (1 + total) ** (1 / years) - 1,
            "volatility": float(np.std(daily, ddof=1) * sqrt(len(daily) / years)) if len(daily) > 1 else None,
            "max_drawdown": float(min(0.0, (values / np.maximum.accumulate(values) - 1).min()))}


def path_stats(nav, days, first, last):
    """Annualized figures, calendar-year returns and the two sub-periods of a run."""
    sessions, origin = days[first:last + 1], days[first - 1]
    whole = segment(nav, sessions, origin, sessions[0], sessions[-1])
    years = {str(year): segment(nav, sessions, origin, f"{year}-01-01", f"{year}-12-31")["return"]
             for year in range(int(sessions[0][:4]), int(sessions[-1][:4]) + 1)}
    periods = {name: segment(nav, sessions, origin, begin, finish) for name, (begin, finish) in PERIODS.items()}
    return {"annual_return": whole["annual_return"], "volatility": whole["volatility"], "years": years,
            "periods": {name: value for name, value in periods.items() if value}}


def names_replaced(selection, ends):
    """Average number of names that enter the list at each month-end after the first."""
    changes = [len(selection[b] - selection[a]) for a, b in zip(ends, ends[1:])]
    return sum(changes) / len(changes) if changes else 0.0


def industry_mix(selection, ends, industries, top=8):
    """Average share of the list in each industry over the month-ends, largest first."""
    lists = [selection[i] for i in ends if selection[i]]
    shares = Counter()
    for members in lists:
        for code in members:
            shares[industries.get(code) or "unknown"] += 1 / len(members) / len(lists)
    return [[name, share] for name, share in shares.most_common(top)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT)
    parser.add_argument("--start", default="2020-01-02")
    parser.add_argument("--end", default="2026-11-06")
    args = parser.parse_args()
    root = args.data
    warmup = (date.fromisoformat(args.start) - timedelta(days=460)).isoformat()
    days, stocks, _ = load_market(root / "qlib_bin.tar.gz", warmup, args.end)
    aliases = reconcile_codes(stocks, root / "bse-code-map.csv")
    start, end = bisect_left(days, args.start), len(days) - 1
    if start >= end or start <= 252:
        parser.error("price archive has no coverage for the requested dates and the year before them")
    metadata = load_stock_metadata(root / "stocks.csv")
    mask_before_listing(stocks, metadata, days)
    delistings = {code: bisect_left(days, iso_day(row["delist_date"])) for code, row in metadata.items()
                  if row["delist_date"] and code in stocks}
    quiet, loud, universe = monthly_pools(stocks, days)
    dividends, bonuses = read_dividends(root / "dividends", aliases)
    portfolios = {"quiet": quiet, "loud": loud, "universe": universe,
                  "dividend_low_vol": monthly_dividend_pools(stocks, dividends, days, bonuses) if dividends else None}
    masks = limit_masks(stocks, days)
    ends = [i for i in month_ends(days) if start - 1 <= i < end]
    industries = {code: row.get("industry") for code, row in metadata.items()}
    report = {"start": days[start], "end": days[end],
              "rules": {"size": SIZE, "keep": KEEP, "history_sessions": HISTORY, "window_sessions": WINDOW,
                        "min_turnover_yuan": DIVIDEND_MIN_TURNOVER},
              "execution": list(MODES),
              "universe_mean_size": float(np.mean([len(universe[i]) for i in ends])),
              "names_replaced_per_month": {"quiet": names_replaced(quiet, ends), "loud": names_replaced(loud, ends)},
              "industry_mix": {name: industry_mix(selection, ends, industries)
                               for name, selection in (("quiet", quiet), ("loud", loud), ("universe", universe))},
              "runs": []}
    for mode in MODES:
        blocks = masks if mode == CONSERVATIVE else None
        for slip in (0, 0.0005):
            entry = {"execution": mode, "slippage": slip}
            for name, selection in portfolios.items():
                if selection is None:
                    continue
                values = []
                result = hold_pool(stocks, start, end, selection, slip, delistings, blocks=blocks, values=values)
                result.update(path_stats(values, days, start, end))
                entry[name] = result
            report["runs"].append(entry)
    report["pools"] = {name + "_month_end": {days[i]: sorted(selection[i]) for i in ends}
                       for name, selection in (("quiet", quiet), ("loud", loud))}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
