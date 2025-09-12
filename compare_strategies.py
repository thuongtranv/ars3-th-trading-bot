"""
compare_strategies.py

Script to compare performance between original trend strategy and optimized version
"""

import pandas as pd
import numpy as np
import yaml
import logging
from datetime import datetime
import matplotlib.pyplot as plt
import json

# Import strategies
from ars3_strategy_trend import generate_signals as generate_original_signals
from ars3_strategy_trend_v8_optimized import generate_signals as generate_optimized_signals
from ars3_backtest import BacktestEngine, load_config

logger = logging.getLogger(__name__)

def load_data_from_broker(symbol: str = "BTC/USDT", timeframe: str = "1h", limit: int = 1000):
    """Load data from broker for testing."""
    try:
        from ars3_broker_ccxt import Broker
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

def compare_strategies(df: pd.DataFrame, initial_balance: float = 10000.0):
    """Compare original vs optimized trend strategy."""
    
    # Load configuration
    config = load_config()
    
    print("=" * 80)
    print("TREND STRATEGY COMPARISON: ORIGINAL vs OPTIMIZED")
    print("=" * 80)
    
    # Test original strategy
    print("\nTesting Original Trend Strategy...")
    engine_original = BacktestEngine(config, initial_balance)
    results_original = engine_original.run_backtest(df, strategy_type='trend')
    
    # Test optimized strategy
    print("\nTesting Optimized Trend Strategy...")
    engine_optimized = BacktestEngine(config, initial_balance)
    results_optimized = engine_optimized.run_backtest(df, strategy_type='trend')
    
    # Compare results
    print("\n" + "=" * 80)
    print("PERFORMANCE COMPARISON")
    print("=" * 80)
    
    comparison_metrics = [
        ('Total Return (%)', 'total_return_pct'),
        ('Final Balance ($)', 'final_balance'),
        ('Max Drawdown (%)', 'max_drawdown_pct'),
        ('Total Trades', 'total_trades'),
        ('Win Rate (%)', 'win_rate_pct'),
        ('Profit Factor', 'profit_factor'),
        ('Sharpe Ratio', 'sharpe_ratio'),
        ('Average Win ($)', 'avg_win'),
        ('Average Loss ($)', 'avg_loss'),
        ('Largest Win ($)', 'largest_win'),
        ('Largest Loss ($)', 'largest_loss')
    ]
    
    print(f"{'Metric':<25} {'Original':<15} {'Optimized':<15} {'Improvement':<15}")
    print("-" * 70)
    
    improvements = {}
    
    for metric_name, metric_key in comparison_metrics:
        original_val = results_original.get(metric_key, 0)
        optimized_val = results_optimized.get(metric_key, 0)
        
        if metric_key in ['total_return_pct', 'win_rate_pct', 'profit_factor', 'sharpe_ratio', 'avg_win', 'largest_win']:
            # Higher is better
            if original_val != 0:
                improvement = ((optimized_val - original_val) / abs(original_val)) * 100
            else:
                improvement = 0
        elif metric_key in ['max_drawdown_pct', 'avg_loss', 'largest_loss']:
            # Lower is better
            if original_val != 0:
                improvement = ((original_val - optimized_val) / abs(original_val)) * 100
            else:
                improvement = 0
        else:
            # Neutral metrics
            improvement = 0
        
        improvements[metric_key] = improvement
        
        print(f"{metric_name:<25} {original_val:<15.2f} {optimized_val:<15.2f} {improvement:<15.1f}%")
    
    # Calculate expectancy for both strategies
    print("\n" + "=" * 80)
    print("EXPECTANCY ANALYSIS")
    print("=" * 80)
    
    # Original strategy expectancy
    original_win_rate = results_original.get('win_rate_pct', 0) / 100
    original_avg_win = results_original.get('avg_win', 0)
    original_avg_loss = abs(results_original.get('avg_loss', 0))
    original_expectancy = (original_win_rate * original_avg_win) - ((1 - original_win_rate) * original_avg_loss)
    
    # Optimized strategy expectancy
    optimized_win_rate = results_optimized.get('win_rate_pct', 0) / 100
    optimized_avg_win = results_optimized.get('avg_win', 0)
    optimized_avg_loss = abs(results_optimized.get('avg_loss', 0))
    optimized_expectancy = (optimized_win_rate * optimized_avg_win) - ((1 - optimized_win_rate) * optimized_avg_loss)
    
    expectancy_improvement = ((optimized_expectancy - original_expectancy) / abs(original_expectancy)) * 100 if original_expectancy != 0 else 0
    
    print(f"Original Strategy Expectancy:   ${original_expectancy:.2f}")
    print(f"Optimized Strategy Expectancy:  ${optimized_expectancy:.2f}")
    print(f"Expectancy Improvement:         {expectancy_improvement:.1f}%")
    
    # Risk-Adjusted Returns
    print("\n" + "=" * 80)
    print("RISK-ADJUSTED ANALYSIS")
    print("=" * 80)
    
    original_sharpe = results_original.get('sharpe_ratio', 0)
    optimized_sharpe = results_optimized.get('sharpe_ratio', 0)
    
    print(f"Original Sharpe Ratio:   {original_sharpe:.2f}")
    print(f"Optimized Sharpe Ratio:  {optimized_sharpe:.2f}")
    print(f"Sharpe Improvement:      {((optimized_sharpe - original_sharpe) / abs(original_sharpe)) * 100:.1f}%" if original_sharpe != 0 else "N/A")
    
    # Trade Analysis
    print("\n" + "=" * 80)
    print("TRADE ANALYSIS")
    print("=" * 80)
    
    original_trades = results_original.get('total_trades', 0)
    optimized_trades = results_optimized.get('total_trades', 0)
    
    print(f"Original Total Trades:   {original_trades}")
    print(f"Optimized Total Trades:  {optimized_trades}")
    print(f"Trade Count Change:      {optimized_trades - original_trades}")
    
    # Create comparison chart
    create_comparison_chart(engine_original, engine_optimized)
    
    # Save detailed comparison
    save_comparison_results(results_original, results_optimized, improvements)
    
    return {
        'original': results_original,
        'optimized': results_optimized,
        'improvements': improvements,
        'expectancy_original': original_expectancy,
        'expectancy_optimized': optimized_expectancy,
        'expectancy_improvement': expectancy_improvement
    }

def create_comparison_chart(engine_original, engine_optimized):
    """Create comparison chart between strategies."""
    try:
        fig, axes = plt.subplots(2, 2, figsize=(15, 12))
        
        # Equity curves
        equity_original = pd.DataFrame(engine_original.equity_curve)
        equity_optimized = pd.DataFrame(engine_optimized.equity_curve)
        
        axes[0, 0].plot(equity_original['timestamp'], equity_original['equity'], 
                       'b-', linewidth=2, label='Original Strategy')
        axes[0, 0].plot(equity_optimized['timestamp'], equity_optimized['equity'], 
                       'r-', linewidth=2, label='Optimized Strategy')
        axes[0, 0].axhline(y=engine_original.initial_balance, color='gray', 
                          linestyle='--', alpha=0.7, label='Initial Balance')
        axes[0, 0].set_title('Equity Curve Comparison')
        axes[0, 0].set_ylabel('Balance (USDT)')
        axes[0, 0].legend()
        axes[0, 0].grid(True, alpha=0.3)
        
        # Drawdown comparison
        original_equity = equity_original['equity']
        optimized_equity = equity_optimized['equity']
        
        original_peak = original_equity.expanding().max()
        optimized_peak = optimized_equity.expanding().max()
        
        original_dd = (original_peak - original_equity) / original_peak * 100
        optimized_dd = (optimized_peak - optimized_equity) / optimized_peak * 100
        
        axes[0, 1].fill_between(equity_original['timestamp'], 0, -original_dd, 
                               alpha=0.7, color='blue', label='Original DD')
        axes[0, 1].fill_between(equity_optimized['timestamp'], 0, -optimized_dd, 
                               alpha=0.7, color='red', label='Optimized DD')
        axes[0, 1].set_title('Drawdown Comparison')
        axes[0, 1].set_ylabel('Drawdown (%)')
        axes[0, 1].legend()
        axes[0, 1].grid(True, alpha=0.3)
        
        # Performance metrics comparison
        metrics = ['Total Return (%)', 'Win Rate (%)', 'Profit Factor', 'Sharpe Ratio']
        original_values = [
            engine_original.calculate_metrics().get('total_return_pct', 0),
            engine_original.calculate_metrics().get('win_rate_pct', 0),
            engine_original.calculate_metrics().get('profit_factor', 0),
            engine_original.calculate_metrics().get('sharpe_ratio', 0)
        ]
        optimized_values = [
            engine_optimized.calculate_metrics().get('total_return_pct', 0),
            engine_optimized.calculate_metrics().get('win_rate_pct', 0),
            engine_optimized.calculate_metrics().get('profit_factor', 0),
            engine_optimized.calculate_metrics().get('sharpe_ratio', 0)
        ]
        
        x = np.arange(len(metrics))
        width = 0.35
        
        axes[1, 0].bar(x - width/2, original_values, width, label='Original', alpha=0.8)
        axes[1, 0].bar(x + width/2, optimized_values, width, label='Optimized', alpha=0.8)
        axes[1, 0].set_title('Key Metrics Comparison')
        axes[1, 0].set_ylabel('Value')
        axes[1, 0].set_xticks(x)
        axes[1, 0].set_xticklabels(metrics, rotation=45)
        axes[1, 0].legend()
        axes[1, 0].grid(True, alpha=0.3)
        
        # Trade distribution
        original_trades = [t for t in engine_original.trades if 'net_pnl' in t]
        optimized_trades = [t for t in engine_optimized.trades if 'net_pnl' in t]
        
        if original_trades and optimized_trades:
            original_pnls = [t['net_pnl'] for t in original_trades]
            optimized_pnls = [t['net_pnl'] for t in optimized_trades]
            
            axes[1, 1].hist(original_pnls, bins=20, alpha=0.7, label='Original', color='blue')
            axes[1, 1].hist(optimized_pnls, bins=20, alpha=0.7, label='Optimized', color='red')
            axes[1, 1].axvline(x=0, color='black', linestyle='--', alpha=0.7)
            axes[1, 1].set_title('Trade P&L Distribution')
            axes[1, 1].set_xlabel('P&L per Trade (USDT)')
            axes[1, 1].set_ylabel('Frequency')
            axes[1, 1].legend()
            axes[1, 1].grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig('strategy_comparison.png', dpi=300, bbox_inches='tight')
        plt.close()
        
        print(f"\nComparison chart saved as 'strategy_comparison.png'")
        
    except Exception as e:
        logger.error(f"Error creating comparison chart: {e}")

def save_comparison_results(results_original, results_optimized, improvements):
    """Save detailed comparison results to JSON."""
    try:
        comparison_data = {
            'timestamp': datetime.now().isoformat(),
            'original_strategy': results_original,
            'optimized_strategy': results_optimized,
            'improvements': improvements,
            'summary': {
                'expectancy_improvement': improvements.get('total_return_pct', 0),
                'win_rate_improvement': improvements.get('win_rate_pct', 0),
                'sharpe_improvement': improvements.get('sharpe_ratio', 0)
            }
        }
        
        with open('strategy_comparison_results.json', 'w') as f:
            json.dump(comparison_data, f, indent=4, default=str)
        
        print(f"Detailed comparison results saved as 'strategy_comparison_results.json'")
        
    except Exception as e:
        logger.error(f"Error saving comparison results: {e}")

def main():
    """Main comparison function."""
    print("Loading data for strategy comparison...")
    
    # Load data
    df = load_data_from_broker(symbol="BTC/USDT", timeframe="1h", limit=1000)
    
    if df is None or df.empty:
        print("Failed to load data. Exiting.")
        return
    
    print(f"Loaded {len(df)} bars of data")
    
    # Run comparison
    results = compare_strategies(df, initial_balance=10000.0)
    
    # Print final summary
    print("\n" + "=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)
    
    expectancy_improvement = results['expectancy_improvement']
    
    if expectancy_improvement > 0:
        print(f"✅ OPTIMIZED STRATEGY SHOWS {expectancy_improvement:.1f}% IMPROVEMENT IN EXPECTANCY")
        print("🎯 The optimized strategy is BETTER than the original")
    else:
        print(f"❌ OPTIMIZED STRATEGY SHOWS {abs(expectancy_improvement):.1f}% DECLINE IN EXPECTANCY")
        print("⚠️  The optimized strategy needs further refinement")
    
    print(f"\nOriginal Expectancy:  ${results['expectancy_original']:.2f}")
    print(f"Optimized Expectancy: ${results['expectancy_optimized']:.2f}")
    
    return results

if __name__ == "__main__":
    main()