# ARS-3 Trading Bot Project

## Overview
ARS-3 is an automated trading bot designed for cryptocurrency markets, focusing on range-bound scalping strategies. It supports both simulation (paper trading) and live trading on exchanges like Binance (spot or futures, with testnet option). The bot uses the RS-75 (Range Scalper 75) strategy, which is implemented in `ars3_strategy_rs75.py`. Key features include:

- **Multi-coin support**: Configurable list of coins (e.g., BTC/USDT, ETH/USDT).
- **Risk management**: Fixed risk per trade, max drawdown protection.
- **Notifications**: Telegram alerts for signals, errors, and restarts.
- **Logging**: Real-time equity curve and trade history saved to CSV.
- **Dashboard**: Streamlit-based UI for monitoring equity and trades.
- **Auto-restart**: Script to handle crashes with alerts.
- **Reporting**: Daily reports via Telegram (optional email).
- **Backtesting**: Basic backtest engine for strategy validation.

The project is modular, with separate files for strategy, broker integration (using CCXT), live trading, backtesting, and utilities.

## Project Structure
```
ARS-3/
├── ars3_live_advanced_final.py     # Main bot: live/simulation trading, order management, SL/TP
├── ars3_strategy_rs75.py           # Core strategy: RS-75 with advanced features (confirmation, smart SL/TP, filters)
├── ars3_broker_ccxt.py             # Broker abstraction: CCXT integration for data fetch and order execution
├── ars3_backtest.py                # Backtest engine: Tests RS-75 strategy on historical data
├── ars3_autostart.py               # Auto-start script: Runs bot + dashboard with auto-restart and Telegram alerts
├── ars3_report_daily.py            # Daily report generator: Sends PNL, drawdown, charts via Telegram/email
├── streamlit_ars3_advanced.py      # Streamlit dashboard: Real-time equity curve and trade history
├── ars3_config.yaml                # Configuration: Coins, timeframe, risk, strategy params, modes
├── .env                            # Environment variables: API keys, Telegram token, email creds
├── trades_live.csv                 # Auto-generated: Log of executed trades
├── equity_live.csv                 # Auto-generated: Equity history log
├── position.json                   # Auto-generated: Persists open position state
├── README.md                       # This file: Project overview and quick start
└── STRATEGY.md                     # Separate file: Detailed explanation of RS-75 strategy
```

## Quick Start Guide
### 1. Prerequisites
- Python 3.8+ installed.
- A Binance account (for live trading). Get API keys from Binance dashboard.
- Telegram bot token and chat ID (for notifications). Create a bot via BotFather.
- Optional: Gmail for email reports (use app password for security).

### 2. Installation
Clone the repository (or download files) and navigate to the project directory.

Install required libraries:
```
pip install ccxt pandas numpy matplotlib pyyaml python-dotenv streamlit schedule requests
pip install plotly
```
- **ccxt**: For exchange integration.
- **pandas/numpy/matplotlib**: Data handling and charting.
- **pyyaml/python-dotenv**: Config and env loading.
- **streamlit**: Dashboard.
- **schedule/requests**: Scheduling and HTTP (Telegram).

No additional installs needed beyond this—CCXT handles exchange APIs.

### 3. Configuration
1. Copy `.env.example` to `.env` and fill in your details:
   ```
   BINANCE_API_KEY=your_api_key
   BINANCE_SECRET_KEY=your_secret
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=your_chat_id
   EMAIL_USER=your_email@gmail.com     # Optional
   EMAIL_PASS=your_app_password        # Optional
   EMAIL_TO=recipient@gmail.com        # Optional
   ```

2. Edit `ars3_config.yaml` for your preferences:
   - `coins`: List of symbols (e.g., ["BTC/USDT", "ETH/USDT"]).
   - `timeframe`: e.g., "1h".
   - `initial_capital`: Starting balance for simulation.
   - `risk_per_trade`: e.g., 0.01 (1% risk).
   - `max_dd_percent`: Max drawdown to pause trading.
   - `simulation_mode`: True for paper trading, False for live.
   - `exchange`: "binance" (default).
   - `futures`: True for futures market.
   - `testnet`: True for Binance testnet (recommended for testing live mode).
   - `rs75_strategy`: Tune indicators (BB, RSI, ADX) and features (e.g., smart SL/TP, volume filter).

### 4. Running the Bot
#### Option 1: Manual Start
- Run the main bot:
  ```
  python ars3_live_advanced_final.py
  ```
  - This starts trading loops, fetches data, generates signals, executes orders (simulation or live).
  - Logs trades to `trades_live.csv` and equity to `equity_live.csv`.

- In a separate terminal, run the dashboard:
  ```
  streamlit run streamlit_ars3_advanced.py
  ```
  - Open http://localhost:8501 in your browser to view equity curve and recent trades.

- For daily reports:
  ```
  python ars3_report_daily.py
  ```
  - Run this manually or schedule it (e.g., via cron).

#### Option 2: Auto-Start with Restart & Alerts
- Run:
  ```
  python ars3_autostart.py
  ```
  - This launches the bot and dashboard in threads/windows.
  - Auto-restarts on crashes.
  - Sends Telegram alerts on start/crash/restart.
  - Works on Windows/Linux/Mac.

### 5. Testing
- **Simulation Mode**: Set `simulation_mode: True` in config. Uses dummy data for testing without real funds.
- **Backtesting**: Use `ars3_backtest.py` to test RS-75 on historical data.
  - Example usage (in code):
    ```python
    import pandas as pd
    from ars3_backtest import backtest
    # Load your OHLCV DataFrame as df
    config = {"rs75": {...}}  # From ars3_config.yaml
    df, trades = backtest(df, config, initial_balance=10000)
    ```
  **Chạy backtest** với broker data
    python ars3_backtest.py
- **Testnet**: Set `testnet: True` for live-like testing on Binance testnet (fake funds).

### 6. Monitoring & Logs
- **Dashboard**: Real-time charts and tables.
- **Telegram**: Alerts for signals, errors, stops.
- **CSVs**: `trades_live.csv` (trade details), `equity_live.csv` (balance over time).
- **Reports**: Daily summary with PNL, drawdown, chart.

### 7. Customization
- Extend `ars3_strategy_rs75.py` for new features (see STRATEGY.md for details).
- Add more exchanges in `ars3_broker_ccxt.py`.
- Tune params in config for better performance.

## Important Notes
- **Risk Warning**: Trading involves risk. Start with simulation/testnet. Never use real funds without thorough testing.
- **API Limits**: Respect exchange rate limits to avoid bans.
- **Errors**: Check console/Telegram for issues. Common: Invalid API keys, network errors.
- **Contributions**: Feel free to fork and improve!

For detailed strategy explanation, see [STRATEGY.md](STRATEGY.md).