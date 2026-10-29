"""Backtest: replay all history through the same score() the live bot uses, then compare with known events.

The milestone windows come from the 27/10/2026 research note, so every result here is in-sample (n = 4).
It shows whether the pipeline reproduces what the note says; it is not evidence that the rules predict.
"""
import logging

import pandas as pd

from . import indicators, sources
from .scoring import State, score

log = logging.getLogger(__name__)
# The state each milestone is expected to switch on.
EXPECTED_STATE = {"top": "top_zone", "panic": "stress_on", "bottom": "stress_on", "recovery": "recovery_on"}
STRESS_WINDOWS = ("panic", "bottom")


def replay(flags, cfg):
    """Day-by-day verdicts, carrying the hysteresis state exactly as the live runner does."""
    state, rows = State(), []
    for day, row in flags.iterrows():
        verdict = score(row, cfg, state)
        state = verdict.state
        rows.append({
            "date": day, "stress_on": state.stress_on, "recovery_on": state.recovery_on,
            "regime": state.regime, "top_zone": state.top_zone, "stress_hits": len(verdict.stress_hits),
            "stress_available": verdict.stress_available, "events": "|".join(verdict.events),
        })
    return pd.DataFrame(rows).set_index("date")


def evaluate(days, cfg):
    """Per milestone: was the expected state on at any point in the window, and could a verdict be formed?"""
    min_available = cfg["stress"]["min_available"]
    results = []
    for name, (start, end) in cfg["backtest"]["milestones"].items():
        window = days.loc[start:end]
        active = window[EXPECTED_STATE[name]]
        results.append({
            "milestone": name, "window": f"{start} -> {end}", "days_in_data": len(window),
            "expected_state_on": bool(active.any()), "first_on": active.idxmax().date() if active.any() else None,
            "days_with_verdict": int((window.stress_available >= min_available).sum()),
        })
    return pd.DataFrame(results)


def false_alarms(days, cfg):
    """Dates where the stress cluster switched on outside the panic and bottom windows."""
    m = cfg["backtest"]["milestones"]
    inside = pd.Series(False, index=days.index)
    for name in STRESS_WINDOWS:
        inside |= (days.index >= m[name][0]) & (days.index <= m[name][1])
    return days.index[days.events.str.contains("STRESS_ON") & ~inside]


def run(cfg, offline=False):
    data, status = sources.load_all(cfg, offline)
    if "price" not in data:
        raise sources.SourceError(f"no price data: {status['price']}")
    values, flags = indicators.compute(data, cfg)
    days = replay(flags, cfg)
    days.join(values).join(flags.add_prefix("f.")).to_csv(cfg["paths"]["backtest"])

    first_valid = flags.apply(lambda c: c.first_valid_index())
    print("IN-SAMPLE: windows come from the 27/10/2026 research note, n = 4 events; this is a pipeline check, not proof.\n")
    print(evaluate(days, cfg).to_string(index=False))
    print(f"\nStress ON outside the panic/bottom windows: {[d.date().isoformat() for d in false_alarms(days, cfg)]}")
    print("\nFirst day each flag has data (missing before that, never counted as False):")
    print(pd.to_datetime(first_valid).dt.date.to_string())
    print(f"\nPer-day verdicts saved to {cfg['paths']['backtest']}")
    return days
