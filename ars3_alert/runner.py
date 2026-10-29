"""One alert run: load data, compute indicators, score the latest day, persist, notify."""
import logging
import time
from datetime import datetime, timezone

import pandas as pd
import schedule

from . import alerts, indicators, sources, store
from .scoring import score

log = logging.getLogger(__name__)


def _log_readings(values, flags, verdict):
    """Every indicator value and flag, so a run can be audited later."""
    for name, value in values.items():
        log.info("value %-22s %s", name, value)
    for name, flag in flags.items():
        log.info("flag  %-26s %s", name, "n/a" if pd.isna(flag) else bool(flag))
    log.info(
        "verdict stress=%d/%d regime=%s(%d/%d) recovery=%d/%d events=%s",
        len(verdict.stress_hits), verdict.stress_available, verdict.state.regime,
        verdict.regime_hits, verdict.regime_available, verdict.recovery_hits, verdict.recovery_available,
        list(verdict.events) or "none",
    )


def _run_record(shadow, asof, values, flags, verdict, status):
    return {
        "run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "asof": asof.date(), "shadow": shadow,
        **{f"v.{k}": v for k, v in values.items()},
        **{f"f.{k}": "" if pd.isna(v) else int(v) for k, v in flags.items()},
        "stress_hits": len(verdict.stress_hits), "stress_available": verdict.stress_available,
        "regime": verdict.state.regime, "recovery_hits": verdict.recovery_hits,
        "events": "|".join(verdict.events),
        "sources_missing": "|".join(n for n, s in status.items() if s.startswith("missing")),
    }


def run_once(cfg, offline=False, send=alerts.send):
    """Returns the verdict, or None when there is no price data to judge from."""
    paths, shadow = cfg["paths"], cfg["run"]["shadow"]
    data, status = sources.load_all(cfg, offline)
    if "price" not in data:
        log.error("no price data: %s", status["price"])
        send(f"Không có dữ liệu giá, bỏ qua lần chạy này: {status['price']}", shadow)
        return None

    values, flags = indicators.compute(data, cfg)
    asof, v, f = flags.index[-1], values.iloc[-1], flags.iloc[-1]
    verdict = score(f, cfg, store.load_state(paths["state"]))
    _log_readings(v, f, verdict)
    store.append_run(paths["runs"], _run_record(shadow, asof, v, f, verdict, status))
    store.write_regime(paths["regime"], asof, verdict, v["halving_days"], shadow)

    # Keep the old state when delivery fails, so the same change is announced again on the next run.
    if verdict.events and not send(alerts.format_message(verdict, asof, v["halving_days"], status), shadow):
        log.warning("alert not delivered; state not advanced, will retry next run")
        return verdict
    store.save_state(paths["state"], verdict.state)
    return verdict


def _guarded(cfg, offline):
    try:
        run_once(cfg, offline)
    except Exception:
        log.exception("run failed; the loop keeps going")


def loop(cfg, offline=False):
    _guarded(cfg, offline)
    schedule.every(cfg["run"]["every_hours"]).hours.do(_guarded, cfg, offline)
    while True:
        schedule.run_pending()
        time.sleep(30)
