"""Scoring: turns one day of flags into a verdict. Pure, so the live runner and the backtest share it."""
from dataclasses import dataclass

import pandas as pd

RECOVERY_FLAGS = (
    "regime.above_fast_ma", "regime.above_sth_cost", "recovery.etf_positive", "recovery.funding_neutral",
)


@dataclass(frozen=True)
class State:
    """What the next day needs to know about today (hysteresis and transition detection)."""
    stress_on: bool = False
    recovery_on: bool = False
    regime: str = "unknown"
    top_zone: bool = False


@dataclass(frozen=True)
class Verdict:
    stress_hits: tuple
    stress_available: int
    stress_missing: tuple
    regime_hits: int
    regime_available: int
    recovery_hits: int
    recovery_available: int
    state: State
    events: tuple


def _tally(row, names):
    """(names of flags that fired, number of flags with data, names of flags with no data)."""
    known = [n for n in names if not pd.isna(row[n])]
    return tuple(n for n in known if row[n]), len(known), tuple(n for n in names if pd.isna(row[n]))


def _events(prev, new):
    for attr, label in (("stress_on", "STRESS"), ("recovery_on", "RECOVERY")):
        if getattr(prev, attr) != getattr(new, attr):
            yield f"{label}_{'ON' if getattr(new, attr) else 'OFF'}"
    if new.top_zone and not prev.top_zone:
        yield "TOP_ZONE_ENTER"
    if "unknown" not in (prev.regime, new.regime) and prev.regime != new.regime:
        yield f"REGIME_{new.regime.upper()}"


def score(row, cfg, prev=None):
    """Verdict for one day's flags. Flags without data are skipped and counted as missing, never as False."""
    prev = prev or State()
    s, r, rc = cfg["stress"], cfg["regime"], cfg["recovery"]
    names = lambda prefix: [c for c in row.index if c.startswith(prefix)]

    hits, available, missing = _tally(row, names("stress."))
    if available >= s["min_available"]:
        stress_on = len(hits) >= s["min_flags"] or (prev.stress_on and len(hits) >= s["clear_flags"])
    else:
        stress_on = prev.stress_on  # too little data for a verdict: hold the previous state

    regime_hits, regime_available, _ = _tally(row, names("regime."))
    share = len(regime_hits) / regime_available if regime_available else 0.0
    if regime_available < r["min_available"]:
        regime = "unknown"
    elif share >= r["bull_share"]:
        regime = "above_trend"
    elif share <= r["bear_share"]:
        regime = "below_trend"
    else:
        regime = "mixed"

    recovery_hits, recovery_available, _ = _tally(row, RECOVERY_FLAGS)
    recovery_on = len(recovery_hits) >= rc["min_confirmations"]

    top_zone = False if pd.isna(row["phase.top_zone"]) else bool(row["phase.top_zone"])
    state = State(stress_on, recovery_on, regime, top_zone)
    return Verdict(
        hits, available, missing, len(regime_hits), regime_available,
        len(recovery_hits), recovery_available, state, tuple(_events(prev, state)),
    )
