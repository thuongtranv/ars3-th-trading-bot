"""
ars3_backtest.py (v4.1 - Complete Fixed Version)
Professional-grade backtest engine with regime-aware strategy selection,
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
import gc

# Import strategy modules
try:
    from ars3_strategy_rs75 import generate_signals as generate_range_signals
    from ars3_strategy_trend import generate_signals as generate_trend_signals
    from ars3_strategy_utils import MarketRegimeDetector, calculate_regime_indicators
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
    """Professional backtesting engine with regime-aware strategy selection."""
    
    def __init__(self, config: Dict, initial_balance: float = 10000.0):
        self.config = config
        self.initial_balance = initial_balance
        
        # Initialize regime detector
        regime_config = config.get('regime_detection', {})
        self.regime_detector = MarketRegimeDetector(regime_config)
        
        # Backtest state
        self.reset()
        
        # Backtest settings
        self.commission_rate = config.get('backtest', {}).get('commission_rate', 0.0004)
        self.slippage_pct = config.get('backtest', {}).get('slippage_pct', 0.01)
        self.realistic_execution = config.get('backtest', {}).get('realistic_execution', True)
        self.position_timeout_bars = config.get('backtest', {}).get('position_timeout_bars', 200)
        self.include_regime_analysis = config.get('backtest', {}).get('include_regime_analysis', True)
        
        logger.info(f"Backtest engine initialized - Balance: ${initial_balance:,.2f}, Commission: {self.commission_rate*100:.4f}%, Regime Detection: {'ON' if self.include_regime_analysis else 'OFF'}")
    
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
        
        # Regime tracking
        self.regime_history = []
        self.regime_stats = {}
        if hasattr(self, 'regime_detector'):
            self.regime_detector.current_regime = "undefined"
            self.regime_detector.cooldown_counter = 0
        
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
    
    def determine_strategy(self, df: pd.DataFrame, bar_index: int) -> Tuple[str, str]:
        """Determine which strategy to use based on market regime."""
        try:
            if not self.include_regime_analysis:
                return "range", "Default range strategy"
            
            # Need sufficient data for regime detection
            min_data = self.config.get('regime_detection', {}).get('min_data_points', 200)
            if len(df) < min_data:
                return "range", "Insufficient data for regime detection"
            
            # Calculate regime indicators
            indicators = calculate_regime_indicators(df, self.config.get('regime_detection', {}))
            
            if indicators is None:
                return "range", "Failed to calculate regime indicators"
            
            # Determine regime
            regime = self.regime_detector.determine(indicators)
            
            # Record regime history
            regime_record = {
                'bar_index': bar_index,
                'regime': regime,
                'scores': self.regime_detector.last_scores.copy(),
                'indicators': indicators.copy()
            }
            self.regime_history.append(regime_record)
            
            # Map regime to strategy
            if regime == "trending":
                return "trend", f"Trending regime detected (score: {self.regime_detector.last_scores.get('trend_score', 0):.3f})"
            elif regime == "ranging":
                return "range", f"Ranging regime detected (score: {self.regime_detector.last_scores.get('range_score', 0):.3f})"
            elif regime == "compression":
                return "none", "Compression regime - no trading"
            else:
                return "range", "Undefined regime - default to range"
                
        except Exception as e:
            logger.error(f"Error determining strategy: {e}")
            return "range", f"Error in regime detection: {str(e)}"
    
    def open_position(self, signal: Dict, bar_data: Dict, bar_index: int, strategy_used: str, strategy_reason: str) -> bool:
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

            # --- compute recent ATR for trailing-stop sizing (store on position) ---
            atr_value = None
            try:
                if hasattr(self, '_df') and self._df is not None and len(self._df) > 1:
                    start_idx = max(0, bar_index - 50)
                    recent = self._df.iloc[start_idx:bar_index+1]
                    if len(recent) >= 2:
                        high = recent['high']
                        low = recent['low']
                        close = recent['close']
                        tr1 = high - low
                        tr2 = (high - close.shift()).abs()
                        tr3 = (low - close.shift()).abs()
                        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                        atr_value = tr.rolling(window=self.config.get('trend_strategy', {}).get('atr_period', 14), min_periods=1).mean().iloc[-1]
            except Exception:
                atr_value = None
            if atr_value is None or (not np.isfinite(atr_value)):
                atr_value = (bar_data['high'] - bar_data['low'])

            self.current_position = {
                            'symbol': signal.get('symbol', 'BACKTEST'),
                            'type': signal['type'],
                            'size': position_size,
                            'entry_price': execution_price,
                            'entry_bar': bar_index,
                            'entry_time': bar_data['timestamp'],
                            'atr': atr_value,
                            'stop_loss': signal.get('stop', execution_price * (0.98 if signal['type'] == 'BUY' else 1.02)),
                            'tp1': signal.get('tp1', execution_price * (1.02 if signal['type'] == 'BUY' else 0.98)),
                            'tp2': signal.get('tp2', execution_price * (1.05 if signal['type'] == 'BUY' else 0.95)),
                            'has_hit_tp1': False,
                            'commission_paid': commission,
                            'unrealized_pnl': 0.0,
                            'strategy_used': strategy_used,
                            'strategy_reason': strategy_reason,
                            'regime': self.regime_detector.current_regime if self.include_regime_analysis else 'unknown'
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
                'strategy': strategy_used,
                'regime': self.current_position['regime'],
                'reason': signal.get('reason', 'Signal generated')
            }
            
            self.trades.append(trade_record)
            self.trade_count += 1
            
            logger.info(f"Position opened: {signal['type']} {position_size:.6f} at {execution_price:.4f} ({strategy_used} strategy)")
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
                'strategy': position.get('strategy_used', 'unknown'),
                'regime': position.get('regime', 'unknown'),
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
    current_close = bar_data['close']

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

        # 2. Take Profit levels and trailing stop after TP1
        if not position.get('has_hit_tp1', False) and current_high >= position['tp1']:
            exits.append(('TAKE_PROFIT_1', position['tp1'], 0.5))  # Close 50%
        elif position.get('has_hit_tp1', False):
            # Update trailing stop based on stored ATR
            atr_val = position.get('atr', None)
            if atr_val is None or not np.isfinite(atr_val):
                # fallback to bar range if no ATR
                atr_val = (current_high - current_low)
            # ATR trailing multiplier (2x by default)
            new_stop = current_close - 2 * atr_val
            if new_stop > position['stop_loss']:
                position['stop_loss'] = new_stop
            # If price hits trailing stop, exit remaining
            if current_low <= position['stop_loss']:
                exits.append(('STOP_LOSS', position['stop_loss'], 1.0))
                return exits
            # Hard TP2
            if current_high >= position['tp2']:
                exits.append(('TAKE_PROFIT_2', position['tp2'], 1.0))
        else:
            # Check TP2 directly if no TP1 hit flag (rare)
            if current_high >= position['tp2']:
                exits.append(('TAKE_PROFIT_2', position['tp2'], 1.0))

    else:
        # SHORT position exits (mirror logic)
        if current_high >= position['stop_loss']:
            exits.append(('STOP_LOSS', position['stop_loss'], 1.0))
            return exits

        if not position.get('has_hit_tp1', False) and current_low <= position['tp1']:
            exits.append(('TAKE_PROFIT_1', position['tp1'], 0.5))
        elif position.get('has_hit_tp1', False):
            atr_val = position.get('atr', None)
            if atr_val is None or not np.isfinite(atr_val):
                atr_val = (current_high - current_low)
            new_stop = current_close + 2 * atr_val
            if new_stop < position['stop_loss']:
                position['stop_loss'] = new_stop
            if current_high >= position['stop_loss']:
                exits.append(('STOP_LOSS', position['stop_loss'], 1.0))
                return exits
            if current_low <= position['tp2']:
                exits.append(('TAKE_PROFIT_2', position['tp2'], 1.0))
        else:
            if current_low <= position['tp2']:
                exits.append(('TAKE_PROFIT_2', position['tp2'], 1.0))

    return exits


    def run_backtest(self, df: pd.DataFrame, strategy_type: str = 'auto') -> Dict:
        """Run the complete backtest with regime-aware strategy selection."""
        try:
            logger.info(f"Starting backtest with {len(df)} bars using {'regime-aware' if strategy_type == 'auto' else strategy_type} strategy")
            self.reset()
            
            # Validate data
            if not self.validate_data(df):
                raise ValueError("Data validation failed")
            
            # Prepare data
            df = df.copy().reset_index(drop=True)
            # keep a copy of full dataframe for internal calculations (ATR etc.)
            self._df = df.copy()

            
            # Strategy usage tracking
            strategy_usage = {'range': 0, 'trend': 0, 'none': 0}
            
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
                        # Determine strategy based on regime (if auto mode)
                        if strategy_type == 'auto':
                            selected_strategy, strategy_reason = self.determine_strategy(window, i)
                        else:
                            selected_strategy = strategy_type
                            strategy_reason = f"Fixed {strategy_type} strategy"
                        
                        strategy_usage[selected_strategy] = strategy_usage.get(selected_strategy, 0) + 1
                        
                        # Generate signals based on selected strategy
                        signals = []
                        if selected_strategy == 'range':
                            signals = generate_range_signals(
                                window, 
                                self.config.get('rs75_strategy', {}), 
                                balance=self.balance
                            )
                        elif selected_strategy == 'trend':
                            signals = generate_trend_signals(
                                window, 
                                self.config.get('trend_strategy', {}), 
                                balance=self.balance
                            )
                        # 'none' strategy generates no signals
                        
                        # Process first valid signal
                        if signals:
                            signal = signals[0]
                            signal['symbol'] = 'BACKTEST'  # Add symbol for tracking
                            self.open_position(signal, bar_data, i, selected_strategy, strategy_reason)
                            
                    except Exception as e:
                        logger.debug(f"Signal generation error at bar {i}: {e}")
                        continue
                
                # Record equity
                self.equity_curve.append({
                    'timestamp': bar_data['timestamp'],
                    'bar_index': i,
                    'equity': self.balance,
                    'has_position': self.current_position is not None,
                    'regime': self.regime_detector.current_regime if self.include_regime_analysis else 'unknown'
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
            
            # Add regime and strategy analysis
            if self.include_regime_analysis:
                results['regime_analysis'] = self.analyze_regime_performance()
                results['strategy_usage'] = strategy_usage
            
            # Memory cleanup
            gc.collect()
            
            logger.info(f"Backtest completed: Final balance ${self.balance:.2f}, Total return {results.get('total_return_pct', 0):.2f}%")
            
            return results
            
        except Exception as e:
            logger.error(f"Backtest execution failed: {e}")
            raise
    
    def analyze_regime_performance(self) -> Dict:
        """Analyze performance by market regime."""
        try:
            if not self.regime_history:
                return {}
            
            # Group trades by regime
            regime_trades = {}
            for trade in self.trades:
                if 'net_pnl' in trade:  # Only exit trades
                    regime = trade.get('regime', 'unknown')
                    if regime not in regime_trades:
                        regime_trades[regime] = []
                    regime_trades[regime].append(trade)
            
            # Calculate stats per regime
            regime_stats = {}
            for regime, trades in regime_trades.items():
                if not trades:
                    continue
                
                pnls = [t['net_pnl'] for t in trades]
                winning_trades = [p for p in pnls if p > 0]
                losing_trades = [p for p in pnls if p < 0]
                
                regime_stats[regime] = {
                    'total_trades': len(trades),
                    'winning_trades': len(winning_trades),
                    'losing_trades': len(losing_trades),
                    'win_rate_pct': (len(winning_trades) / len(trades) * 100) if trades else 0,
                    'total_pnl': sum(pnls),
                    'avg_pnl': np.mean(pnls),
                    'avg_win': np.mean(winning_trades) if winning_trades else 0,
                    'avg_loss': np.mean(losing_trades) if losing_trades else 0,
                    'best_trade': max(pnls) if pnls else 0,
                    'worst_trade': min(pnls) if pnls else 0
                }
            
            # Regime distribution
            regime_counts = {}
            for record in self.regime_history:
                regime = record['regime']
                regime_counts[regime] = regime_counts.get(regime, 0) + 1
            
            total_bars = len(self.regime_history)
            regime_distribution = {regime: count/total_bars*100 for regime, count in regime_counts.items()}
            
            return {
                'regime_trades': regime_stats,
                'regime_distribution': regime_distribution,
                'regime_changes': len(set(r['regime'] for r in self.regime_history)),
                'total_regime_bars': total_bars
            }
            
        except Exception as e:
            logger.error(f"Error analyzing regime performance: {e}")
            return {}
    
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

def create_regime_analysis_chart(engine: BacktestEngine, save_path: str = "regime_analysis.png") -> None:
    """Create regime analysis visualization."""
    try:
        if not engine.include_regime_analysis or not engine.regime_history:
            logger.info("No regime data available for visualization")
            return
        
        fig, axes = plt.subplots(3, 1, figsize=(15, 12))
        
        # Regime timeline
        regime_df = pd.DataFrame(engine.regime_history)
        
        # Map regimes to colors
        regime_colors = {'trending': 'green', 'ranging': 'blue', 'compression': 'orange', 'undefined': 'gray'}
        
        for regime in regime_df['regime'].unique():
            regime_data = regime_df[regime_df['regime'] == regime]
            axes[0].scatter(regime_data['bar_index'], [regime]*len(regime_data), 
                           color=regime_colors.get(regime, 'black'), label=regime, alpha=0.7)
        
        axes[0].set_title('Market Regime Timeline')
        axes[0].set_ylabel('Regime')
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)
        
        # Regime scores over time
        if 'scores' in regime_df.columns and regime_df['scores'].iloc[0]:
            trend_scores = [s.get('trend_score', 0) for s in regime_df['scores']]
            range_scores = [s.get('range_score', 0) for s in regime_df['scores']]
            
            axes[1].plot(regime_df['bar_index'], trend_scores, label='Trend Score', color='green')
            axes[1].plot(regime_df['bar_index'], range_scores, label='Range Score', color='blue')
            axes[1].axhline(y=0.6, color='red', linestyle='--', alpha=0.7, label='Threshold')
            axes[1].set_title('Regime Scores Over Time')
            axes[1].set_ylabel('Score')
            axes[1].legend()
            axes[1].grid(True, alpha=0.3)
        
        # Regime distribution pie chart
        regime_counts = regime_df['regime'].value_counts()
        axes[2].pie(regime_counts.values, labels=regime_counts.index, autopct='%1.1f%%', 
                    colors=[regime_colors.get(r, 'gray') for r in regime_counts.index])
        axes[2].set_title('Market Regime Distribution')
        
        plt.tight_layout()
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        logger.info(f"Regime analysis chart saved to {save_path}")
        
    except Exception as e:
        logger.error(f"Error creating regime analysis chart: {e}")

def run_comprehensive_backtest(
    data_source: str = "broker",
    config_path: str = "ars3_config.yaml",
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    initial_balance: float = 10000,
    csv_path: str = None,
    strategy_type: str = "auto",  # 'auto', 'range', 'trend'
    save_results: bool = True
) -> Optional[Dict]:
    """
    Run comprehensive backtest with regime-aware strategy selection.
    
    Args:
        data_source: "broker" or "csv"
        config_path: Path to configuration file
        symbol: Trading symbol for broker data
        timeframe: Timeframe for broker data
        initial_balance: Starting balance
        csv_path: Path to CSV file if using CSV data
        strategy_type: "auto" (regime-aware), "range", "trend"
        save_results: Whether to save detailed results
    
    Returns:
        Dict with comprehensive backtest results including regime analysis
    """
    print("=" * 80)
    print("ARS-3 PROFESSIONAL BACKTEST ENGINE v4.1 (With Regime Detection)")
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
        strategy_desc = "Regime-Aware (Auto)" if strategy_type == "auto" else strategy_type.title()
        print(f"Running {strategy_desc} strategy backtest...")
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
            ('Sharpe Ratio', f"{results['sharpe_ratio']:.2f}")
        ]
        
        for metric, value in key_metrics:
            print(f"{metric:<20}: {value}")
        
        # Print regime analysis if available
        if 'regime_analysis' in results:
            regime_analysis = results['regime_analysis']
            print(f"\n{'='*60}")
            print("REGIME ANALYSIS")
            print(f"{'='*60}")
            
            if 'regime_distribution' in regime_analysis:
                print("Market Regime Distribution:")
                for regime, pct in regime_analysis['regime_distribution'].items():
                    print(f"  {regime.capitalize():<12}: {pct:.1f}%")
            
            if 'strategy_usage' in results:
                print(f"\nStrategy Usage:")
                total_bars = len(df)  # Fixed: define total_bars here
                for strategy, count in results['strategy_usage'].items():
                    pct = (count / total_bars * 100) if total_bars > 0 else 0
                    print(f"  {strategy.capitalize():<12}: {pct:.1f}% ({count} bars)")
            
            if 'regime_trades' in regime_analysis:
                print(f"\nPerformance by Regime:")
                for regime, stats in regime_analysis['regime_trades'].items():
                    print(f"  {regime.capitalize()}:")
                    print(f"    Trades: {stats['total_trades']}, Win Rate: {stats['win_rate_pct']:.1f}%")
                    print(f"    Total P&L: ${stats['total_pnl']:.2f}, Avg P&L: ${stats['avg_pnl']:.2f}")
        
        # Save results if requested
        if save_results:
            try:
                # Save equity curve
                equity_df = pd.DataFrame(engine.equity_curve)
                equity_df.to_csv("backtest_equity.csv", index=False)
                
                # Save trades
                trades_df = pd.DataFrame(engine.trades)
                trades_df.to_csv("backtest_trades.csv", index=False)
                
                # Save regime history if available
                if engine.regime_history:
                    regime_df = pd.DataFrame(engine.regime_history)
                    regime_df.to_csv("backtest_regimes.csv", index=False)
                
                # Save metrics
                with open("backtest_metrics.json", "w") as f:
                    json.dump(results, f, indent=4, default=str)
                
                # Create visualizations
                plot_results(engine)
                
                # Create regime analysis chart
                if engine.include_regime_analysis:
                    create_regime_analysis_chart(engine)
                
                # Create performance report
                create_performance_report(engine)
                
                print("\nDetailed results saved:")
                print("- backtest_equity.csv")
                print("- backtest_trades.csv")
                if engine.regime_history:
                    print("- backtest_regimes.csv")
                print("- backtest_metrics.json")
                print("- backtest_results.png")
                if engine.include_regime_analysis:
                    print("- regime_analysis.png")
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
            'trades': engine.trades,
            'regime_history': engine.regime_history if engine.include_regime_analysis else []
        })
        
        return results
        
    except Exception as e:
        logger.error(f"Backtest failed: {e}")
        print(f"\nBacktest failed: {e}")
        return None

def create_performance_report(engine: BacktestEngine, save_path: str = "backtest_report.html") -> None:
    """Create comprehensive HTML performance report with regime analysis."""
    try:
        metrics = engine.calculate_metrics()
        
        # Create equity DataFrame
        equity_df = pd.DataFrame(engine.equity_curve)
        
        # Create trades DataFrame
        trades_df = pd.DataFrame([t for t in engine.trades if 'net_pnl' in t])
        
        # Regime analysis section
        regime_section = ""
        if engine.include_regime_analysis and engine.regime_history:
            regime_analysis = engine.analyze_regime_performance()
            
            regime_section = """
            <h2>Market Regime Analysis</h2>
            <h3>Regime Distribution</h3>
            """
            
            if 'regime_distribution' in regime_analysis:
                regime_section += "<ul>"
                for regime, pct in regime_analysis['regime_distribution'].items():
                    regime_section += f"<li>{regime.capitalize()}: {pct:.1f}%</li>"
                regime_section += "</ul>"
            
            if 'regime_trades' in regime_analysis:
                regime_section += "<h3>Performance by Regime</h3><table border='1'>"
                regime_section += "<tr><th>Regime</th><th>Trades</th><th>Win Rate</th><th>Total P&L</th><th>Avg P&L</th></tr>"
                
                for regime, stats in regime_analysis['regime_trades'].items():
                    regime_section += f"""
                    <tr>
                        <td>{regime.capitalize()}</td>
                        <td>{stats['total_trades']}</td>
                        <td>{stats['win_rate_pct']:.1f}%</td>
                        <td>${stats['total_pnl']:.2f}</td>
                        <td>${stats['avg_pnl']:.2f}</td>
                    </tr>
                    """
                regime_section += "</table>"
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>ARS-3 Backtest Report with Regime Analysis</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                .metric {{ display: inline-block; margin: 10px; padding: 10px; border: 1px solid #ccc; }}
                .positive {{ color: green; }}
                .negative {{ color: red; }}
                table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: right; }}
                th {{ background-color: #f2f2f2; }}
                .regime-section {{ background-color: #f9f9f9; padding: 15px; margin: 20px 0; border-radius: 5px; }}
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
            
            <div class="regime-section">
                {regime_section}
            </div>
            
            <h2>Trade Details</h2>
            <div class="metric">
                <strong>Total Trades:</strong> {metrics.get('total_trades', 0)}
            </div>
            <div class="metric">
                <strong>Winning Trades:</strong> {metrics.get('winning_trades', 0)}
            </div>
            <div class="metric">
                <strong>Losing Trades:</strong> {metrics.get('losing_trades', 0)}
            </div>
            <div class="metric">
                <strong>Average Win:</strong> ${metrics.get('avg_win', 0):.2f}
            </div>
            <div class="metric">
                <strong>Average Loss:</strong> ${metrics.get('avg_loss', 0):.2f}
            </div>
            <div class="metric">
                <strong>Largest Win:</strong> ${metrics.get('largest_win', 0):.2f}
            </div>
            <div class="metric">
                <strong>Largest Loss:</strong> ${metrics.get('largest_loss', 0):.2f}
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
        
        fig, axes = plt.subplots(4, 1, figsize=(15, 16))
        
        # Equity curve with regime coloring
        axes[0].plot(equity_df['timestamp'], equity_df['equity'], 'b-', linewidth=2, label='Equity')
        axes[0].axhline(y=engine.initial_balance, color='r', linestyle='--', alpha=0.7, label='Initial Balance')
        
        # Color background by regime if available
        if engine.include_regime_analysis and 'regime' in equity_df.columns:
            regime_colors = {'trending': 'lightgreen', 'ranging': 'lightblue', 'compression': 'lightyellow', 'undefined': 'lightgray'}
            
            for i in range(len(equity_df) - 1):
                regime = equity_df.iloc[i]['regime']
                color = regime_colors.get(regime, 'white')
                axes[0].axvspan(equity_df.iloc[i]['timestamp'], equity_df.iloc[i+1]['timestamp'], 
                               alpha=0.3, color=color)
        
        axes[0].set_title('Equity Curve with Market Regimes')
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
        
        # Trade P&L distribution
        exit_pnls = [t['net_pnl'] for t in engine.trades if 'net_pnl' in t]
        if exit_pnls:
            axes[2].hist(exit_pnls, bins=20, alpha=0.7, color='purple', edgecolor='black')
            axes[2].axvline(x=0, color='red', linestyle='--', alpha=0.7)
            axes[2].set_title('Trade P&L Distribution')
            axes[2].set_xlabel('P&L per Trade (USDT)')
            axes[2].set_ylabel('Frequency')
            axes[2].grid(True, alpha=0.3)
        
        # Regime distribution (if available)
        if engine.include_regime_analysis and engine.regime_history:
            regime_counts = {}
            for record in engine.regime_history:
                regime = record['regime']
                regime_counts[regime] = regime_counts.get(regime, 0) + 1
            
            if regime_counts:
                regimes = list(regime_counts.keys())
                counts = list(regime_counts.values())
                colors = ['green' if r == 'trending' else 'blue' if r == 'ranging' else 'orange' if r == 'compression' else 'gray' for r in regimes]
                
                axes[3].pie(counts, labels=regimes, autopct='%1.1f%%', colors=colors)
                axes[3].set_title('Market Regime Distribution')
        else:
            axes[3].text(0.5, 0.5, 'Regime Analysis Disabled', horizontalalignment='center', 
                        verticalalignment='center', transform=axes[3].transAxes, fontsize=14)
            axes[3].set_title('Market Regime Analysis')
        
        plt.tight_layout()
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        logger.info(f"Backtest charts saved to {save_path}")
        
    except Exception as e:
        logger.error(f"Error creating plots: {e}")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="ARS-3 Professional Backtest Engine with Regime Detection")
    parser.add_argument("--data-source", choices=["broker", "csv"], default="broker", help="Data source")
    parser.add_argument("--csv-path", help="Path to CSV file (required if data-source=csv)")
    parser.add_argument("--symbol", default="BTC/USDT", help="Trading symbol")
    parser.add_argument("--timeframe", default="1h", help="Timeframe")
    parser.add_argument("--balance", type=float, default=10000, help="Initial balance")
    parser.add_argument("--strategy", choices=["auto", "range", "trend"], default="auto", help="Strategy type")
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
            
            if 'regime_analysis' in results:
                total_regime_changes = results['regime_analysis'].get('regime_changes', 0)
                print(f"Regime Changes: {total_regime_changes}")
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