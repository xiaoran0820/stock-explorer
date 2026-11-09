"""Read the monthly dividend pool and evaluate the verified daily breakout rule."""

import json
from datetime import date, time, timedelta
from functools import lru_cache
from math import isfinite
from pathlib import Path

from backtest_picker import moving_average
from trend_pullback_backtest import breakout_setup

SNAPSHOT = Path(__file__).resolve().parent / ".cache" / "steady-pool.json"
STRATEGY = "55 日突破 · 1R"


@lru_cache(maxsize=2)
def _read_snapshot(path, version):
    with path.open() as source:
        snapshot = json.load(source)
    if not (snapshot.get("as_of") and snapshot.get("members") and isinstance(snapshot.get("stocks"), dict)):
        raise ValueError("invalid steady strategy snapshot")
    return snapshot


def read_snapshot(path=SNAPSHOT):
    return _read_snapshot(path, path.stat().st_mtime_ns)


def pool_period(snapshot, now):
    as_of = snapshot["as_of"]
    year, month = (int(part) for part in as_of[:7].split("-"))
    next_month = f"{year + (month == 12):04d}-{month % 12 + 1:02d}"
    return {"as_of": as_of, "active_month": next_month,
            "stale": now.strftime("%Y-%m") > next_month,
            "yield_floor": snapshot["yield_floor"], "turnover_floor": snapshot["turnover_floor"]}


def closed_bars(bars, now):
    """Exclude the open session and ignore malformed bars from an upstream feed."""
    today = now.date().isoformat()
    cutoff_includes_today = now.time() >= time(15, 5)
    by_date = {}
    for bar in bars:
        try:
            day = date.fromisoformat(bar["date"]).isoformat()
        except (TypeError, ValueError):
            continue
        if day > today or (day == today and not cutoff_includes_today):
            continue
        if (all(isfinite(bar[key]) and bar[key] > 0 for key in ("open", "close", "high", "low"))
                and bar["low"] <= min(bar["open"], bar["close"])
                and max(bar["open"], bar["close"]) <= bar["high"]):
            by_date[day] = {**bar, "date": day}
    return [by_date[day] for day in sorted(by_date)]


def verified_previous_session(sessions, prior, latest):
    if latest in sessions[1:]:
        return sessions[sessions.index(latest) - 1]
    previous_day = date.fromisoformat(prior)
    gap = (date.fromisoformat(latest) - previous_day).days
    if 1 <= gap <= 3 and all((previous_day + timedelta(days=i)).weekday() >= 5 for i in range(1, gap)):
        return prior
    return None


def entry_open_passed(as_of, now):
    """Whether the session after the signal day has already opened (09:30 Beijing).

    Without a holiday calendar a weekday holiday counts as a session, so a plan
    is retired early rather than shown after its entry open has passed."""
    session = date.fromisoformat(as_of) + timedelta(days=1)
    while session.weekday() >= 5:
        session += timedelta(days=1)
    today = now.date()
    return today > session or (today == session and now.time() >= time(9, 30))


def breakout_reading(bars, pool_day, previous_session=None):
    if not bars:
        return None
    recent = bars[-1]
    result = {"as_of": recent["date"], "close": recent["close"], "enough": len(bars) >= 120}
    if not result["enough"]:
        return result

    averages = moving_average([bar["close"] for bar in bars], 100)

    def levels(index):
        return (max(bar["high"] for bar in bars[index - 55:index]), averages[index], averages[index - 20],
                min(bar["low"] for bar in bars[index - 9:index + 1]))

    high, average, prior_average, stop = levels(len(bars) - 1)
    close = recent["close"]
    setup = breakout_setup(close, high, average, prior_average, stop)
    result.update(high_55=high, ma_100=average, prior_ma_100=prior_average, above_ma=close > average,
                  rising_ma=average > prior_average, breakout=close > high, stop=stop,
                  setup=setup is not None, indicative_target=2 * close - stop if setup else None,
                  max_open=2 * close - stop if setup else None)

    # Explain a fill or skip following the preceding session's setup. The
    # rules prohibit a sale on that entry day, including a touched target.
    if len(bars) >= 121 and pool_day and bars[-2]["date"] == previous_session and previous_session >= pool_day:
        previous = bars[-2]
        prior_setup = breakout_setup(previous["close"], *levels(len(bars) - 2))
        if prior_setup:
            opening = recent["open"]
            filled = recent["high"] > recent["low"] and prior_setup.stop < opening <= 2 * prior_setup.close - prior_setup.stop
            result["previous_plan"] = {"signal_day": previous["date"], "entry_day": recent["date"],
                                       "opening": opening, "filled": filled, "stop": prior_setup.stop,
                                       "target": 2 * opening - prior_setup.stop if filled else None,
                                       "stop_due": filled and close <= prior_setup.stop}
    return result
