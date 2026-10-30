# ars3_alert: BTC early-warning alert bot

Alerts only. It never imports the broker and never places orders. It runs as its own process next to ARS-3 and
shares only the `common/` helpers (env, retry, logging, Telegram).

## Layers (all thresholds live in `config.yaml`)

| Layer | Question | Output |
|---|---|---|
| 1 Phase | Days since the 20/4/2024 halving | context flag `phase.top_zone` (never a trigger) |
| 2 Regime | Is price above the 50w/200w MA, STH cost basis, True Market Mean? | `above_trend` / `mixed` / `below_trend` / `unknown` |
| 3 Stress | Do >= `min_flags` of the available stress flags fire? | `STRESS_ON` / `STRESS_OFF` (hysteresis via `clear_flags`) |
| 4 Recovery | >= `min_confirmations` of: above 50w MA, above STH cost basis, ETF positive weeks, funding neutral | `RECOVERY_ON` / `RECOVERY_OFF` |

A flag whose data is unavailable is `<NA>`: it is skipped and listed as missing, never counted as `False`.
Too few available stress flags (`min_available`) means no verdict and the previous state is held.

| Module | Job |
|---|---|
| `sources.py` | fetch every source, cache to CSV, fall back to the cache when a fetch fails |
| `indicators.py` | causal daily values and flags (row t only uses data available on day t) |
| `scoring.py` | pure `score(flags_row, cfg, prev_state)`, shared by live and backtest |
| `alerts.py`, `store.py` | Telegram message; state, regime bridge file and run log |
| `runner.py`, `backtest.py`, `__main__.py` | one run / loop, replay over history, CLI |

## Use

```
cp .env.example .env                    # fill TELEGRAM_*; BGEOMETRICS_API_KEY when you have it
python -m ars3_alert verify             # fetch every source fresh and print what really came back
python -m ars3_alert run                # one run
python -m ars3_alert run --loop         # every run.every_hours
python -m ars3_alert backtest           # replay history through the same score()
python -m ars3_alert run --offline      # cache only, no network
```

Run it from the repository root: the paths in `config.yaml` are relative to the working directory.
Use Python 3.13 inside the project's `.venv` (`.\.venv\Scripts\python.exe -m ars3_alert ...` on Windows).

## Before trusting an alert

1. `verify` first. Fields marked `UNVERIFIED` in `config.yaml` (ETF JSON keys, the on-chain endpoints) are guesses; fix
   them from what `verify` prints. `sources.mnav` needs a manual `data/mnav.csv` (`date,mnav`).
2. `run.shadow: true` prefixes every message with `[SHADOW]`. The state changes only a few times per cycle, so a few weeks
   of shadow runs validates the pipeline (fresh data, how many flags are missing, no flapping), not signal quality.
3. Every threshold is a hypothesis from a note written after the events (n = 3-5 cycles). `backtest` is in-sample by
   construction. Do not tune thresholds to make it look better.

`data/regime_state.json` is a read-only bridge for other processes. ARS-3 does not read it yet.
