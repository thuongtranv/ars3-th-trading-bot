"""
ars3_backtest.py (FIXED VERSION)
Improved backtest engine for ARS-3 with proper position tracking and intrabar execution
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import yaml
import logging
from datetime import datetime
from ars3_strategy_rs75 import generate_signals
from ars3_broker_ccxt import Broker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BacktestEngine:
    def __init__(self, initial_balance: float = 10000):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.equity_curve = []
        self.trades = []
        self.position = None
        
    def reset(self):
        """Reset backtest state."""
        self.balance = self.initial_balance
        self.equity_curve = []
        self.trades = []
        self.position = None

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

def backtest(df: pd.DataFrame, cfg: dict, initial_balance: float = 10000.0) -> tuple:
    """
    Enhanced backtest with intrabar execution and proper position tracking.
    
    Args:
        df: OHLCV DataFrame with columns [timestamp, open, high, low, close, volume]
        cfg: Configuration dict containing rs75_strategy parameters
        initial_balance: Starting balance for backtest
    
    Returns:
        tuple: (df_with_equity, trades_list)
    """
    try:
        # Validate input data
        is_valid, error_msg = validate_ohlcv_data(df)
        if not is_valid:
            raise ValueError(f"Data validation failed: {error_msg}")
        
        df = df.copy().reset_index(drop=True)
        
        # Initialize backtest state
        engine = BacktestEngine(initial_balance)
        equity_curve = []
        trades = []
        
        # Position tracking
        position = None
        
        logger.info(f"Starting backtest with {len(df)} bars, balance: {initial_balance}")
        
        for i in range(len(df)):
            current_bar = df.iloc[i]
            current_open = current_bar['open']
            current_high = current_bar['high']
            current_low = current_bar['low']
            current_close = current_bar['close']
            current_time = current_bar.get('timestamp', i)
            
            # FIXED: Handle position exits first using intrabar prices
            if position is not None:
                entry_price = position['entry_price']
                stop_loss = position['stop_loss']
                tp1 = position['tp1']
                tp2 = position['tp2']
                position_type = position['type']
                size = position['size']
                has_hit_tp1 = position.get('has_hit_tp1', False)
                
                # Track if position was closed this bar
                position_closed = False
                
                # Check stop loss first (highest priority)
                if position_type == 'BUY':
                    if current_low <= stop_loss:
                        # Long position stopped out
                        exit_price = stop_loss
                        pnl = (exit_price - entry_price) * size
                        engine.balance += pnl
                        
                        trades.append({
                            'timestamp': current_time,
                            'type': 'STOP_LOSS',
                            'entry_type': position_type,
                            'entry_price': entry_price,
                            'exit_price': exit_price,
                            'size': size,
                            'pnl': pnl,
                            'balance': engine.balance,
                            'reason': 'Stop Loss Hit'
                        })
                        
                        position = None
                        position_closed = True
                        logger.debug(f"Bar {i}: STOP LOSS - BUY position closed at {exit_price}, PnL: {pnl:.2f}")
                
                else:  # SELL position
                    if current_high >= stop_loss:
                        # Short position stopped out
                        exit_price = stop_loss
                        pnl = (entry_price - exit_price) * size
                        engine.balance += pnl
                        
                        trades.append({
                            'timestamp': current_time,
                            'type': 'STOP_LOSS',
                            'entry_type': position_type,
                            'entry_price': entry_price,
                            'exit_price': exit_price,
                            'size': size,
                            'pnl': pnl,
                            'balance': engine.balance,
                            'reason': 'Stop Loss Hit'
                        })
                        
                        position = None
                        position_closed = True
                        logger.debug(f"Bar {i}: STOP LOSS - SELL position closed at {exit_price}, PnL: {pnl:.2f}")
                
                # Check take profits if position still open
                if not position_closed and position is not None:
                    if position_type == 'BUY':
                        # Check TP1
                        if not has_hit_tp1 and current_high >= tp1:
                            exit_price = tp1
                            tp1_size = size * 0.5  # Close 50%
                            pnl = (exit_price - entry_price) * tp1_size
                            engine.balance += pnl
                            
                            trades.append({
                                'timestamp': current_time,
                                'type': 'TAKE_PROFIT_1',
                                'entry_type': position_type,
                                'entry_price': entry_price,
                                'exit_price': exit_price,
                                'size': tp1_size,
                                'pnl': pnl,
                                'balance': engine.balance,
                                'reason': 'Take Profit 1'
                            })
                            
                            # Update position - keep remaining 50%
                            position['size'] = size * 0.5
                            position['has_hit_tp1'] = True
                            logger.debug(f"Bar {i}: TP1 - 50% of BUY position closed at {exit_price}, PnL: {pnl:.2f}")
                        
                        # Check TP2 (only if TP1 was hit)
                        elif has_hit_tp1 and current_high >= tp2:
                            exit_price = tp2
                            remaining_size = position['size']
                            pnl = (exit_price - entry_price) * remaining_size
                            engine.balance += pnl
                            
                            trades.append({
                                'timestamp': current_time,
                                'type': 'TAKE_PROFIT_2',
                                'entry_type': position_type,
                                'entry_price': entry_price,
                                'exit_price': exit_price,
                                'size': remaining_size,
                                'pnl': pnl,
                                'balance': engine.balance,
                                'reason': 'Take Profit 2'
                            })
                            
                            position = None
                            position_closed = True
                            logger.debug(f"Bar {i}: TP2 - Remaining BUY position closed at {exit_price}, PnL: {pnl:.2f}")
                    
                    else:  # SELL position
                        # Check TP1
                        if not has_hit_tp1 and current_low <= tp1:
                            exit_price = tp1
                            tp1_size = size * 0.5
                            pnl = (entry_price - exit_price) * tp1_size
                            engine.balance += pnl
                            
                            trades.append({
                                'timestamp': current_time,
                                'type': 'TAKE_PROFIT_1',
                                'entry_type': position_type,
                                'entry_price': entry_price,
                                'exit_price': exit_price,
                                'size': tp1_size,
                                'pnl': pnl,
                                'balance': engine.balance,
                                'reason': 'Take Profit 1'
                            })
                            
                            position['size'] = size * 0.5
                            position['has_hit_tp1'] = True
                            logger.debug(f"Bar {i}: TP1 - 50% of SELL position closed at {exit_price}, PnL: {pnl:.2f}")
                        
                        # Check TP2
                        elif has_hit_tp1 and current_low <= tp2:
                            exit_price = tp2
                            remaining_size = position['size']
                            pnl = (entry_price - exit_price) * remaining_size
                            engine.balance += pnl
                            
                            trades.append({
                                'timestamp': current_time,
                                'type': 'TAKE_PROFIT_2',
                                'entry_type': position_type,
                                'entry_price': entry_price,
                                'exit_price': exit_price,
                                'size': remaining_size,
                                'pnl': pnl,
                                'balance': engine.balance,
                                'reason': 'Take Profit 2'
                            })
                            
                            position = None
                            position_closed = True
                            logger.debug(f"Bar {i}: TP2 - Remaining SELL position closed at {exit_price}, PnL: {pnl:.2f}")
            
            # Check for new entry signals (only if no position)
            if position is None:
                try:
                    # Build window for signal generation (up to current bar)
                    window = df.iloc[:i+1].copy()
                    
                    # Generate signals
                    signals = generate_signals(window, cfg.get("rs75_strategy", {}), balance=engine.balance)
                    
                    if signals:
                        signal = signals[0]  # Take first valid signal
                        
                        # Validate signal has required fields
                        required_fields = ['type', 'price', 'stop', 'tp1', 'size']
                        if not all(field in signal and signal[field] is not None for field in required_fields):
                            logger.warning(f"Bar {i}: Invalid signal - missing fields: {signal}")
                            continue
                        
                        # FIXED: Use realistic entry price (current bar's open)
                        entry_price = current_open
                        stop_price = signal['stop']
                        tp1_price = signal['tp1']
                        
                        # Calculate TP2 if not provided
                        tp2_price = signal.get('tp2')
                        if tp2_price is None:
                            if signal['type'] == 'BUY':
                                tp2_price = entry_price + (tp1_price - entry_price) * 2
                            else:
                                tp2_price = entry_price - (entry_price - tp1_price) * 2
                        
                        # Validate position size
                        size = signal['size']
                        if size <= 0:
                            logger.warning(f"Bar {i}: Invalid position size: {size}")
                            continue
                        
                        # Check if we have enough balance
                        required_margin = size * entry_price
                        if required_margin > engine.balance * 0.95:  # Don't use more than 95% of balance
                            logger.warning(f"Bar {i}: Insufficient balance for position")
                            continue
                        
                        # Create position
                        position = {
                            'type': signal['type'],
                            'entry_price': entry_price,
                            'size': size,
                            'stop_loss': stop_price,
                            'tp1': tp1_price,
                            'tp2': tp2_price,
                            'has_hit_tp1': False,
                            'entry_bar': i
                        }
                        
                        # Log entry trade
                        trades.append({
                            'timestamp': current_time,
                            'type': 'ENTRY',
                            'entry_type': signal['type'],
                            'entry_price': entry_price,
                            'size': size,
                            'stop_loss': stop_price,
                            'tp1': tp1_price,
                            'tp2': tp2_price,
                            'balance': engine.balance,
                            'reason': signal.get('reason', 'Signal generated')
                        })
                        
                        logger.debug(f"Bar {i}: NEW {signal['type']} position opened at {entry_price}")
                        
                except Exception as e:
                    logger.error(f"Bar {i}: Error generating signals: {e}")
                    continue
            
            # Record equity
            equity_curve.append(engine.balance)
        
        # Add equity curve to dataframe
        df['equity'] = equity_curve
        
        logger.info(f"Backtest completed. Final balance: {engine.balance:.2f}")
        logger.info(f"Total trades: {len([t for t in trades if t['type'] == 'ENTRY'])}")
        
        return df, trades
        
    except Exception as e:
        logger.error(f"Backtest failed: {e}", exc_info=True)
        return df, []

def calculate_metrics(df: pd.DataFrame, trades: list, initial_balance: float) -> dict:
    """Calculate comprehensive backtest metrics."""
    try:
        if df.empty or not trades:
            return {
                'initial_balance': initial_balance,
                'final_balance': initial_balance,
                'total_return_pct': 0,
                'max_drawdown_pct': 0,
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate_pct': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'profit_factor': 0,
                'sharpe_ratio': 0
            }
        
        final_balance = df['equity'].iloc[-1]
        total_return = (final_balance - initial_balance) / initial_balance * 100
        
        # Drawdown calculation
        equity_series = df['equity']
        peak = equity_series.expanding().max()
        drawdown = (peak - equity_series) / peak * 100
        max_drawdown = drawdown.max()
        
        # Trade statistics
        entry_trades = [t for t in trades if t['type'] == 'ENTRY']
        pnl_trades = [t for t in trades if 'pnl' in t and t['pnl'] is not None]
        
        winning_trades = [t for t in pnl_trades if t['pnl'] > 0]
        losing_trades = [t for t in pnl_trades if t['pnl'] < 0]
        
        total_trades = len(entry_trades)
        win_count = len(winning_trades)
        loss_count = len(losing_trades)
        win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
        
        avg_win = np.mean([t['pnl'] for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([t['pnl'] for t in losing_trades]) if losing_trades else 0
        
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')
        
        # Sharpe ratio (simplified - assumes daily returns)
        if len(equity_series) > 1:
            returns = equity_series.pct_change().dropna()
            sharpe_ratio = returns.mean() / returns.std() * np.sqrt(252) if returns.std() != 0 else 0
        else:
            sharpe_ratio = 0
        
        metrics = {
            'initial_balance': initial_balance,
            'final_balance': final_balance,
            'total_return_pct': total_return,
            'max_drawdown_pct': max_drawdown,
            'total_trades': total_trades,
            'winning_trades': win_count,
            'losing_trades': loss_count,
            'win_rate_pct': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'sharpe_ratio': sharpe_ratio,
            'total_pnl': sum(t.get('pnl', 0) for t in pnl_trades)
        }
        
        return metrics
        
    except Exception as e:
        logger.error(f"Error calculating metrics: {e}")
        return {}

def load_data_from_broker(symbol: str = "BTC/USDT", timeframe: str = "1h", limit: int = 1000):
    """Load data from broker for backtesting."""
    try:
        broker = Broker()
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
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
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
    metrics = calculate_metrics(df_result, trades, initial_balance)
    
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