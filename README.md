# ARS-3 Professional Trading Bot System

## Overview
ARS-3 is a sophisticated, production-ready automated cryptocurrency trading bot featuring advanced market regime detection and multi-strategy execution. The system intelligently switches between range-scalping and trend-following strategies based on real-time market analysis, providing a complete ecosystem for algorithmic trading.

### Key Features
- **Professional Market Regime Detection**: Automatically identifies trending, ranging, and compression market states
- **Multi-Strategy Architecture**: RS-75 Range Scalper + Trend Following with intelligent switching
- **Advanced Risk Management**: Position sizing, stop losses, take profits, and emergency controls
- **Production-Grade Monitoring**: System health checks, memory management, and comprehensive logging
- **Exchange Integration**: Full CCXT integration with Binance (spot/futures) and testnet support
- **Real-time Notifications**: Telegram alerts for trades, errors, and system status
- **Automated Backtesting**: Professional backtesting engine with regime analysis
- **Auto-restart System**: Intelligent process monitoring with crash recovery
- **Daily Reporting**: Automated performance reports with charts via Telegram/email
- **Data Persistence**: CSV logging, position recovery, and equity tracking
- **Safety First**: Multiple layers of protection against losses and system failures

## System Architecture

### Core Trading Components
```
ars3_live.py                     # Main trading engine with regime detection
ars3_strategy_rs75.py            # Range scalping strategy (Bollinger Bands + RSI)
ars3_strategy_trend.py           # Trend following strategy (EMA crossover + ADX)
ars3_strategy_utils.py           # Market regime detector and utilities  
ars3_broker_ccxt.py              # Exchange integration and order execution
ars3_config.yaml                 # Comprehensive configuration file
```

### System Management Components
```
ars3_autostart.py               # Enhanced auto-restart system with monitoring
ars3_backtest.py                # Professional backtesting engine with regime analysis
ars3_report_daily.py            # Daily performance reporting system
integration_test.py             # Complete system integration tests
```

### Configuration & Documentation
```
.env                            # Environment variables (API keys, tokens)
requirements.txt                # Python dependencies with proper versions
README.md                       # This comprehensive guide
STRATEGY.md                     # Detailed strategy documentation
```

### Auto-Generated Data Files
```
equity_live.csv                 # Real-time equity curve tracking
trades_live.csv                 # Complete trade execution log
position.json                   # Current position state persistence
ars3_bot.log                    # System logs with rotation
ars3_autostart.log              # Auto-restart system logs
backtest_*.csv                  # Backtesting results and analysis
equity_report.png               # Daily performance charts
```

## Quick Start Guide

### 1. Prerequisites
- **Python 3.8+** (recommended 3.9+)
- **Binance account** with API access (for live trading)
- **Telegram bot** token and chat ID (for notifications)
- **2GB+ RAM** and stable internet connection

### 2. Installation
```bash
# Clone repository
git clone <repository-url>
cd ars3-trading-bot

# Install dependencies
pip install -r requirements.txt

# Verify installation
python integration_test.py
```

### 3. Configuration

#### Environment Variables (.env)
```bash
# Required for all modes
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id

# Required for live trading only
BINANCE_API_KEY=your_binance_api_key
BINANCE_API_SECRET=your_binance_secret_key

# Optional - Email notifications
EMAIL_USER=your_email@gmail.com
EMAIL_PASS=your_app_password
EMAIL_TO=recipient@gmail.com
```

#### Main Configuration (ars3_config.yaml)
Key settings to customize:
```yaml
# Basic Settings
coins: ["BTC/USDT", "ETH/USDT", "BNB/USDT"]  # Trading symbols
timeframe: "1h"                               # Chart timeframe
initial_capital: 10000.0                      # Starting balance
simulation_mode: true                         # Paper trading mode

# Risk Management
risk_per_trade: 0.01                         # Risk 1% per trade
max_dd_percent: 15                           # Max 15% drawdown
max_daily_loss: 500                          # Daily loss limit

# Exchange Settings
exchange: "binance"                          # Exchange name
futures: false                               # Spot trading (safer)
testnet: true                               # Use testnet first!

# Strategy Selection (Automatic based on regime detection)
# - "ranging" markets -> RS-75 Range Scalper
# - "trending" markets -> Trend Following
# - "compression" -> Wait for breakout
```

### 4. Running the System

#### Option A: Complete System with Auto-Restart (Recommended)
```bash
# Start full system with intelligent monitoring
python ars3_autostart.py
```
**Features:**
- Launches main trading bot automatically
- Intelligent crash detection and recovery
- System health monitoring (CPU, memory, disk)
- Real-time Telegram notifications
- Emergency stop functionality
- Cross-platform support (Windows/Linux/Mac)

#### Option B: Individual Components (Manual Control)
```bash
# 1. Main trading bot
python ars3_live.py

# 2. Daily reports (run separately or schedule)
python ars3_report_daily.py

# 3. Backtesting and strategy validation
python ars3_backtest.py --symbol BTC/USDT --strategy auto

# 4. System integration tests
python integration_test.py
```

#### Option C: Backtesting and Strategy Analysis
```bash
# Basic regime-aware backtest
python ars3_backtest.py --symbol BTC/USDT --strategy auto

# Test specific strategies
python ars3_backtest.py --symbol ETH/USDT --strategy range --timeframe 1h
python ars3_backtest.py --symbol BNB/USDT --strategy trend --timeframe 4h

# Advanced backtesting with custom parameters
python ars3_backtest.py \
    --data-source broker \
    --symbol SOL/USDT \
    --timeframe 1h \
    --balance 20000 \
    --strategy auto \
    --config ars3_config.yaml
```

### 5. System Monitoring

#### Real-time Monitoring Options
- **Console Output**: Live logging to terminal with color coding
- **Log Files**: Detailed logs in `ars3_bot.log` with automatic rotation
- **Telegram Alerts**: Instant notifications for trades, errors, and system status
- **CSV Files**: `equity_live.csv` and `trades_live.csv` for analysis
- **Position Recovery**: Automatic position restoration after restart

#### Auto-Restart System Features
- **Process Health Monitoring**: CPU, memory, and connection checks
- **Intelligent Restart Logic**: Exponential backoff and failure tracking
- **Emergency Stop Integration**: File-based emergency halt mechanism
- **Resource Usage Alerts**: Proactive notifications for system issues
- **Graceful Shutdown**: Clean process termination with position preservation

#### Daily Reporting System
- **Automated Generation**: Daily P&L and performance summaries
- **Visual Charts**: Equity curves and drawdown analysis
- **Multi-channel Delivery**: Telegram and email with chart attachments
- **Performance Analytics**: Win rate, drawdown, and risk metrics
- **Historical Tracking**: Trend analysis and performance comparison

## Market Regime Detection

### Professional Multi-Factor Analysis
The system continuously analyzes market conditions using a sophisticated scoring system:

**Trending Markets** (Use Trend Following):
- High ADX (>25) indicating strong directional movement
- Rising volatility (nATR) supporting momentum
- Clear directional slope in long-term price movement
- Volume confirmation supporting the trend

**Ranging Markets** (Use RS-75 Scalper):
- Low ADX (<22) indicating sideways price action
- Moderate volatility within acceptable bands
- Oscillating price movement around mean levels
- Clear support/resistance levels for mean reversion

**Compression Markets** (Wait Mode):
- Very low volatility indicating market consolidation
- Tight Bollinger Bands suggesting breakout preparation
- Reduced trading volume and price movement

### Intelligent Strategy Switching
- **Hysteresis System**: Prevents excessive regime changes with entry/exit thresholds
- **Cooldown Periods**: Stability controls between strategy switches
- **Confidence Scoring**: Higher regime confidence enables larger position sizes
- **Real-time Adaptation**: Continuous market state evaluation

## Comprehensive Backtesting System

### Professional Strategy Testing
```bash
# Comprehensive regime-aware backtesting
python ars3_backtest.py --data-source broker --symbol BTC/USDT --strategy auto

# Strategy comparison testing
python ars3_backtest.py --symbol ETH/USDT --strategy range  # Range-only
python ars3_backtest.py --symbol ETH/USDT --strategy trend  # Trend-only
python ars3_backtest.py --symbol ETH/USDT --strategy auto   # Regime-aware
```

### Advanced Backtesting Features
- **Regime Analysis**: Performance breakdown by market conditions
- **Realistic Execution**: Slippage and commission modeling
- **Risk Metrics**: Comprehensive drawdown and volatility analysis
- **Monte Carlo Simulation**: Statistical confidence testing
- **Visual Analysis**: Equity curves, regime distribution, and performance charts
- **Strategy Comparison**: Side-by-side performance analysis

### Backtesting Outputs
- **Performance Reports**: HTML reports with detailed analytics
- **Data Exports**: CSV files for trade analysis and equity tracking
- **Visualization**: Charts showing regime transitions and performance
- **Risk Analysis**: Drawdown periods and risk-adjusted returns

## Auto-Start System

### Comprehensive Process Management
The auto-start system provides enterprise-grade process management:

**Core Features:**
- **Multi-process Coordination**: Manages trading bot and optional dashboard
- **Health Monitoring**: Continuous process health and resource checks
- **Automatic Recovery**: Intelligent restart with exponential backoff
- **Emergency Controls**: File-based emergency stop and system shutdown
- **Cross-platform Support**: Windows, Linux, and macOS compatibility

**Advanced Monitoring:**
- **Resource Tracking**: CPU, memory, and disk usage monitoring
- **Connection Health**: Exchange connectivity and API status checks
- **Performance Alerts**: Proactive notifications for system issues
- **Log Management**: Automatic log rotation and cleanup

**Safety Features:**
- **Graceful Shutdown**: Clean process termination with position preservation
- **Emergency Stop**: Immediate halt capability via file creation
- **Resource Limits**: Automatic restart on excessive resource usage
- **Failure Tracking**: Intelligent failure analysis and response

## Daily Reporting System

### Automated Performance Analysis
```bash
# Generate and send daily report
python ars3_report_daily.py

# Schedule daily reports (Linux/Mac example)
echo "0 18 * * * cd /path/to/ars3 && python ars3_report_daily.py" | crontab -
```

### Report Features
- **Comprehensive Analytics**: P&L, drawdown, win rates, and trade statistics
- **Visual Charts**: Multi-panel equity curves and performance analysis
- **Risk Assessment**: Drawdown warnings and risk level indicators
- **Multi-channel Delivery**: Telegram messages and email with attachments
- **Historical Tracking**: Performance trends and comparison analysis

### Report Contents
- **Financial Summary**: Daily P&L, equity changes, and return percentages
- **Trading Statistics**: Win rates, trade counts, and average performance
- **Risk Metrics**: Maximum drawdown and risk-adjusted performance
- **Symbol Analysis**: Best performing trading pairs
- **Visual Analytics**: Charts showing equity progression and drawdown periods

## Strategy Details

### RS-75 Range Scalper
**Market Conditions**: Ranging markets with low ADX and moderate volatility

**Entry Logic**:
- Price touches Bollinger Band extremes (upper/lower bands)
- RSI confirms overbought (>70) or oversold (<30) conditions
- Low volatility environment (nATR < 0.45%) for risk control
- Optional volume confirmation (above average volume)
- Higher timeframe trend alignment (optional filter)

**Exit Strategy**:
- **TP1**: Bollinger Band middle line (50% position closure)
- **TP2**: Opposite Bollinger Band (remaining position closure)
- **Stop Loss**: Beyond recent swing levels with ATR buffer
- **Break-even**: Move stop to entry after TP1 achievement

### Trend Following Strategy
**Market Conditions**: Trending markets with high ADX and clear momentum

**Entry Logic**:
- EMA crossover signal (12-period over 26-period)
- Strong trend confirmation (ADX > 25)
- Volume confirmation supporting the move
- Price momentum validation

**Exit Strategy**:
- **TP1**: 3x ATR distance from entry (conservative target)
- **TP2**: 6x ATR distance from entry (extended target)
- **Trailing Stop**: 2x ATR trailing distance after TP1
- **Maximum Hold**: Force exit after 10 days if no signal

## Risk Management Framework

### Multi-Layer Protection System

**Position-Level Controls**:
- Smart stop losses based on market structure (swing levels)
- Risk-based position sizing (1% account risk per trade)
- Take profit scaling with partial position management
- Position timeout protection (forced closure after time limit)

**Account-Level Controls**:
- Maximum daily loss limits with automatic trading halt
- Portfolio drawdown protection (15% maximum drawdown)
- Daily trade count limits to prevent overtrading
- Emergency circuit breakers for extreme market conditions

**System-Level Controls**:
- Connection monitoring with automatic reconnection
- Memory management and resource cleanup
- Process health monitoring with restart capability
- Graceful shutdown procedures with position preservation

### Emergency Features
- **Kill Switch**: Create `EMERGENCY_STOP` file for immediate halt
- **Resource Monitoring**: Automatic shutdown on resource exhaustion
- **Connection Failures**: Automatic halt on exchange disconnection
- **Balance Protection**: Continuous balance validation and alerts

## Safety Guidelines

### Pre-Live Trading Checklist
1. **Extensive Simulation Testing**: Run paper trading for minimum 1-2 weeks
2. **Comprehensive Backtesting**: Validate strategies on 6+ months historical data
3. **Testnet Validation**: Test with real API integration on exchange testnet
4. **Small Position Testing**: Start with minimum position sizes in live mode
5. **System Monitoring**: Ensure all notification systems are working
6. **Emergency Procedures**: Test emergency stop and restart procedures

### Risk Warnings
- **Trading Risk**: Never risk more than you can afford to lose
- **System Testing**: Always test extensively before live deployment
- **Futures Trading**: Adds significant leverage risk - use extreme caution
- **Market Volatility**: Conditions can change rapidly and unpredictably
- **No Guarantees**: No trading strategy guarantees profits

## Performance Monitoring

### Key Metrics Tracked
- **Real-time Equity**: Live balance and P&L tracking
- **Risk Metrics**: Drawdown, volatility, and risk-adjusted returns
- **Strategy Performance**: Win rates and profit factors by regime
- **System Health**: Uptime, connection quality, and resource usage
- **Trading Activity**: Trade frequency, position sizing, and execution quality

### Analytics and Reporting
- **Performance Attribution**: Results by market regime and strategy
- **Risk Analysis**: Drawdown periods and recovery times
- **Efficiency Metrics**: Sharpe ratio and risk-adjusted returns
- **System Reliability**: Uptime statistics and failure analysis

## Troubleshooting Guide

### Common Issues and Solutions

**Bot Won't Start**:
```bash
# Check configuration and dependencies
python integration_test.py

# Verify environment variables
cat .env

# Check log files
tail -f ars3_bot.log
```

**No Trades Generated**:
- Market conditions may not meet strategy criteria
- Check regime detection in logs (`Market Regime: RANGING/TRENDING`)
- Verify sufficient balance for minimum position sizes
- Review strategy parameters in configuration

**Connection Issues**:
- Validate API keys and permissions in exchange account
- Check network connectivity and firewall settings
- Verify exchange supports selected trading symbols
- Test connection manually: `python -c "from ars3_broker_ccxt import Broker; b=Broker(); print(b.test_connection())"`

**Memory or Performance Issues**:
- Bot includes automatic memory management and cleanup
- Check system resources: `python -c "import psutil; print(f'Memory: {psutil.virtual_memory().percent}%')"`
- Restart system if issues persist
- Review log files for resource usage warnings

### Log Analysis
Monitor these critical log messages:
- `Market Regime: RANGING/TRENDING/COMPRESSION` - Strategy selection
- `Position Exit (STOP_LOSS/TAKE_PROFIT_X)` - Trade exits
- `NEW POSITION OPENED` - Trade entries
- `Emergency condition detected` - System alerts
- `Broker connection test failed` - Connection issues

## Advanced Configuration

### Strategy Parameter Tuning
Optimize performance by adjusting parameters in `ars3_config.yaml`:

**Range Strategy (RS-75)**:
- `bb_period`: Bollinger Band calculation period (default: 20)
- `bb_std`: Bollinger Band standard deviation (default: 2.5)
- `rsi_period`: RSI calculation period (default: 7)
- `rsi_oversold`/`rsi_overbought`: RSI signal thresholds (30/70)

**Trend Strategy**:
- `fast_ema_period`/`slow_ema_period`: EMA crossover periods (12/26)
- `adx_threshold`: Minimum ADX for trend confirmation (default: 25)
- `atr_sl_multiplier`: Stop loss distance multiplier (default: 2.5)

**Regime Detection**:
- `trend_on`/`trend_off`: Trend regime thresholds (0.60/0.45)
- `range_on`/`range_off`: Range regime thresholds (0.60/0.45)
- `cooldown_bars`: Stability control between regime changes (default: 3)

### Exchange Integration
- **Multi-exchange Support**: CCXT framework supports 100+ exchanges
- **Market Types**: Spot and futures trading capabilities
- **Order Types**: Market orders with planned limit order support
- **Commission Handling**: Configurable commission rates and realistic modeling

### Notification Customization
- **Telegram Filtering**: Selective message types and frequency control
- **Email Integration**: HTML reports with chart attachments
- **Alert Thresholds**: Custom conditions for various notification types
- **Report Scheduling**: Flexible timing for daily/weekly reports

## File Structure Reference

```
ARS-3-Professional-Trading-System/
├── Core Trading Engine
│   ├── ars3_live.py                    # Main trading engine with regime detection
│   ├── ars3_broker_ccxt.py             # Exchange integration and order execution
│   ├── ars3_config.yaml                # Comprehensive system configuration
│   └── .env                            # Environment variables (API keys)
│
├── Trading Strategies
│   ├── ars3_strategy_rs75.py           # RS-75 Range scalping strategy
│   ├── ars3_strategy_trend.py          # Trend following strategy
│   └── ars3_strategy_utils.py          # Regime detection and utilities
│
├── System Management
│   ├── ars3_autostart.py               # Enhanced auto-restart with monitoring
│   ├── ars3_backtest.py                # Professional backtesting engine
│   ├── ars3_report_daily.py            # Daily performance reporting
│   └── integration_test.py             # Complete system testing
│
├── Documentation & Setup
│   ├── README.md                       # This comprehensive guide
│   ├── STRATEGY.md                     # Detailed strategy documentation
│   └── requirements.txt                # Python dependencies
│
└── Data Files (Auto-Generated)
    ├── equity_live.csv                 # Real-time equity tracking
    ├── trades_live.csv                 # Complete trade history
    ├── position.json                   # Current position state
    ├── ars3_bot.log                    # System logs (rotated)
    ├── ars3_autostart.log              # Auto-restart system logs
    ├── backtest_*.csv                  # Backtesting results
    ├── backtest_*.png                  # Performance charts
    └── equity_report.png               # Daily report charts
```

## Usage Examples

### Basic Operations
```bash
# Complete system startup with monitoring
python ars3_autostart.py

# Manual trading bot (for development/testing)
python ars3_live.py

# Generate daily performance report
python ars3_report_daily.py

# Run system integration tests
python integration_test.py
```

### Backtesting Examples
```bash
# Basic regime-aware backtest
python ars3_backtest.py --symbol BTC/USDT --strategy auto

# Compare strategies on same data
python ars3_backtest.py --symbol ETH/USDT --strategy range
python ars3_backtest.py --symbol ETH/USDT --strategy trend
python ars3_backtest.py --symbol ETH/USDT --strategy auto

# Advanced backtesting with custom settings
python ars3_backtest.py \
    --data-source broker \
    --symbol SOL/USDT \
    --timeframe 4h \
    --balance 25000 \
    --strategy auto \
    --config custom_config.yaml
```

### Emergency Controls
```bash
# Create emergency stop (halts all trading)
touch EMERGENCY_STOP

# Remove emergency stop (resumes trading)
rm EMERGENCY_STOP

# Check system status
tail -f ars3_autostart.log

# Monitor trading activity
tail -f ars3_bot.log | grep -E "(Position|Trade|Signal)"
```

## Development and Extension

### Modular Architecture
The system supports easy customization and extension:

**Adding New Strategies**:
1. Create strategy module following existing patterns
2. Implement `generate_signals()` function
3. Add strategy configuration to `ars3_config.yaml`
4. Update regime detection logic if needed

**Custom Indicators**:
1. Add indicator functions to `ars3_strategy_utils.py`
2. Update strategy modules to use new indicators
3. Add configuration parameters as needed

**Exchange Integration**:
1. CCXT framework supports 100+ exchanges
2. Update broker configuration in `ars3_broker_ccxt.py`
3. Test thoroughly with testnet before live deployment

### Testing and Validation
- **Unit Testing**: Individual component testing capability
- **Integration Testing**: Complete system validation
- **Backtesting**: Historical performance validation
- **Paper Trading**: Real-time testing with simulated funds
- **Testnet Trading**: Live API testing with fake funds

## System Requirements

### Minimum Requirements
- **Operating System**: Windows 10+, Ubuntu 18+, macOS 10.14+
- **Python**: 3.8+ (recommended 3.9+)
- **RAM**: 2GB minimum, 4GB recommended
- **Storage**: 2GB free space for logs and data
- **Network**: Stable broadband internet connection

### Recommended Setup
- **CPU**: Multi-core processor for better performance
- **RAM**: 8GB for optimal performance with backtesting
- **Storage**: SSD for faster data access and logging
- **Network**: Redundant internet connection for reliability
- **Monitoring**: Dedicated monitoring server for large deployments

## Performance Optimization

### System Performance
- **Memory Management**: Automatic cleanup and garbage collection
- **CPU Optimization**: Efficient indicator calculations and data processing
- **Network Efficiency**: Connection pooling and rate limit management
- **Storage Optimization**: Log rotation and data archival

### Trading Performance
- **Execution Speed**: Optimized order placement and market data processing
- **Slippage Reduction**: Smart order timing and market impact analysis
- **Risk Control**: Real-time position monitoring and automated risk management
- **Strategy Efficiency**: Optimized signal generation and regime detection

## Support and Maintenance

### Getting Help
1. **Documentation**: Start with this README and STRATEGY.md
2. **Logs Analysis**: Check `ars3_bot.log` and `ars3_autostart.log` for errors
3. **Integration Tests**: Run `python integration_test.py` for diagnostics
4. **Configuration**: Verify all settings in `ars3_config.yaml` and `.env`

### Best Practices
- **Testing Protocol**: Always test extensively before live deployment
- **Risk Management**: Start with conservative position sizes and risk limits
- **Monitoring**: Establish proper monitoring and alerting procedures
- **Backup Strategy**: Maintain regular backups of configuration and data
- **Update Procedure**: Keep system updated with latest improvements

### Maintenance Tasks
- **Log Rotation**: Automatic log management with configurable retention
- **Data Cleanup**: Periodic cleanup of old trade and equity data
- **Performance Review**: Regular analysis of strategy performance and system health
- **Configuration Updates**: Periodic optimization of strategy parameters

## Security Considerations

### API Key Security
- **Environment Variables**: Store sensitive data in `.env` file only
- **File Permissions**: Restrict access to configuration files
- **Key Rotation**: Regularly rotate API keys and passwords
- **Access Control**: Limit API key permissions to minimum required

### System Security
- **Network Security**: Use secure connections and consider VPN for sensitive deployments
- **Process Isolation**: Run components with appropriate user permissions
- **Monitoring**: Implement intrusion detection and anomaly monitoring
- **Backup Security**: Encrypt backups and store securely

### Trading Security
- **Position Limits**: Implement strict position and risk limits
- **Emergency Controls**: Maintain multiple emergency stop mechanisms
- **Audit Trail**: Complete logging of all trading decisions and actions
- **Recovery Procedures**: Well-defined procedures for various failure scenarios

## Legal and Compliance

### Regulatory Considerations
- **Jurisdictional Compliance**: Ensure compliance with local trading regulations
- **Tax Reporting**: Maintain accurate records for tax reporting requirements
- **Risk Disclosure**: Understand and accept trading risks
- **Professional Advice**: Consider consulting with financial professionals

### Disclaimers
- **No Investment Advice**: This system is for educational and research purposes
- **Risk Warning**: Trading involves substantial risk of loss
- **No Guarantees**: Past performance does not guarantee future results
- **User Responsibility**: Users are responsible for their trading decisions

## Conclusion

The ARS-3 Professional Trading Bot System represents a comprehensive solution for algorithmic cryptocurrency trading. With its advanced regime detection, multi-strategy approach, and enterprise-grade monitoring, it provides a robust foundation for systematic trading operations.

The system prioritizes safety through multiple risk management layers, extensive testing capabilities, and comprehensive monitoring. The modular architecture supports customization and extension while maintaining reliability and performance.

Remember that successful algorithmic trading requires thorough testing, proper risk management, and continuous monitoring. Always start with simulation mode, progress through testnet trading, and begin live trading with conservative position sizes.

For the latest updates, additional documentation, and community support, check the project repository and documentation resources.

---

**Version**: ARS-3 v1.1 Professional  
**Last Updated**: September 2025  
**License**: Please check repository for license information  
**Support**: Check logs, documentation, and integration tests for troubleshooting# ARS-3 Professional Trading Bot