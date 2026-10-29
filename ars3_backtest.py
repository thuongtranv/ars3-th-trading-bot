"""
ars3_backtest.py
Backtest engine for the RS-75 strategy.

Execution model: a signal is computed from closed bars up to i-1 and filled at the open of bar i.
Exits are checked against each bar's high/low, stop-loss before targets (conservative).
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import ccxt
import logging
from datetime import datetime

from ars3_strategy_rs75 import generate_signals, strategy_cfg
from ars3_broker_ccxt import Broker
from common.env import load_yaml
from common.log import setup_logging

logger = logging.getLogger(__name__)
FILL_CAP = 0.95  # max share of the balance a position may use as notional

def validate_ohlcv_data(df: pd.DataFrame) -> tuple:
    """Validate OHLCV data for backtesting."""
    if df.empty:
        return False, "Empty dataframe"
    
    required_cols = ['open', 'high', 'low', 'close', 'volume']
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        return False, f"Missing columns: {missing_cols}"
    
    # Check for NaN values
    if df[required_cols].isna().any().any():
        return False, "Data contains NaN values"
    
    # Check for negative or zero prices
    price_cols = ['open', 'high', 'low', 'close']
    if (df[price_cols] <= 0).any().any():
        return False, "Data contains non-positive prices"
    
    # Check OHLC consistency
    invalid_rows = (
        (df['high'] < df['low']) |
        (df['high'] < df['open']) |
        (df['high'] < df['close']) |
        (df['low'] > df['open']) |
        (df['low'] > df['close'])
    )
    
    if invalid_rows.any():
        return False, f"OHLC data inconsistency in {invalid_rows.sum()} rows"
    
    return True, "Data validation passed"

def _open_position(history, entry_price, scfg, balance, trade_id):
    """Position for the next bar's open from signals on closed bars; None if there is no valid one."""
    signals = generate_signals(history, scfg, balance=balance)
    if not signals:
        return None
    s = signals[0]
    direction = 1 if s["type"] == "BUY" else -1
    tp1 = s["tp1"]
    # Levels come from the previous close; the fill at the next open can land on the wrong side of them.
    if direction * (tp1 - entry_price) <= 0 or direction * (entry_price - s["stop"]) <= 0:
        return None
    if s["size"] * entry_price > balance * FILL_CAP:
        return None
    return {
        "id": trade_id, "type": s["type"], "direction": direction,
        "entry_price": entry_price, "size": s["size"], "stop_loss": s["stop"],
        "tp1": tp1, "tp2": s.get("tp2") or entry_price + 2 * (tp1 - entry_price),
        "has_hit_tp1": False, "reason": s.get("reason", ""),
    }

def _exit_fill(pos, bar):
    """First exit on this bar as (kind, price, size). Stop-loss is checked before targets."""
    d = pos["direction"]
    adverse, favorable = (bar["low"], bar["high"]) if d > 0 else (bar["high"], bar["low"])
    if d * (adverse - pos["stop_loss"]) <= 0:
        return "STOP_LOSS", pos["stop_loss"], pos["size"]
    if not pos["has_hit_tp1"] and d * (favorable - pos["tp1"]) >= 0:
        return "TAKE_PROFIT_1", pos["tp1"], pos["size"] / 2
    if pos["has_hit_tp1"] and d * (favorable - pos["tp2"]) >= 0:
        return "TAKE_PROFIT_2", pos["tp2"], pos["size"]
    return None

def backtest(df: pd.DataFrame, cfg: dict, initial_balance: float = 10000.0) -> tuple:
    """Returns (df with a mark-to-market 'equity' column, list of trade records)."""
    ok, msg = validate_ohlcv_data(df)
    if not ok:
        raise ValueError(f"Data validation failed: {msg}")

    df = df.reset_index(drop=True)
    scfg = strategy_cfg(cfg)
    balance, position, trades, equity = initial_balance, None, [], []
    logger.info(f"Starting backtest with {len(df)} bars, balance: {initial_balance}")

    for i, bar in df.iterrows():
        ts = bar.get("timestamp", i)

        if position is None:
            position = _open_position(df.iloc[:i], bar["open"], scfg, balance, trade_id=i)
            if position:
                trades.append({
                    "timestamp": ts, "type": "ENTRY", "trade_id": i, "entry_type": position["type"],
                    "entry_price": position["entry_price"], "size": position["size"],
                    "stop_loss": position["stop_loss"], "tp1": position["tp1"], "tp2": position["tp2"],
                    "balance": balance, "reason": position["reason"],
                })

        fill = _exit_fill(position, bar) if position else None
        if fill:
            kind, price, size = fill
            pnl = position["direction"] * (price - position["entry_price"]) * size
            balance += pnl
            trades.append({
                "timestamp": ts, "type": kind, "trade_id": position["id"], "entry_type": position["type"],
                "entry_price": position["entry_price"], "exit_price": price, "size": size,
                "pnl": pnl, "balance": balance, "reason": kind,
            })
            if kind == "TAKE_PROFIT_1":
                position["size"] -= size
                position["has_hit_tp1"] = True
            else:
                position = None

        open_pnl = position["direction"] * (bar["close"] - position["entry_price"]) * position["size"] if position else 0.0
        equity.append(balance + open_pnl)

    df["equity"] = equity
    logger.info(f"Backtest completed. Final equity: {equity[-1]:.2f}")
    return df, trades

def calculate_metrics(df: pd.DataFrame, trades: list, initial_balance: float, bars_per_year: float = 365 * 24) -> dict:
    """Trade stats are per round trip (entry to flat), so a partial exit is not a separate trade."""
    exits = [t for t in trades if "pnl" in t]
    if df.empty or not exits:
        return {
            "initial_balance": initial_balance, "final_balance": initial_balance, "total_return_pct": 0,
            "max_drawdown_pct": 0, "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
            "win_rate_pct": 0, "avg_win": 0, "avg_loss": 0, "profit_factor": 0, "sharpe_ratio": 0, "total_pnl": 0,
        }

    per_trade = pd.DataFrame(exits).groupby("trade_id")["pnl"].sum()
    wins, losses = per_trade[per_trade > 0], per_trade[per_trade < 0]
    equity = df["equity"]
    peak = equity.cummax()
    returns = equity.pct_change().dropna()
    return {
        "initial_balance": initial_balance,
        "final_balance": equity.iloc[-1],
        "total_return_pct": (equity.iloc[-1] / initial_balance - 1) * 100,
        "max_drawdown_pct": ((peak - equity) / peak * 100).max(),
        "total_trades": len(per_trade),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate_pct": len(wins) / len(per_trade) * 100,
        "avg_win": wins.mean() if len(wins) else 0,
        "avg_loss": losses.mean() if len(losses) else 0,
        "profit_factor": wins.sum() / -losses.sum() if len(losses) else float("inf"),
        "sharpe_ratio": returns.mean() / returns.std() * np.sqrt(bars_per_year) if returns.std() else 0,
        "total_pnl": per_trade.sum(),
    }

def load_data_from_broker(symbol: str = "BTC/USDT", timeframe: str = "1h", limit: int = 1000):
    """Load data from broker for backtesting."""
    try:
        broker = Broker()
        if broker.simulation_mode:
            logger.warning("Broker is in simulation mode: backtesting on RANDOM data, results are meaningless")
        broker.symbol = symbol
        broker.timeframe = timeframe
        
        ohlcv_data = broker.fetch_ohlcv(limit=limit)
        if not ohlcv_data:
            raise ValueError("No data received from broker")
        
        df = pd.DataFrame(ohlcv_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.sort_values('timestamp').reset_index(drop=True)
        
        logger.info(f"Loaded {len(df)} bars from broker")
        return df
    
    except Exception as e:
        logger.error(f"Error loading data from broker: {e}")
        return None

def load_data_from_csv(file_path: str):
    """Load data from CSV file for backtesting."""
    try:
        df = pd.read_csv(file_path)
        
        # Standardize column names
        column_mapping = {
            'time': 'timestamp',
            'Time': 'timestamp',
            'Date': 'timestamp',
            'date': 'timestamp',
            'Open': 'open',
            'High': 'high', 
            'Low': 'low',
            'Close': 'close',
            'Volume': 'volume'
        }
        
        df = df.rename(columns=column_mapping)
        
        # Convert timestamp
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # Validate required columns
        required_cols = ['open', 'high', 'low', 'close']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            raise ValueError(f"CSV missing required columns: {missing_cols}")
        
        # Add volume if missing
        if 'volume' not in df.columns:
            df['volume'] = 1000  # Default volume
        
        df = df.sort_values('timestamp' if 'timestamp' in df.columns else df.index).reset_index(drop=True)
        
        logger.info(f"Loaded {len(df)} bars from CSV")
        return df
    
    except Exception as e:
        logger.error(f"Error loading data from CSV: {e}")
        return None

def plot_backtest_results(df: pd.DataFrame, trades: list, save_path: str = "backtest_results.png"):
    """Create comprehensive backtest visualization."""
    try:
        fig, axes = plt.subplots(3, 1, figsize=(15, 12))
        
        # Price chart with signals
        ax1 = axes[0]
        ax1.plot(df.index, df['close'], label='Close Price', alpha=0.8, linewidth=1)
        
        # Mark entry points
        entry_trades = [t for t in trades if t['type'] == 'ENTRY']
        for trade in entry_trades:
            try:
                if 'timestamp' in trade:
                    # Find corresponding index
                    time_mask = df['timestamp'] == trade['timestamp']
                    if time_mask.any():
                        idx = df[time_mask].index[0]
                    else:
                        continue
                else:
                    continue
                
                color = 'green' if trade['entry_type'] == 'BUY' else 'red'
                marker = '^' if trade['entry_type'] == 'BUY' else 'v'
                ax1.scatter(idx, trade['entry_price'], color=color, marker=marker, s=100, alpha=0.8, zorder=5)
            except Exception as e:
                continue
        
        # Mark exit points
        exit_trades = [t for t in trades if t['type'] in ['TAKE_PROFIT_1', 'TAKE_PROFIT_2', 'STOP_LOSS']]
        for trade in exit_trades:
            try:
                if 'timestamp' in trade:
                    time_mask = df['timestamp'] == trade['timestamp']
                    if time_mask.any():
                        idx = df[time_mask].index[0]
                    else:
                        continue
                else:
                    continue
                
                color = 'blue' if 'TAKE_PROFIT' in trade['type'] else 'orange'
                marker = 'o' if 'TAKE_PROFIT' in trade['type'] else 'x'
                ax1.scatter(idx, trade['exit_price'], color=color, marker=marker, s=80, alpha=0.8, zorder=5)
            except Exception as e:
                continue
        
        ax1.set_title('Price Chart with Entry/Exit Points')
        ax1.set_ylabel('Price')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # Equity curve
        ax2 = axes[1]
        ax2.plot(df.index, df['equity'], label='Equity Curve', color='purple', linewidth=2)
        ax2.axhline(y=df['equity'].iloc[0], color='red', linestyle='--', alpha=0.7, label='Initial Balance')
        ax2.set_title('Equity Curve')
        ax2.set_ylabel('Balance')
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        
        # Drawdown
        ax3 = axes[2]
        equity_series = df['equity']
        peak = equity_series.expanding().max()
        drawdown = (peak - equity_series) / peak * 100
        ax3.fill_between(df.index, 0, -drawdown, alpha=0.7, color='red', label='Drawdown %')
        ax3.set_title('Drawdown')
        ax3.set_xlabel('Time')
        ax3.set_ylabel('Drawdown (%)')
        ax3.legend()
        ax3.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        plt.show()
        
        logger.info(f"Backtest chart saved to {save_path}")
        return fig
        
    except Exception as e:
        logger.error(f"Error creating backtest plot: {e}")
        return None

def run_backtest(data_source: str = "broker", config_path: str = "ars3_config.yaml", 
                symbol: str = "BTC/USDT", initial_balance: float = 10000, 
                csv_path: str = None, plot_results: bool = True):
    """
    Main function to run comprehensive backtest.
    
    Args:
        data_source: "broker" or "csv"
        config_path: Path to configuration file
        symbol: Trading symbol (for broker data)
        initial_balance: Starting balance
        csv_path: Path to CSV file (for CSV data)
        plot_results: Whether to create plots
    
    Returns:
        dict: Backtest results including dataframe, trades, and metrics
    """
    print("=" * 60)
    print("ARS-3 ENHANCED BACKTEST ENGINE")
    print("=" * 60)
    
    # Load configuration
    try:
        config = load_yaml(config_path)
        logger.info(f"Configuration loaded from {config_path}")
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        return None
    
    # Load data
    print(f"Loading data from {data_source}...")
    if data_source == "broker":
        df = load_data_from_broker(symbol=symbol, limit=1000)
    elif data_source == "csv":
        if not csv_path:
            logger.error("csv_path must be provided for CSV data source")
            return None
        df = load_data_from_csv(csv_path)
    else:
        logger.error("data_source must be 'broker' or 'csv'")
        return None
    
    if df is None or df.empty:
        logger.error("Failed to load data")
        return None
    
    print(f"Loaded {len(df)} bars of data")
    if 'timestamp' in df.columns:
        print(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    
    # Run backtest
    print("Running enhanced backtest...")
    start_time = datetime.now()
    
    try:
        df_result, trades = backtest(df, config, initial_balance)
        if not trades:
            logger.warning("No trades generated during backtest")
    except Exception as e:
        logger.error(f"Backtest execution failed: {e}")
        return None
    
    end_time = datetime.now()
    print(f"Backtest completed in {(end_time - start_time).total_seconds():.2f} seconds")
    
    # Calculate metrics
    bars_per_year = 365 * 86400 / ccxt.Exchange.parse_timeframe(config.get("timeframe", "1h"))
    metrics = calculate_metrics(df_result, trades, initial_balance, bars_per_year)
    
    # Print results
    print("\n" + "=" * 50)
    print("BACKTEST RESULTS")
    print("=" * 50)
    for key, value in metrics.items():
        if isinstance(value, float):
            print(f"{key}: {value:.4f}")
        else:
            print(f"{key}: {value}")
    
    # Create plots
    if plot_results:
        try:
            plot_backtest_results(df_result, trades)
        except Exception as e:
            logger.error(f"Error creating plots: {e}")
    
    # Save detailed results
    try:
        df_result.to_csv("backtest_equity.csv", index=False)
        if trades:
            trades_df = pd.DataFrame(trades)
            trades_df.to_csv("backtest_trades.csv", index=False)
        print("Detailed results saved to CSV files")
    except Exception as e:
        logger.error(f"Error saving results: {e}")
    
    return {
        'dataframe': df_result,
        'trades': trades,
        'metrics': metrics,
        'config': config
    }

if __name__ == "__main__":
    setup_logging()
    # Example usage with error handling
    print("Starting ARS-3 Enhanced Backtest...")
    
    try:
        results = run_backtest(
            data_source="broker",  # or "csv"
            symbol="BTC/USDT",
            initial_balance=10000,
            plot_results=True
            # csv_path="your_data.csv"  # uncomment if using CSV
        )
        
        if results:
            print("\nBacktest completed successfully!")
            print(f"Final Balance: {results['metrics'].get('final_balance', 'N/A')}")
            print(f"Total Return: {results['metrics'].get('total_return_pct', 'N/A'):.2f}%")
            print(f"Max Drawdown: {results['metrics'].get('max_drawdown_pct', 'N/A'):.2f}%")
        else:
            print("\nBacktest failed!")
            
    except KeyboardInterrupt:
        print("\nBacktest interrupted by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)