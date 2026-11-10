"""Order and fill rules shared by the offline backtests.

The backtests only see daily bars (open, close, high, low), never the order
book, so every fill is an assumption. Results are reported under two sets:

ideal         An order at the open fills at the open price on any day the stock
              traded at more than one price (high > low). A stop fills at the
              stop price, or at the open when the open is already past it.
conservative  The ideal rules plus three restrictions:
              - no buy at the open when the stock opens at its limit-up price;
              - no sale on a day the stock closes at its limit-down price, so a
                triggered stop waits for the next open where the stock can trade;
              - in the market-pool replay, an entry may use at most PARTICIPATION
                of the stock's 20-day median daily turnover, for an account of
                ACCOUNT yuan.

Both sets trade in the session after a signal, keep A-share T+1 (nothing bought
today is sold today), charge the same costs, and assume the stop came first when
one bar touched both the stop and the target. Limits are checked on raw prices,
the adjusted prices divided by their adjustment factor: market_pool_backtest.limit_masks
does this for the market-wide replay, and build_price_snapshot.py stores the same
flags in the fixed-sample snapshots, which hold adjusted prices only. ST stocks
(5% limit) are not identified, so they are checked against their board's normal limit.
"""

from backtest_picker import BUY_COST, SELL_COST, beijing_stock

IDEAL, CONSERVATIVE = "ideal", "conservative"
MODES = (IDEAL, CONSERVATIVE)
CHINEXT_WIDER_LIMIT = "2020-08-24"  # ChiNext moved from a 10% to a 20% daily limit
# Limit prices are rounded to the 0.01-yuan tick (the Beijing exchange rounds down),
# so a raw price within one tick of the exact limit counts as at it.
TICK = 0.01
PARTICIPATION = 0.05
ACCOUNT = 1_000_000


def price_limit(code, day):
    """Daily price limit as a fraction of the previous close, by board.

    code may carry an exchange prefix (sz300750) or not (300750)."""
    digits = code[-6:]
    if digits.startswith("68"):
        return 0.20
    if digits.startswith("30"):
        return 0.20 if day >= CHINEXT_WIDER_LIMIT else 0.10
    if beijing_stock(digits):
        return 0.30
    return 0.10


def opens_at_limit_up(opening, reference, limit, tolerance):
    """Whether the open sits at the limit-up price set by the previous close.

    Works on floats and NumPy arrays alike; NaN compares False."""
    return opening >= reference * (1 + limit) - tolerance


def closes_at_limit_down(close, reference, limit, tolerance):
    """Whether the close sits at the limit-down price set by the previous close."""
    return close <= reference * (1 - limit) + tolerance


def snapshot_blocks(stock):
    """Conservative-mode blocks stored with a fixed-sample snapshot stock.

    Returns {day: (no_buy_at_open, no_sale)}. build_price_snapshot.py checks the
    limits on raw prices when it cuts the snapshot; the rows alone cannot, because
    adjusted prices hide the tick size."""
    if "limits" not in stock:
        raise ValueError("the snapshot has no price-limit flags; rebuild it with build_price_snapshot.py")
    return {day: (bool(no_buy), bool(no_sale)) for day, (no_buy, no_sale) in stock["limits"].items()}


def buy_quantity(cash, price, slip):
    """Shares bought with cash at price, after slippage and buying costs."""
    return cash / (price * (1 + slip)) * (1 - BUY_COST)


def sale_value(quantity, price, slip):
    """Cash received for quantity sold at price, after slippage and selling costs."""
    return quantity * price * (1 - slip) * (1 - SELL_COST)


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
