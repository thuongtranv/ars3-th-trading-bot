
# RS-75 Strategy Explanation (ars3_strategy_rs75.py)

## Overview
RS-75 (Range Scalper 75) is a mean-reversion scalping strategy optimized for ranging markets. It uses Bollinger Bands (BB) for entry triggers, RSI for overbought/oversold confirmation, and ADX/NATR for regime filtering (to avoid trending markets). The strategy includes advanced "expert" features that can be enabled/disabled via config.

Key principles:
- Enter trades when price touches/extends beyond BB in a low-volatility, non-trending market.
- Scale out profits at multiple targets (TP1, TP2).
- Strict risk management with position sizing based on stop distance.

## Core Components
### 1. Indicators
- **Bollinger Bands (BB)**: Period=20, Std=2.5. Upper/lower bands define overextension.
- **RSI**: Period=7. Oversold <30 (buy), overbought >70 (sell).
- **ADX**: Period=14. <20 indicates no trend.
- **ATR/NATR**: Measures volatility. NATR (ATR/Close) <0.0040 for low vol.
- **Volume MA**: Optional filter, period=20. Requires volume > MA for entry.
- **HTF MA**: Optional higher-timeframe filter, period=50. Aligns with trend (e.g., long only above MA).

### 2. Regime Filter
- Trade only if market is "ranging": Low ADX + Low NATR.
- Skips trending or volatile periods.

### 3. Entry Rules
- **Base Entry**:
  - Buy: Price touches/breaks lower BB + RSI <30.
  - Sell: Price touches/breaks upper BB + RSI >70.
- **Confirmation Entry** (optional): Wait for price to close back inside BB after breakout (reduces false signals).
- **Filters**:
  - Volume: Entry only if current volume > MA.
  - HTF: Long only if price > HTF MA; short only if below.

### 4. Risk & Exits
- **Position Size**: Risk fixed % (e.g., 1%) of balance per trade, based on entry-stop distance.
- **Stop Loss (SL)**:
  - Smart SL (optional): Based on recent swing high/low (lookback=10 bars).
  - Fallback: ATR-based (multiplier=1.7).
- **Take Profit (TP)**:
  - TP1: Smart (BB midline) or ATR-based (multiplier=1.2). Close 50% position.
  - TP2: Extended beyond TP1 (e.g., 2x distance; calculated in backtest/live).
- No trailing SL (yet—can be added).

## Signal Generation
- `generate_signals(df, cfg, balance)`: Analyzes last bar only.
- Returns list of dicts: {"type": "BUY/SELL", "price", "size", "stop", "tp1", "reason"}.
- Used in live trading (`ars3_live_advanced_final.py`) and backtesting (`ars3_backtest.py`).

## Tuning & Extensions
- All params in `rs75_strategy` section of `ars3_config.yaml`.
- Enable/disable features: `use_confirmation_entry`, `use_smart_sl`, `use_smart_tp`, `use_volume_filter`, `use_htf_filter`.
- For HTF filter: Provide `df_htf` (higher timeframe data) when calling `generate_signals`.
- Improvements: Add fees, slippage, multi-timeframe, or machine learning filters.

This strategy performs best in sideways markets. Backtest thoroughly on your symbols/timeframes before live use!