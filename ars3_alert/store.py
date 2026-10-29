"""Files the alert bot persists: hysteresis state, the read-only regime bridge file, and the run log."""
import json
import logging
import os
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from .scoring import State

log = logging.getLogger(__name__)


def _write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)  # atomic: a reader never sees a half-written file


def load_state(path):
    try:
        return State(**json.loads(Path(path).read_text(encoding="utf-8")))
    except FileNotFoundError:
        return State()
    except (ValueError, TypeError) as e:
        log.warning("state file %s is unreadable (%s); starting from a clean state", path, e)
        return State()


def save_state(path, state):
    _write_json(path, asdict(state))


def write_regime(path, asof, verdict, halving_days, shadow):
    """Bridge file for other processes (e.g. ARS-3). Read-only for them: only the alert bot writes it."""
    s = verdict.state
    _write_json(path, {
        "asof": asof.date().isoformat(), "regime": s.regime, "stress_on": s.stress_on,
        "recovery_on": s.recovery_on, "top_zone": s.top_zone,
        "halving_days": None if pd.isna(halving_days) else int(halving_days), "shadow": shadow,
    })


def append_run(path, record):
    """One row per run; columns are the union over all runs, so adding an indicator never breaks the log."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    runs = pd.DataFrame([record])
    if path.exists():
        runs = pd.concat([pd.read_csv(path), runs])
    runs.to_csv(path, index=False)
