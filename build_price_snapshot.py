"""Rebuild the fixed-sample price snapshots from the local Qlib archive.

brooks_backtest.py and trend_pullback_backtest.py replay a hand-picked list of
large caps from a JSON snapshot of date-keyed [open, close, high, low] rows.
Prices here are forward-adjusted by multiplication: each bar is scaled by its
adjustment factor over the factor on the stock's last bar, so the last bar shows
the real traded price and day-to-day percentage moves equal total returns.

An additive adjustment, which subtracts every later cash dividend, is not used:
it pushes the early prices of big dividend payers toward zero (China Shenhua
fell to 0.18 yuan in 2020) and inflates their percentage moves.

Each stock also gets "limits": the sessions on which the conservative fill rules
refuse a buy at the open (limit-up open) or a sale (limit-down close), as
{day: [no_buy, no_sale]}. Adjusted prices hide the 0.01-yuan tick, so the flags
are computed here on raw prices by market_pool_backtest.limit_masks, the same
check the market-wide replay uses.

Requires NumPy. Usage: python3 build_price_snapshot.py [--start ...] [--end ...]
"""

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from backtest_picker import beijing_stock
from market_pool_backtest import ROOT, limit_masks, load_market

OUTPUT = Path(__file__).resolve().parent / ".cache"
SAMPLES = {
    # 24 large caps from different industries.
    "brooks-daily.json": (
        "600036", "601318", "601166", "600030", "600519", "000858", "600887", "603288",
        "600276", "000538", "300760", "600196", "600900", "601088", "601899", "600031",
        "002415", "000063", "002475", "600570", "000333", "600690", "002594", "300750",
    ),
    # A second, non-overlapping list used only to check the 24-stock findings.
    "trend-validation-snapshot.json": (
        "601398", "600000", "601628", "600585", "600028", "601857",
        "600309", "600019", "600104", "000725", "002230", "300059",
    ),
}


def archive_code(code):
    return ("sh" if code.startswith("6") else "bj" if beijing_stock(code) else "sz") + code


def snapshot(days, stocks, names, codes):
    keys = {code: archive_code(code) for code in codes}
    masks = limit_masks({key: stocks[key] for key in keys.values() if key in stocks}, days)
    output = {}
    for code, key in keys.items():
        stock = stocks.get(key)
        valid = np.flatnonzero(np.isfinite(stock.bars[:, 1])) if stock is not None else ()
        if not len(valid):
            output[code] = {"name": names.get(code, ""), "error": "no bars in the archive"}
            continue
        scale = 1 / float(stock.factor[valid[-1]])
        rows = {days[i]: [round(float(value) * scale, 4) for value in stock.bars[i]] for i in valid}
        no_buy, no_sale = masks[key]
        limits = {days[i]: [bool(no_buy[i]), bool(no_sale[i])] for i in valid if no_buy[i] or no_sale[i]}
        output[code] = {"name": names.get(code, ""), "rows": rows, "limits": limits}
    return {"calendar": days, "stocks": output}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT)
    parser.add_argument("--start", default="2019-07-01")
    parser.add_argument("--end", default="2026-11-06")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    days, stocks, _ = load_market(args.data / "qlib_bin.tar.gz", args.start, args.end)
    with (args.data / "stocks.csv").open() as stream:
        names = {row["code"]: row["name"] for row in csv.DictReader(stream)}
    for filename, codes in SAMPLES.items():
        data = snapshot(days, stocks, names, codes)
        path = args.output / filename
        path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        usable = sum("rows" in item for item in data["stocks"].values())
        print(f"{path}: {usable}/{len(codes)} stocks, {len(days)} sessions {days[0]}..{days[-1]}")


if __name__ == "__main__":
    main()
