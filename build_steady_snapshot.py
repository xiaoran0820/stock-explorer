"""Build the website's compact monthly pool from the verified market-data archive."""

import argparse
import json
from datetime import date, timedelta
from math import sqrt
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np

from market_pool_backtest import (
    DIVIDEND_MIN_TURNOVER, DIVIDEND_POOL_SIZE, ROOT, dividend_metrics, load_market,
    load_stock_metadata, mask_before_listing, rank_low_volatility, read_dividends, reconcile_bse,
)
from steady_strategy import SNAPSHOT, breakout_reading


def build(root, through):
    warmup = (date.fromisoformat(through) - timedelta(days=520)).isoformat()
    days, stocks, _ = load_market(root / "qlib_bin.tar.gz", warmup, through)
    aliases = reconcile_bse(stocks, root / "bse-code-map.csv")
    metadata = load_stock_metadata(root / "stocks.csv")
    mask_before_listing(stocks, metadata, days)
    dividends, bonuses = read_dividends(root / "dividends", aliases)
    month_end = max(i for i in range(len(days) - 1) if days[i][:7] != days[i + 1][:7])
    as_of = days[month_end]

    metrics = {}
    scores = []
    for symbol, stock in stocks.items():
        paid = dividends.get(symbol, {})
        row = dividend_metrics(stock, paid, sorted(paid), days, month_end, bonuses.get(symbol))
        if row["eligible"]:
            scores.append((symbol, row["yield"], row["volatility"]))
        metrics[symbol] = row

    yield_floor, ranked = rank_low_volatility(scores)
    if len(ranked) < DIVIDEND_POOL_SIZE:
        raise ValueError("insufficient cross-sectional data for the monthly pool")
    ranks = {symbol: rank for rank, (symbol, _, _) in enumerate(ranked, 1)}
    members = [symbol for symbol, _, _ in ranked[:DIVIDEND_POOL_SIZE]]
    member_set = set(members)
    output = {"as_of": as_of, "archive_through": days[-1], "sessions": days[-50:],
              "members": members, "stocks": {}, "yield_floor": yield_floor,
              "turnover_floor": DIVIDEND_MIN_TURNOVER}
    for symbol, stock in stocks.items():
        valid = np.flatnonzero(np.isfinite(stock.bars[:, 1]))
        if not len(valid):
            continue
        last = valid[-1]
        # Skip archived companies already delisted. They remain in backtests.
        known = metadata.get(symbol, {})
        delisted = known.get("delist_date")
        if delisted and delisted <= days[-1].replace("-", ""):
            continue
        factor = float(stock.factor[last])
        bars = []
        if factor > 0:
            for i in valid[-121:]:
                bar = dict(zip(("open", "close", "high", "low"), (float(v) / factor for v in stock.bars[i])))
                bars.append({**bar, "date": days[i]})
        row = metrics[symbol]
        row["volatility"] = row["volatility"] * sqrt(252) if row["volatility"] is not None else None
        output["stocks"][symbol] = {
            "code": symbol[2:], "exchange": symbol[:2], "name": known.get("name") or symbol[2:],
            "selection": {**row, "rank": ranks.get(symbol), "selected": symbol in member_set},
            "archived": breakout_reading(bars, as_of if symbol in member_set else None,
                                         days[last - 1] if last else None),
        }
    if any(symbol not in output["stocks"] for symbol in members):
        raise ValueError("monthly candidates require a valid stock record")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT)
    parser.add_argument("--through", default=date.today().isoformat())
    parser.add_argument("--output", type=Path, default=SNAPSHOT)
    args = parser.parse_args()
    snapshot = build(args.data, args.through)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(snapshot, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            stream.flush()
            temporary.replace(args.output)
        finally:
            temporary.unlink(missing_ok=True)
    print(f"{snapshot['as_of']} · {len(snapshot['members'])} 只候选 · 日线归档至 {snapshot['archive_through']}")


if __name__ == "__main__":
    main()
