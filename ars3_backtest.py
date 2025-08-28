"""
ars3_backtest.py (v3.0 - COMPLETELY REWRITTEN)
Professional-grade backtest engine with proper intrabar execution, 
realistic slippage, commission handling, and comprehensive analytics.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import yaml
import logging
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import json
import os

# Import strategy modules
try:
    from ars3_strategy_rs75 import generate_signals as generate_range_signals
    from ars3_strategy_trend import generate_signals as generate_trend_signals
    from ars3_broker_ccxt import Broker
except ImportError as e:
    logging.error(f"Failed to import required modules: {e}")
    exit(1)

# Suppress warnings for cleaner output
warnings.filterwarnings('ignore')

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('backtest.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class BacktestEngine:
    """Professional backtesting engine with realistic execution simulation."""
    
    def __init__(self, config: Dict, initial_balance: float = 10000.0):
        self.config = config
        self.initial_balance = initial_balance
        
        # Backtest state
        self.reset()
        
        # Backtest settings
        self.commission_rate = config.get('backtest', {}).get('commission_rate', 0.0004)
        self.slippage_pct = config.get('backtest', {}).get('slippage_pct', 0.01)
        self.realistic_execution = config.get('backtest', {}).get('realistic_execution', True)
        self.position_timeout_bars = config.get('backtest', {}).get('position_timeout_bars', 200)
        
        logger.info(f"Backtest engine initialized - Balance: ${initial_balance:,.2f}, Commission: {self.commission_rate*100:.4f}%, Slippage: {self.slippage_pct*100:.2f}%")
    
    def reset(self):
        """Reset backtest state for new run."""
        self.balance = self.initial_balance
        self.equity_curve = []
        self.trades = []
        self.positions = []
        self.current_position = None
        self.daily_stats = {}
        self.trade_count = 0
        self.peak_balance = self.initial_balance
        self.max_drawdown = 0.0
        
        logger.debug("Backtest state reset")
    
    def apply_slippage(self, price: float, side: str, volatility: float = 0.001) -> float:
        """Apply realistic slippage based on market conditions."""
        if not self.realistic_execution:
            return price
        
        # Base slippage increases with volatility
        base_slippage = self.slippage_pct * (1 + volatility * 10)
        slippage_amount = price * base_slippage * np.random.uniform(0.1, 1.0)
        
        # Apply slippage against the trader
        if side.upper() == 'BUY':
            return price + slippage_amount
        else:
            return price - slippage_amount
    
    def calculate_commission(self, trade_value: float) -> float:
        """Calculate trading commission."""
        return trade_value * self.commission_rate
    
    def calculate_position_size(self, signal: Dict, current_price: float, balance: float) -> float:
        """Calculate position size with enhanced risk management."""
        try:
            entry_price = current_price
            stop_loss = signal.get('stop', entry_price * 0.98)  # Default 2% stop
            
            risk_per_unit = abs(entry_price - stop_loss)
            if risk_per_unit <= 0:
                logger.warning("Invalid risk per unit calculation")
                return 0.0
            
            # Risk management parameters
            risk_pct = signal.get('risk_pct', self.config.get('rs75_strategy', {}).get('risk_pct', 0.01))
            max_position_pct = self.config.get('risk_management', {}).get('max_position_size_pct', 0.1)
            
            # Calculate size based on risk
            risk_amount = balance * risk_pct
            size_by_risk = risk_amount / risk_per_unit
            
            # Limit by maximum position size
            max_position_value = balance * max_position_pct
            max_size = max_position_value / entry_price
            
            final_size = min(size_by_risk, max_size)
            
            # Ensure we can afford the position (with margin for slippage and commission)
            required_capital = final_size * entry_price * 1.05  # 5% buffer
            if required_capital > balance:
                final_size = (balance * 0.95) / entry_price  # Use 95% of balance max
            
            return max(0.0, final_size)
            
        except Exception as e:
            logger.error(f"Error calculating position size: {e}")
            return 0.0
    
    def open_position(self, signal: Dict, bar_data: Dict, bar_index: int) -> bool:
        """Open a new position with realistic execution."""
        try:
            if self.current_position is not None:
                return False  # Already have a position
            
            # Calculate execution price with slippage
            current_price = bar_data['close']
            volatility = (bar_data['high'] - bar_data['low']) / bar_data['close']
            execution_price = self.apply_slippage(current_price, signal['type'], volatility)
            
            # Calculate position size
            position_size = self.calculate_position_size(signal, execution_price, self.balance)
            if position_size <= 0:
                logger.warning("Invalid position size calculated")
                return False
            
            # Calculate trade value and commission
            trade_value = position_size * execution_price
            commission = self.calculate_commission(trade_value)
            
            # Check if we have enough balance
            if trade_value + commission > self.balance:
                logger.warning(f"Insufficient balance for trade: Required ${trade_value + commission:.2f}, Available ${self.balance:.2f}")
                return False
            
            # Create position
            self.current_position = {
                'symbol': signal.get('symbol', 'UNKNOWN'),
                'type': signal['type'],
                'size': position_size,
                'entry_price': execution_price,
                'entry_bar': bar_index,
                'entry_time': bar_data['timestamp'],
                'stop_loss': signal.get('stop', execution_price * (0.98 if signal['type'] == 'BUY' else 1.02)),
                'tp1': signal.get('tp1', execution_price * (1.02 if signal['type'] == 'BUY' else 0.98)),
                'tp2': signal.get('tp2', execution_price * (1.05 if signal['type'] == 'BUY' else 0.95)),
                'has_hit_tp1': False,
                'commission_paid': commission,
                'unrealized_pnl': 0.0
            }
            
            # Deduct balance
            self.balance -= commission
            
            # Log trade
            trade_record = {
                'timestamp': bar_data['timestamp'],
                'bar_index': bar_index,
                'type': 'ENTRY',
                'signal_type': signal['type'],
                'symbol': self.current_position['symbol'],
                'price': execution_price,
                'size': position_size,
                'commission': commission,
                'balance_after': self.balance,
                'reason': signal.get('reason', 'Signal generated')
            }
            
            self.trades.append(trade_record)
            self.trade_count += 1
            
            logger.info(f"Position opened: {signal['type']} {position_size:.6f} at {execution_price:.4f}")
            return True
            
        except Exception as e:
            logger.error(f"Error opening position: {e}")
            return False
    
    def close_position(self, exit_type: str, exit_price: float, bar_data: Dict, bar_index: int, partial: float = 1.0) -> Optional[Dict]:
        """Close position (full or partial) with realistic execution."""
        try:
            if self.current_position is None:
                return None
            
            position = self.current_position
            exit_size = position['size'] * partial
            
            if exit_size <= 0:
                return None
            
            # Apply slippage to exit price
            volatility = (bar_data['high'] - bar_data['low']) / bar_data['close']
            exit_side = 'SELL' if position['type'] == 'BUY' else 'BUY'
            realistic_exit_price = self.apply_slippage(exit_price, exit_side, volatility)
            
            # Calculate P&L
            if position['type'] == 'BUY':
                gross_pnl = (realistic_exit_price - position['entry_price']) * exit_size
            else:
                gross_pnl = (position['entry_price'] - realistic_exit_price) * exit_size
            
            # Calculate commission for exit
            exit_trade_value = exit_size * realistic_exit_price
            exit_commission = self.calculate_commission(exit_trade_value)
            
            # Net P&L after commission
            net_pnl = gross_pnl - exit_commission - (position['commission_paid'] * partial)
            
            # Update balance
            self.balance += net_pnl
            
            # Update peak and drawdown
            if self.balance > self.peak_balance:
                self.peak_balance = self.balance
            
            current_drawdown = (self.peak_balance - self.balance) / self.peak_balance * 100
            if current_drawdown > self.max_drawdown:
                self.max_drawdown = current_drawdown
            
            # Create trade record
            trade_record = {
                'timestamp': bar_data['timestamp'],
                'bar_index': bar_index,
                'type': exit_type,
                'signal_type': position['type'],
                'symbol': position['symbol'],
                'entry_price': position['entry_price'],
                'exit_price': realistic_exit_price,
                'size': exit_size,
                'gross_pnl': gross_pnl,
                'commission': exit_commission + (position['commission_paid'] * partial),
                'net_pnl': net_pnl,
                'balance_after': self.balance,
                'bars_held': bar_index - position['entry_bar'],
                'reason': f"{exit_type} triggered"
            }
            
            self.trades.append(trade_record)
            
            # Update or close position
            if partial >= 1.0:
                # Full close
                self.positions.append(self.current_position.copy())
                self.current_position = None
                logger.info(f"Position closed: {exit_type} - P&L: ${net_pnl:.2f}")
            else:
                # Partial close
                position['size'] -= exit_size
                position['commission_paid'] *= (1 - partial)
                if exit_type == 'TAKE_PROFIT_1':
                    position['has_hit_tp1'] = True
                logger.info(f"Partial close: {exit_type} - P&L: ${net_pnl:.2f}, Remaining: {position['size']:.6f}")
            
            return trade_record
            
        except Exception as e:
            logger.error(f"Error closing position: {e}")
            return None
    
    def check_exit_conditions(self, bar_data: Dict, bar_index: int) -> List[Tuple[str, float, float]]:
        """Check all exit conditions and return triggers (exit_type, price, percentage)."""
        if self.current_position is None:
            return []
        
        position = self.current_position
        current_high = bar_data['high']
        current_low = bar_data['low']
        
        exits = []
        
        # Position timeout check
        bars_held = bar_index - position['entry_bar']
        if bars_held >= self.position_timeout_bars:
            exits.append(('TIMEOUT', bar_data['close'], 1.0))
            return exits  # Timeout takes priority
        
        if position['type'] == 'BUY':
            # Long position exits
            # 1. Stop Loss (highest priority)
            if current_low <= position['stop_loss']:
                exits.append(('STOP_LOSS', position['stop_loss'], 1.0))
                return exits  # Stop loss takes priority over everything
            
            # 2. Take Profit levels
            if not position['has_hit_tp1'] and current_high >= position['tp1']:
                exits.append(('TAKE_PROFIT_1', position['tp1'], 0.5))  # Close 50%
            elif position['has_hit_tp1'] and current_high >= position['tp2']:
                exits.append(('TAKE_PROFIT_2', position['tp2'], 1.0))  # Close remaining
        
        else:  # SHORT position
            # Short position exits
            # 1. Stop Loss (highest priority)
            if current_high >= position['stop_loss']:
                exits.append(('STOP_LOSS', position['stop_loss'], 1.0))
                return exits
            
            # 2. Take Profit levels
            if not position['has_hit_tp1'] and current_low <= position['tp1']:
                exits.append(('TAKE_PROFIT_1', position['tp1'], 0.5))
            elif position['has_hit_tp1'] and current_low <= position['tp2']:
                exits.append(('TAKE_PROFIT_2', position['tp2'], 1.0))
        
        return exits
    
    def run_backtest(self, df: pd.DataFrame, strategy_type: str = 'range') -> Dict:
        """Run the complete backtest."""
        try:
            logger.info(f"Starting backtest with {len(df)} bars using {strategy_type} strategy")
            self.reset()
            
            # Validate data
            if not self.validate_data(df):
                raise ValueError("Data validation failed")
            
            # Prepare data
            df = df.copy().reset_index(drop=True)
            
            for i in range(len(df)):
                bar_data = {
                    'timestamp': df.iloc[i]['timestamp'] if 'timestamp' in df.columns else pd.Timestamp.now(),
                    'open': df.iloc[i]['open'],
                    'high': df.iloc[i]['high'],
                    'low': df.iloc[i]['low'],
                    'close': df.iloc[i]['close'],
                    'volume': df.iloc[i]['volume'] if 'volume' in df.columns else 1000
                }
                
                # Check exit conditions first
                if self.current_position is not None:
                    exit_triggers = self.check_exit_conditions(bar_data, i)
                    
                    for exit_type, exit_price, partial in exit_triggers:
                        self.close_position(exit_type, exit_price, bar_data, i, partial)
                
                # Check for new entry signals (only if no position)
                if self.current_position is None:
                    # Build window for signal generation
                    window = df.iloc[:i+1].copy()
                    
                    try:
                        # Generate signals based on strategy type
                        if strategy_type == 'range':
                            signals = generate_range_signals(
                                window, 
                                self.config.get('rs75_strategy', {}), 
                                balance=self.balance
                            )
                        elif strategy_type == 'trend':
                            signals = generate_trend_signals(
                                window, 
                                self.config.get('trend_strategy', {}), 
                                balance=self.balance
                            )
                        else:
                            signals = []
                        
                        # Process first valid signal
                        if signals:
                            signal = signals[0]
                            signal['symbol'] = 'BACKTEST'  # Add symbol for tracking
                            self.open_position(signal, bar_data, i)
                            
                    except Exception as e:
                        logger.debug(f"Signal generation error at bar {i}: {e}")
                        continue
                
                # Record equity
                self.equity_curve.append({
                    'timestamp': bar_data['timestamp'],
                    'bar_index': i,
                    'equity': self.balance,
                    'has_position': self.current_position is not None
                })
            
            # Force close any remaining position
            if self.current_position is not None:
                logger.warning("Forcing close of remaining position at backtest end")
                final_bar = {
                    'timestamp': df.iloc[-1]['timestamp'] if 'timestamp' in df.columns else pd.Timestamp.now(),
                    'close': df.iloc[-1]['close'],
                    'high': df.iloc[-1]['high'],
                    'low': df.iloc[-1]['low']
                }
                self.close_position('FORCE_CLOSE', df.iloc[-1]['close'], final_bar, len(df)-1, 1.0)
            
            # Calculate final metrics
            results = self.calculate_metrics()
            
            logger.info(f"Backtest completed: Final balance ${self.balance:.2f}, Total return {results.get('total_return_pct', 0):.2f}%")
            
            return results
            
        except Exception as e:
            logger.error(f"Backtest execution failed: {e}")
            raise
    
    def validate_data(self, df: pd.DataFrame) -> bool:
        """Validate OHLCV data quality."""
        try:
            if df.empty:
                logger.error("Empty dataframe")
                return False
            
            required_cols = ['open', 'high', 'low', 'close']
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                logger.error(f"Missing columns: {missing_cols}")
                return False
            
            # Check for invalid prices
            if (df[required_cols] <= 0).any().any():
                logger.error("Data contains non-positive prices")
                return False
            
            # Check OHLC consistency
            invalid_bars = (
                (df['high'] < df['low']) |
                (df['high'] < df['open']) |
                (df['high'] < df['close']) |
                (df['low'] > df['open']) |
                (df['low'] > df['close'])
            ).sum()
            
            if invalid_bars > 0:
                logger.warning(f"Found {invalid_bars} bars with OHLC inconsistencies")
                # Fix inconsistencies
                df['high'] = df[['open', 'high', 'low', 'close']].max(axis=1)
                df['low'] = df[['open', 'high', 'low', 'close']].min(axis=1)
            
            logger.info(f"Data validation passed: {len(df)} bars")
            return True
            
        except Exception as e:
            logger.error(f"Data validation error: {e}")
            return False
    
    def calculate_metrics(self) -> Dict:
        """Calculate comprehensive backtest metrics."""
        try:
            metrics = {
                'initial_balance': self.initial_balance,
                'final_balance': self.balance,
                'total_return_pct': ((self.balance - self.initial_balance) / self.initial_balance) * 100,
                'max_drawdown_pct': self.max_drawdown,
                'total_trades': len([t for t in self.trades if t['type'] == 'ENTRY']),
                'winning_trades': 0,
                'losing_trades': 0,
                'total_pnl': 0,
                'gross_pnl': 0,
                'total_commission': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'largest_win': 0,
                'largest_loss': 0,
                'win_rate_pct': 0,
                'profit_factor': 0,
                'sharpe_ratio': 0,
                'avg_bars_per_trade': 0,
                'trades_per_month': 0
            }
            
            # Analyze exit trades
            exit_trades = [t for t in self.trades if 'net_pnl' in t]
            
            if exit_trades:
                pnls = [t['net_pnl'] for t in exit_trades]
                gross_pnls = [t['gross_pnl'] for t in exit_trades]
                commissions = [t['commission'] for t in exit_trades]
                bars_held = [t['bars_held'] for t in exit_trades]
                
                metrics['total_pnl'] = sum(pnls)
                metrics['gross_pnl'] = sum(gross_pnls)
                metrics['total_commission'] = sum(commissions)
                
                winning_trades = [p for p in pnls if p > 0]
                losing_trades = [p for p in pnls if p < 0]
                
                metrics['winning_trades'] = len(winning_trades)
                metrics['losing_trades'] = len(losing_trades)
                metrics['win_rate_pct'] = (len(winning_trades) / len(pnls) * 100) if pnls else 0
                
                if winning_trades:
                    metrics['avg_win'] = np.mean(winning_trades)
                    metrics['largest_win'] = max(winning_trades)
                
                if losing_trades:
                    metrics['avg_loss'] = np.mean(losing_trades)
                    metrics['largest_loss'] = min(losing_trades)
                
                # Profit factor
                total_wins = sum(winning_trades) if winning_trades else 0
                total_losses = abs(sum(losing_trades)) if losing_trades else 1
                metrics['profit_factor'] = total_wins / total_losses if total_losses > 0 else float('inf')
                
                # Average trade duration
                metrics['avg_bars_per_trade'] = np.mean(bars_held) if bars_held else 0
                
                # Sharpe ratio (simplified)
                if len(pnls) > 1:
                    returns = np.array(pnls) / self.initial_balance
                    if np.std(returns) > 0:
                        metrics['sharpe_ratio'] = np.mean(returns) / np.std(returns) * np.sqrt(252)  # Annualized
            
            # Trading frequency
            if self.equity_curve and len(self.equity_curve) > 1:
                total_days = (self.equity_curve[-1]['timestamp'] - self.equity_curve[0]['timestamp']).days
                metrics['trades_per_month'] = (metrics['total_trades'] / max(total_days, 1)) * 30
            
            return metrics
            
        except Exception as e:
            logger.error(f"Error calculating metrics: {e}")
            return {}

def load_config(config_path: str = "ars3_config.yaml") -> Dict:
    """Load and validate configuration."""
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        
        # Validate critical settings
        required_keys = ['initial_capital', 'rs75_strategy', 'risk_management']
        missing_keys = [k for k in required_keys if k not in config]
        if missing_keys:
            raise ValueError(f"Missing config keys: {missing_keys}")
        
        return config
    except Exception as e:
        logger.error(f"Config loading failed: {e}")
        raise

def load_data_from_broker(symbol: str = "BTC/USDT", timeframe: str = "1h", limit: int = 1000) -> Optional[pd.DataFrame]:
    """Load data from broker with enhanced error handling."""
    try:
        broker = Broker()
        broker.symbol = symbol
        broker.timeframe = timeframe
        
        logger.info(f"Fetching {limit} bars of {symbol} {timeframe} data...")
        ohlcv_data = broker.fetch_ohlcv(limit=limit)
        
        if not ohlcv_data:
            raise ValueError("No data received from broker")
        
        df = pd.DataFrame(ohlcv_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.sort_values('timestamp').reset_index(drop=True)
        
        logger.info(f"Successfully loaded {len(df)} bars from {df['timestamp'].min()} to {df['timestamp'].max()}")
        return df
        
    except Exception as e:
        logger.error(f"Error loading data from broker: {e}")
        return None

def load_data_from_csv(file_path: str) -> Optional[pd.DataFrame]:
    """Load and validate data from CSV."""
    try:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"CSV file not found: {file_path}")
        
        df = pd.read_csv(file_path)
        
        # Standardize column names
        column_mapping = {
            'time': 'timestamp', 'Time': 'timestamp', 'Date': 'timestamp', 'date': 'timestamp',
            'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'
        }
        df = df.rename(columns=column_mapping)
        
        # Validate and convert timestamp
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
            df = df.dropna(subset=['timestamp'])
        else:
            df['timestamp'] = pd.date_range(start='2023-01-01', periods=len(df), freq='H')
        
        # Validate required columns
        required_cols = ['open', 'high', 'low', 'close']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            raise ValueError(f"CSV missing required columns: {missing_cols}")
        
        # Add volume if missing
        if 'volume' not in df.columns:
            df['volume'] = 1000
        
        df = df.sort_values('timestamp').reset_index(drop=True)
        
        logger.info(f"Successfully loaded {len(df)} bars from CSV")
        return df
        
    except Exception as e:
        logger.error(f"Error loading data from CSV: {e}")
        return None

def create_performance_report(engine: BacktestEngine, save_path: str = "backtest_report.html") -> None:
    """Create comprehensive HTML performance report."""
    try:
        metrics = engine.calculate_metrics()
        
        # Create equity DataFrame
        equity_df = pd.DataFrame(engine.equity_curve)
        
        # Create trades DataFrame
        trades_df = pd.DataFrame([t for t in engine.trades if 'net_pnl' in t])
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>ARS-3 Backtest Report</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                .metric {{ display: inline-block; margin: 10px; padding: 10px; border: 1px solid #ccc; }}
                .positive {{ color: green; }}
                .negative {{ color: red; }}
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: right; }}
                th {{ background-color: #f2f2f2; }}
            </style>
        </head>
        <body>
            <h1>ARS-3 Backtest Performance Report</h1>
            <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            
            <h2>Summary Metrics</h2>
            <div class="metric">
                <strong>Total Return:</strong> 
                <span class="{'positive' if metrics.get('total_return_pct', 0) > 0 else 'negative'}">
                    {metrics.get('total_return_pct', 0):.2f}%
                </span>
            </div>
            <div class="metric">
                <strong>Final Balance:</strong> ${metrics.get('final_balance', 0):,.2f}
            </div>
            <div class="metric">
                <strong>Max Drawdown:</strong> {metrics.get('max_drawdown_pct', 0):.2f}%
            </div>
            <div class="metric">
                <strong>Win Rate:</strong> {metrics.get('win_rate_pct', 0):.1f}%
            </div>
            <div class="metric">
                <strong>Profit Factor:</strong> {metrics.get('profit_factor', 0):.2f}
            </div>
            <div class="metric">
                <strong>Sharpe Ratio:</strong> {metrics.get('sharpe_ratio', 0):.2f}
            </div>
        </body>
        </html>
        """
        
        with open(save_path, 'w') as f:
            f.write(html_content)
        
        logger.info(f"Performance report saved to {save_path}")
        
    except Exception as e:
        logger.error(f"Error creating performance report: {e}")

def plot_results(engine: BacktestEngine, save_path: str = "backtest_results.png") -> None:
    """Create comprehensive backtest visualization."""
    try:
        # Create equity DataFrame
        equity_df = pd.DataFrame(engine.equity_curve)
        if equity_df.empty:
            logger.warning("No equity data to plot")
            return
        
        fig, axes = plt.subplots(3, 1, figsize=(15, 12))
        
        # Equity curve
        axes[0].plot(equity_df['timestamp'], equity_df['equity'], 'b-', linewidth=2, label='Equity')
        axes[0].axhline(y=engine.initial_balance, color='r', linestyle='--', alpha=0.7, label='Initial Balance')
        axes[0].set_title('Equity Curve')
        axes[0].set_ylabel('Balance (USDT)')
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)
        
        # Mark entry and exit points
        entry_trades = [t for t in engine.trades if t['type'] == 'ENTRY']
        exit_trades = [t for t in engine.trades if t['type'] in ['STOP_LOSS', 'TAKE_PROFIT_1', 'TAKE_PROFIT_2', 'TIMEOUT']]
        
        for trade in entry_trades:
            color = 'green' if trade['signal_type'] == 'BUY' else 'red'
            marker = '^' if trade['signal_type'] == 'BUY' else 'v'
            axes[0].scatter(trade['timestamp'], trade['balance_after'], color=color, marker=marker, s=50, alpha=0.8, zorder=5)
        
        for trade in exit_trades:
            color = 'blue' if 'TAKE_PROFIT' in trade['type'] else 'orange'
            axes[0].scatter(trade['timestamp'], trade['balance_after'], color=color, marker='x', s=50, alpha=0.8, zorder=5)
        
        # Drawdown
        equity_series = equity_df['equity']
        peak = equity_series.expanding().max()
        drawdown = (peak - equity_series) / peak * 100
        
        axes[1].fill_between(equity_df['timestamp'], 0, -drawdown, alpha=0.7, color='red', label='Drawdown %')
        axes[1].set_title('Drawdown')
        axes[1].set_ylabel('Drawdown (%)')
        axes[1].legend()
        axes[1].grid(True, alpha=0.3)
        
        # Trade PnL distribution
        exit_pnls = [t['net_pnl'] for t in engine.trades if 'net_pnl' in t]
        if exit_pnls:
            axes[2].hist(exit_pnls, bins=20, alpha=0.7, color='purple', edgecolor='black')
            axes[2].axvline(x=0, color='red', linestyle='--', alpha=0.7)
            axes[2].set_title('Trade P&L Distribution')
            axes[2].set_xlabel('P&L per Trade (USDT)')
            axes[2].set_ylabel('Frequency')
            axes[2].grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        plt.show()
        
        logger.info(f"Backtest charts saved to {save_path}")
        
    except Exception as e:
        logger.error(f"Error creating plots: {e}")

def run_comprehensive_backtest(
    data_source: str = "broker",
    config_path: str = "ars3_config.yaml",
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    initial_balance: float = 10000,
    csv_path: str = None,
    strategy_type: str = "range",
    save_results: bool = True
) -> Optional[Dict]:
    """
    Run comprehensive backtest with full analysis and reporting.
    
    Args:
        data_source: "broker" or "csv"
        config_path: Path to configuration file
        symbol: Trading symbol for broker data
        timeframe: Timeframe for broker data
        initial_balance: Starting balance
        csv_path: Path to CSV file if using CSV data
        strategy_type: "range" or "trend"
        save_results: Whether to save detailed results
    
    Returns:
        Dict with comprehensive backtest results
    """
    print("=" * 80)
    print("ARS-3 PROFESSIONAL BACKTEST ENGINE v3.0")
    print("=" * 80)
    
    try:
        # Load configuration
        config = load_config(config_path)
        logger.info(f"Configuration loaded from {config_path}")
        
        # Load data
        print(f"Loading data from {data_source}...")
        if data_source == "broker":
            df = load_data_from_broker(symbol=symbol, timeframe=timeframe, limit=1000)
        elif data_source == "csv":
            if not csv_path:
                raise ValueError("csv_path required for CSV data source")
            df = load_data_from_csv(csv_path)
        else:
            raise ValueError("data_source must be 'broker' or 'csv'")
        
        if df is None or df.empty:
            raise ValueError("Failed to load data")
        
        print(f"Data loaded: {len(df)} bars from {df['timestamp'].min()} to {df['timestamp'].max()}")
        
        # Initialize backtest engine
        engine = BacktestEngine(config, initial_balance)
        
        # Run backtest
        print(f"Running {strategy_type} strategy backtest...")
        start_time = datetime.now()
        
        results = engine.run_backtest(df, strategy_type)
        
        end_time = datetime.now()
        execution_time = (end_time - start_time).total_seconds()
        
        print(f"Backtest completed in {execution_time:.2f} seconds")
        
        # Print results summary
        print("\n" + "=" * 60)
        print("BACKTEST RESULTS SUMMARY")
        print("=" * 60)
        
        key_metrics = [
            ('Initial Balance', f"${results['initial_balance']:,.2f}"),
            ('Final Balance', f"${results['final_balance']:,.2f}"),
            ('Total Return', f"{results['total_return_pct']:.2f}%"),
            ('Max Drawdown', f"{results['max_drawdown_pct']:.2f}%"),
            ('Total Trades', f"{results['total_trades']}"),
            ('Win Rate', f"{results['win_rate_pct']:.1f}%"),
            ('Profit Factor', f"{results['profit_factor']:.2f}"),
            ('Sharpe Ratio', f"{results['sharpe_ratio']:.2f}"),
            ('Total P&L', f"${results['total_pnl']:.2f}"),
            ('Total Commission', f"${results['total_commission']:.2f}"),
            ('Avg Win', f"${results['avg_win']:.2f}"),
            ('Avg Loss', f"${results['avg_loss']:.2f}"),
            ('Largest Win', f"${results['largest_win']:.2f}"),
            ('Largest Loss', f"${results['largest_loss']:.2f}")
        ]
        
        for metric, value in key_metrics:
            print(f"{metric:<20}: {value}")
        
        # Save results if requested
        if save_results:
            try:
                # Save equity curve
                equity_df = pd.DataFrame(engine.equity_curve)
                equity_df.to_csv("backtest_equity.csv", index=False)
                
                # Save trades
                trades_df = pd.DataFrame(engine.trades)
                trades_df.to_csv("backtest_trades.csv", index=False)
                
                # Save metrics
                with open("backtest_metrics.json", "w") as f:
                    json.dump(results, f, indent=4, default=str)
                
                # Create visualizations
                plot_results(engine)
                
                # Create performance report
                create_performance_report(engine)
                
                print("\nDetailed results saved:")
                print("- backtest_equity.csv")
                print("- backtest_trades.csv") 
                print("- backtest_metrics.json")
                print("- backtest_results.png")
                print("- backtest_report.html")
                
            except Exception as e:
                logger.error(f"Error saving results: {e}")
        
        # Add backtest metadata
        results.update({
            'config': config,
            'data_source': data_source,
            'symbol': symbol,
            'timeframe': timeframe,
            'strategy_type': strategy_type,
            'data_bars': len(df),
            'execution_time_seconds': execution_time,
            'equity_curve': engine.equity_curve,
            'trades': engine.trades
        })
        
        return results
        
    except Exception as e:
        logger.error(f"Backtest failed: {e}")
        print(f"\nBacktest failed: {e}")
        return None

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="ARS-3 Professional Backtest Engine")
    parser.add_argument("--data-source", choices=["broker", "csv"], default="broker", help="Data source")
    parser.add_argument("--csv-path", help="Path to CSV file (required if data-source=csv)")
    parser.add_argument("--symbol", default="BTC/USDT", help="Trading symbol")
    parser.add_argument("--timeframe", default="1h", help="Timeframe")
    parser.add_argument("--balance", type=float, default=10000, help="Initial balance")
    parser.add_argument("--strategy", choices=["range", "trend"], default="range", help="Strategy type")
    parser.add_argument("--config", default="ars3_config.yaml", help="Config file path")
    parser.add_argument("--no-save", action="store_true", help="Don't save results")
    
    args = parser.parse_args()
    
    try:
        results = run_comprehensive_backtest(
            data_source=args.data_source,
            config_path=args.config,
            symbol=args.symbol,
            timeframe=args.timeframe,
            initial_balance=args.balance,
            csv_path=args.csv_path,
            strategy_type=args.strategy,
            save_results=not args.no_save
        )
        
        if results:
            print(f"\nBacktest completed successfully!")
            print(f"Final Return: {results['total_return_pct']:.2f}%")
            print(f"Max Drawdown: {results['max_drawdown_pct']:.2f}%")
        else:
            print("\nBacktest failed!")
            exit(1)
            
    except KeyboardInterrupt:
        print("\nBacktest interrupted by user")
        exit(0)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        print(f"\nUnexpected error: {e}")
        exit(1)