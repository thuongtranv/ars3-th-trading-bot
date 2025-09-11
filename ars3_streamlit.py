import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import time
import os
import logging
import json
import yaml
import numpy as np

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="ARS-3 Advanced Dashboard", 
    layout="wide",
    initial_sidebar_state="expanded",
    page_icon="📈"
)

# Enhanced CSS for better styling
st.markdown("""
<style>
.metric-card {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    padding: 1rem;
    border-radius: 10px;
    color: white;
    margin: 0.5rem 0;
    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
}

.status-card {
    padding: 1rem;
    border-radius: 8px;
    margin: 0.5rem 0;
    border-left: 4px solid;
}

.status-success {
    background-color: #d4edda;
    border-left-color: #28a745;
    color: #155724;
}

.status-warning {
    background-color: #fff3cd;
    border-left-color: #ffc107;
    color: #856404;
}

.status-error {
    background-color: #f8d7da;
    border-left-color: #dc3545;
    color: #721c24;
}

.status-info {
    background-color: #d1ecf1;
    border-left-color: #17a2b8;
    color: #0c5460;
}

.regime-indicator {
    padding: 0.5rem 1rem;
    border-radius: 20px;
    font-weight: bold;
    text-align: center;
    margin: 0.25rem;
    display: inline-block;
}

.regime-trending {
    background-color: #28a745;
    color: white;
}

.regime-ranging {
    background-color: #ffc107;
    color: #212529;
}

.regime-compression {
    background-color: #6c757d;
    color: white;
}

.regime-undefined {
    background-color: #dc3545;
    color: white;
}

.trade-profit {
    color: #28a745;
    font-weight: bold;
}

.trade-loss {
    color: #dc3545;
    font-weight: bold;
}

.position-long {
    background-color: rgba(40, 167, 69, 0.1);
    border: 1px solid #28a745;
}

.position-short {
    background-color: rgba(220, 53, 69, 0.1);
    border: 1px solid #dc3545;
}

.stAlert > div {
    padding: 0.5rem;
}

/* Custom metrics styling */
div[data-testid="metric-container"] {
    background-color: rgba(28, 131, 225, 0.1);
    border: 1px solid rgba(28, 131, 225, 0.1);
    padding: 5% 5% 5% 10%;
    border-radius: 5px;
    color: rgb(30, 103, 119);
    overflow-wrap: break-word;
}

div[data-testid="metric-container"] > label[data-testid="metric-label"] > div {
    overflow-wrap: break-word;
    white-space: break-spaces;
    color: red;
}

.sidebar .sidebar-content {
    background-color: #f8f9fa;
}

/* Emergency button styling */
.emergency-button {
    background-color: #dc3545 !important;
    color: white !important;
    border: none;
    padding: 0.5rem 1rem;
    border-radius: 5px;
    cursor: pointer;
    font-weight: bold;
}

.emergency-button:hover {
    background-color: #c82333 !important;
}
</style>
""", unsafe_allow_html=True)

# Title with status indicator
col1, col2, col3 = st.columns([3, 1, 1])
with col1:
    st.title("ARS-3 Multi-Strategy Trading Dashboard")
with col2:
    # Bot status indicator
    if os.path.exists("ars3_bot.log"):
        st.markdown('<div class="status-card status-success">🟢 Bot Active</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="status-card status-error">🔴 Bot Offline</div>', unsafe_allow_html=True)
with col3:
    # Emergency stop check
    emergency_file = "EMERGENCY_STOP"
    if os.path.exists(emergency_file):
        st.markdown('<div class="status-card status-error">🚨 EMERGENCY STOP</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="status-card status-success">✅ Normal Operation</div>', unsafe_allow_html=True)

st.markdown("---")

# Enhanced sidebar controls
st.sidebar.header("📊 Dashboard Controls")
st.sidebar.markdown("---")

# Display options
st.sidebar.subheader("Display Options")
show_trades = st.sidebar.checkbox("Show recent trades", True)
show_equity = st.sidebar.checkbox("Show equity curve", True)
show_metrics = st.sidebar.checkbox("Show performance metrics", True)
show_position = st.sidebar.checkbox("Show current position", True)
show_regime = st.sidebar.checkbox("Show market regime", True)
show_logs = st.sidebar.checkbox("Show system logs", False)

# Data controls
st.sidebar.subheader("Data Controls")
max_trades_display = st.sidebar.slider("Max trades to display", 5, 100, 20)
max_log_lines = st.sidebar.slider("Max log lines", 10, 100, 50)

# Refresh controls
st.sidebar.subheader("Refresh Settings")
auto_refresh = st.sidebar.checkbox("Auto refresh", False)
refresh_interval = st.sidebar.selectbox(
    "Refresh interval",
    [5, 10, 30, 60, 300],
    index=2,
    format_func=lambda x: f"{x}s" if x < 60 else f"{x//60}m"
)

# Advanced options
st.sidebar.subheader("Advanced Options")
show_debug_info = st.sidebar.checkbox("Show debug info", False)
export_data = st.sidebar.checkbox("Enable data export", False)

st.sidebar.markdown("---")

# Emergency controls
st.sidebar.subheader("🚨 Emergency Controls")
if st.sidebar.button("Create Emergency Stop", type="secondary", help="Creates EMERGENCY_STOP file to halt trading"):
    try:
        with open("EMERGENCY_STOP", "w") as f:
            f.write(f"Emergency stop created from dashboard at {datetime.now()}")
        st.sidebar.success("Emergency stop file created!")
        time.sleep(1)
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Failed to create emergency stop: {e}")

if st.sidebar.button("Remove Emergency Stop", help="Removes EMERGENCY_STOP file"):
    try:
        if os.path.exists("EMERGENCY_STOP"):
            os.remove("EMERGENCY_STOP")
            st.sidebar.success("Emergency stop removed!")
            time.sleep(1)
            st.rerun()
        else:
            st.sidebar.info("No emergency stop file found")
    except Exception as e:
        st.sidebar.error(f"Failed to remove emergency stop: {e}")

# Auto refresh functionality
if auto_refresh:
    time.sleep(refresh_interval)
    st.rerun()

# Enhanced data loading with validation and caching
@st.cache_data(ttl=10, show_spinner=False)
def load_comprehensive_data():
    """Load all data sources with enhanced error handling and validation."""
    data = {
        'trades': None,
        'equity': None, 
        'position': None,
        'config': None,
        'logs': [],
        'system_health': {},
        'files_status': {}
    }
    
    # Load configuration
    try:
        config_file = "ars3_config.yaml"
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                data['config'] = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Error loading config: {e}")
    
    # Load trades data
    try:
        trades_file = "trades_live.csv"
        if os.path.exists(trades_file) and os.path.getsize(trades_file) > 0:
            df = pd.read_csv(trades_file)
            if not df.empty and 'timestamp' in df.columns:
                df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
                df = df.dropna(subset=['timestamp']).sort_values('timestamp')
                if not df.empty:
                    data['trades'] = df
    except Exception as e:
        logger.error(f"Error loading trades: {e}")
    
    # Load equity data
    try:
        equity_file = "equity_live.csv"
        if os.path.exists(equity_file) and os.path.getsize(equity_file) > 0:
            df = pd.read_csv(equity_file)
            if not df.empty and 'timestamp' in df.columns:
                df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
                df = df.dropna(subset=['timestamp', 'equity']).sort_values('timestamp')
                if not df.empty:
                    data['equity'] = df
    except Exception as e:
        logger.error(f"Error loading equity: {e}")
    
    # Load position data
    try:
        position_file = "position.json"
        if os.path.exists(position_file) and os.path.getsize(position_file) > 0:
            with open(position_file, 'r') as f:
                pos = json.load(f)
                if pos and isinstance(pos, dict):
                    data['position'] = pos
    except Exception as e:
        logger.error(f"Error loading position: {e}")
    
    # Load system logs
    try:
        log_file = "ars3_bot.log"
        if os.path.exists(log_file):
            with open(log_file, 'r') as f:
                lines = f.readlines()
                data['logs'] = lines[-100:]  # Last 100 lines
    except Exception as e:
        logger.error(f"Error loading logs: {e}")
    
    # File status check
    files_to_check = [
        "trades_live.csv", "equity_live.csv", "position.json", 
        "ars3_bot.log", "ars3_config.yaml", "EMERGENCY_STOP"
    ]
    
    for filename in files_to_check:
        try:
            if os.path.exists(filename):
                stat = os.stat(filename)
                data['files_status'][filename] = {
                    'exists': True,
                    'size': stat.st_size,
                    'modified': datetime.fromtimestamp(stat.st_mtime),
                    'age_seconds': time.time() - stat.st_mtime
                }
            else:
                data['files_status'][filename] = {'exists': False}
        except Exception as e:
            data['files_status'][filename] = {'exists': False, 'error': str(e)}
    
    return data

def safe_get_value(data, key, default="N/A"):
    """Safely get value from data structure."""
    try:
        if isinstance(data, dict) and key in data:
            return data[key]
        elif hasattr(data, key):
            return getattr(data, key)
        else:
            return default
    except Exception:
        return default

def format_number(value, decimals=2, prefix="", suffix=""):
    """Format number with error handling."""
    try:
        if pd.isna(value) or value is None:
            return "N/A"
        if isinstance(value, (int, float)):
            return f"{prefix}{value:.{decimals}f}{suffix}"
        return str(value)
    except (TypeError, ValueError):
        return "N/A"

def calculate_comprehensive_stats(trades_data, equity_data):
    """Calculate comprehensive trading statistics."""
    stats = {
        'total_trades': 0,
        'total_pnl': 0,
        'winning_trades': 0,
        'losing_trades': 0,
        'win_rate': 0,
        'avg_win': 0,
        'avg_loss': 0,
        'profit_factor': 0,
        'max_consecutive_wins': 0,
        'max_consecutive_losses': 0,
        'sharpe_ratio': 0,
        'total_return': 0,
        'max_drawdown': 0,
        'recovery_factor': 0
    }
    
    try:
        # Trade statistics
        if trades_data is not None and not trades_data.empty:
            # Filter for completed trades with PnL
            pnl_trades = trades_data[trades_data['pnl'].notna()] if 'pnl' in trades_data.columns else pd.DataFrame()
            
            if not pnl_trades.empty:
                stats['total_trades'] = len(pnl_trades)
                stats['total_pnl'] = pnl_trades['pnl'].sum()
                
                wins = pnl_trades[pnl_trades['pnl'] > 0]
                losses = pnl_trades[pnl_trades['pnl'] < 0]
                
                stats['winning_trades'] = len(wins)
                stats['losing_trades'] = len(losses)
                stats['win_rate'] = (stats['winning_trades'] / stats['total_trades'] * 100) if stats['total_trades'] > 0 else 0
                
                stats['avg_win'] = wins['pnl'].mean() if not wins.empty else 0
                stats['avg_loss'] = losses['pnl'].mean() if not losses.empty else 0
                
                # Profit factor
                gross_profit = wins['pnl'].sum() if not wins.empty else 0
                gross_loss = abs(losses['pnl'].sum()) if not losses.empty else 0.01
                stats['profit_factor'] = gross_profit / gross_loss if gross_loss > 0 else 0
                
                # Consecutive wins/losses
                pnl_series = pnl_trades['pnl'].values
                consecutive_wins = consecutive_losses = max_wins = max_losses = 0
                
                for pnl in pnl_series:
                    if pnl > 0:
                        consecutive_wins += 1
                        consecutive_losses = 0
                        max_wins = max(max_wins, consecutive_wins)
                    else:
                        consecutive_losses += 1
                        consecutive_wins = 0
                        max_losses = max(max_losses, consecutive_losses)
                
                stats['max_consecutive_wins'] = max_wins
                stats['max_consecutive_losses'] = max_losses
        
        # Equity curve statistics
        if equity_data is not None and not equity_data.empty and len(equity_data) > 1:
            initial_equity = equity_data['equity'].iloc[0]
            final_equity = equity_data['equity'].iloc[-1]
            
            stats['total_return'] = ((final_equity - initial_equity) / initial_equity) * 100
            
            # Drawdown calculation
            peak = equity_data['equity'].expanding().max()
            drawdown = (peak - equity_data['equity']) / peak * 100
            stats['max_drawdown'] = drawdown.max()
            
            # Recovery factor
            if stats['max_drawdown'] > 0:
                stats['recovery_factor'] = stats['total_return'] / stats['max_drawdown']
            
            # Simple Sharpe ratio (assuming daily returns)
            returns = equity_data['equity'].pct_change().dropna()
            if not returns.empty and returns.std() > 0:
                stats['sharpe_ratio'] = (returns.mean() / returns.std()) * np.sqrt(365)  # Annualized
                
    except Exception as e:
        logger.error(f"Error calculating stats: {e}")
    
    return stats

def get_regime_display(regime_info):
    """Format market regime for display."""
    if isinstance(regime_info, dict):
        regime = regime_info.get('regime', 'undefined')
    elif isinstance(regime_info, str):
        regime = regime_info
    else:
        regime = 'undefined'
    
    regime_classes = {
        'trending': 'regime-trending',
        'ranging': 'regime-ranging', 
        'compression': 'regime-compression',
        'undefined': 'regime-undefined'
    }
    
    class_name = regime_classes.get(regime, 'regime-undefined')
    return f'<div class="regime-indicator {class_name}">{regime.upper()}</div>'

# Load all data
with st.spinner("Loading dashboard data..."):
    all_data = load_comprehensive_data()

trades = all_data['trades']
equity = all_data['equity']
position = all_data['position']
config = all_data['config']
logs = all_data['logs']
files_status = all_data['files_status']

# System Status Overview
st.subheader("🔍 System Status Overview")

# Create status grid
status_cols = st.columns(6)

with status_cols[0]:
    if files_status.get('trades_live.csv', {}).get('exists', False):
        age = files_status['trades_live.csv'].get('age_seconds', 0)
        if age < 300:  # 5 minutes
            st.markdown('<div class="status-card status-success">📊 Trades: Fresh</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="status-card status-warning">📊 Trades: Stale</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="status-card status-error">📊 Trades: Missing</div>', unsafe_allow_html=True)

with status_cols[1]:
    if files_status.get('equity_live.csv', {}).get('exists', False):
        st.markdown('<div class="status-card status-success">💰 Equity: OK</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="status-card status-error">💰 Equity: Missing</div>', unsafe_allow_html=True)

with status_cols[2]:
    if position and isinstance(position, dict) and position:
        pos_type = position.get('type', 'UNKNOWN')
        color = 'success' if pos_type == 'BUY' else 'warning'
        st.markdown(f'<div class="status-card status-{color}">📍 Position: {pos_type}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="status-card status-info">📍 Position: None</div>', unsafe_allow_html=True)

with status_cols[3]:
    if files_status.get('ars3_bot.log', {}).get('exists', False):
        age = files_status['ars3_bot.log'].get('age_seconds', 0)
        if age < 120:  # 2 minutes
            st.markdown('<div class="status-card status-success">📝 Logs: Active</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="status-card status-warning">📝 Logs: Inactive</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="status-card status-error">📝 Logs: Missing</div>', unsafe_allow_html=True)

with status_cols[4]:
    if config and isinstance(config, dict):
        mode = "SIM" if config.get('simulation_mode', True) else "LIVE"
        color = 'info' if mode == 'SIM' else 'warning'
        st.markdown(f'<div class="status-card status-{color}">⚙️ Mode: {mode}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="status-card status-error">⚙️ Config: Missing</div>', unsafe_allow_html=True)

with status_cols[5]:
    st.markdown(f'<div class="status-card status-info">🕒 Updated: {datetime.now().strftime("%H:%M:%S")}</div>', unsafe_allow_html=True)

# Market Regime Display
if show_regime and equity is not None:
    st.subheader("🎯 Market Regime Analysis")
    
    regime_cols = st.columns(4)
    
    # Try to get regime info from latest equity data
    current_regime = "undefined"
    if 'regime' in equity.columns:
        current_regime = equity['regime'].iloc[-1] if not equity.empty else "undefined"
    
    with regime_cols[0]:
        st.markdown("**Current Regime:**", unsafe_allow_html=True)
        st.markdown(get_regime_display(current_regime), unsafe_allow_html=True)
    
    with regime_cols[1]:
        # Strategy selection based on regime
        if current_regime == "trending":
            st.markdown("**Active Strategy:** Trend Following", unsafe_allow_html=True)
        elif current_regime == "ranging":
            st.markdown("**Active Strategy:** Range Scalping (RS-75)", unsafe_allow_html=True)
        else:
            st.markdown("**Active Strategy:** Market Analysis", unsafe_allow_html=True)
    
    with regime_cols[2]:
        # Regime confidence or additional info
        if trades is not None and not trades.empty:
            recent_trades = len(trades[trades['timestamp'] > datetime.now() - timedelta(hours=24)])
            st.metric("24h Trades", recent_trades)
    
    with regime_cols[3]:
        # Next regime check
        if config:
            timeframe = config.get('timeframe', '1h')
            st.metric("Timeframe", timeframe)

# Enhanced Current Position Display
if show_position and position:
    st.subheader("📊 Current Position Details")
    
    try:
        pos_type = safe_get_value(position, 'type')
        symbol = safe_get_value(position, 'symbol')
        size = float(safe_get_value(position, 'size', 0))
        entry_price = float(safe_get_value(position, 'entry_price', 0))
        
        # Position header
        col1, col2 = st.columns([3, 1])
        with col1:
            position_class = "position-long" if pos_type == "BUY" else "position-short"
            st.markdown(f'<div class="status-card {position_class}"><h4>{pos_type} {symbol} - Size: {size:.6f}</h4></div>', unsafe_allow_html=True)
        
        # Position metrics
        pos_cols = st.columns(6)
        
        with pos_cols[0]:
            st.metric("Entry Price", format_number(entry_price, 4))
        
        with pos_cols[1]:
            stop_loss = float(safe_get_value(position, 'stop_loss', 0))
            st.metric("Stop Loss", format_number(stop_loss, 4))
        
        with pos_cols[2]:
            tp1 = float(safe_get_value(position, 'tp1', 0))
            st.metric("Take Profit 1", format_number(tp1, 4))
        
        with pos_cols[3]:
            tp2 = float(safe_get_value(position, 'tp2', 0))
            st.metric("Take Profit 2", format_number(tp2, 4))
        
        with pos_cols[4]:
            has_hit_tp1 = safe_get_value(position, 'has_hit_tp1', False)
            st.metric("TP1 Status", "✅ Hit" if has_hit_tp1 else "⏳ Pending")
        
        with pos_cols[5]:
            strategy = safe_get_value(position, 'strategy', 'Unknown')
            st.metric("Strategy", strategy.title())
        
        # Position timing and risk
        pos_detail_cols = st.columns(4)
        
        with pos_detail_cols[0]:
            if 'timestamp' in position:
                try:
                    pos_time = pd.to_datetime(position['timestamp'])
                    duration = datetime.now() - pos_time
                    hours = int(duration.total_seconds() / 3600)
                    minutes = int((duration.total_seconds() % 3600) / 60)
                    st.metric("Duration", f"{hours}h {minutes}m")
                except:
                    st.metric("Duration", "N/A")
        
        with pos_detail_cols[1]:
            # Risk calculation
            if entry_price > 0 and stop_loss > 0:
                risk_pct = abs((entry_price - stop_loss) / entry_price) * 100
                st.metric("Risk %", format_number(risk_pct, 2, suffix="%"))
        
        with pos_detail_cols[2]:
            # Potential reward
            if entry_price > 0 and tp1 > 0:
                reward_pct = abs((tp1 - entry_price) / entry_price) * 100
                st.metric("Reward % (TP1)", format_number(reward_pct, 2, suffix="%"))
        
        with pos_detail_cols[3]:
            # R:R ratio
            if entry_price > 0 and stop_loss > 0 and tp1 > 0:
                risk_amount = abs(entry_price - stop_loss)
                reward_amount = abs(tp1 - entry_price)
                rr_ratio = reward_amount / risk_amount if risk_amount > 0 else 0
                st.metric("R:R Ratio", format_number(rr_ratio, 2))
        
    except Exception as e:
        st.error(f"Error displaying position: {e}")

# Enhanced Performance Metrics
if show_metrics:
    st.subheader("📈 Performance Analytics")
    
    # Calculate comprehensive statistics
    stats = calculate_comprehensive_stats(trades, equity)
    
    # Primary metrics row
    metrics_cols = st.columns(5)
    
    with metrics_cols[0]:
        return_color = "inverse" if stats['total_return'] >= 0 else "off"
        st.metric(
            "Total Return", 
            format_number(stats['total_return'], 2, suffix="%"),
            delta=format_number(stats['total_return'], 2, suffix="%")
        )
    
    with metrics_cols[1]:
        if equity is not None and not equity.empty:
            current_balance = equity['equity'].iloc[-1]
            initial_balance = equity['equity'].iloc[0]
            balance_change = current_balance - initial_balance
            st.metric(
                "Current Balance",
                format_number(current_balance, 2, prefix="$"),
                delta=format_number(balance_change, 2, prefix="$")
            )
        else:
            st.metric("Current Balance", "N/A")
    
    with metrics_cols[2]:
        st.metric("Total Trades", stats['total_trades'])
    
    with metrics_cols[3]:
        st.metric("Win Rate", format_number(stats['win_rate'], 1, suffix="%"))
    
    with metrics_cols[4]:
        st.metric("Max Drawdown", format_number(stats['max_drawdown'], 2, suffix="%"))
    
    # Secondary metrics row
    metrics_cols2 = st.columns(5)
    
    with metrics_cols2[0]:
        st.metric("Total PnL", format_number(stats['total_pnl'], 2, prefix="$"))
    
    with metrics_cols2[1]:
        st.metric("Profit Factor", format_number(stats['profit_factor'], 2))
    
    with metrics_cols2[2]:
        st.metric("Avg Win", format_number(stats['avg_win'], 2, prefix="$"))
    
    with metrics_cols2[3]:
        st.metric("Avg Loss", format_number(stats['avg_loss'], 2, prefix="$"))
    
    with metrics_cols2[4]:
        st.metric("Sharpe Ratio", format_number(stats['sharpe_ratio'], 2))

# Enhanced Equity Curve with Multiple Views
if show_equity and equity is not None and not equity.empty:
    st.subheader("💹 Equity & Performance Analysis")
    
    # Equity curve tabs
    equity_tab1, equity_tab2, equity_tab3 = st.tabs(["Equity Curve", "Drawdown Analysis", "Returns Distribution"])
    
    with equity_tab1:
        # Main equity chart with enhancements
        fig = make_subplots(
            rows=2, cols=1,
            subplot_titles=("Equity Curve", "Daily PnL"),
            vertical_spacing=0.1,
            row_heights=[0.7, 0.3]
        )
        
        # Equity line
        fig.add_trace(
            go.Scatter(
                x=equity["timestamp"], 
                y=equity["equity"],
                mode='lines',
                name='Equity',
                line=dict(color='#1f77b4', width=2),
                hovertemplate='<b>Time:</b> %{x}<br><b>Equity:</b> $%{y:.2f}<extra></extra>'
            ),
            row=1, col=1
        )
        
        # Initial balance line
        initial_balance = equity['equity'].iloc[0]
        fig.add_hline(
            y=initial_balance, 
            line_dash="dash", 
            line_color="red",
            annotation_text="Initial Balance",
            row=1, col=1
        )
        
        # Daily PnL if available
        if 'daily_pnl' in equity.columns:
            colors = ['green' if x >= 0 else 'red' for x in equity['daily_pnl']]
            fig.add_trace(
                go.Bar(
                    x=equity["timestamp"],
                    y=equity["daily_pnl"],
                    name='Daily PnL',
                    marker_color=colors,
                    hovertemplate='<b>Date:</b> %{x}<br><b>Daily PnL:</b> $%{y:.2f}<extra></extra>'
                ),
                row=2, col=1
            )
        
        fig.update_layout(
            title="Equity Performance Over Time",
            showlegend=True,
            height=600,
            template="plotly_white"
        )
        
        fig.update_xaxes(title_text="Time", row=2, col=1)
        fig.update_yaxes(title_text="Equity (USDT)", row=1, col=1)
        fig.update_yaxes(title_text="Daily PnL ($)", row=2, col=1)
        
        st.plotly_chart(fig, width='stretch')
    
    with equity_tab2:
        # Drawdown analysis
        if len(equity) > 1:
            peak = equity['equity'].expanding().max()
            drawdown = (peak - equity['equity']) / peak * 100
            
            # Drawdown chart
            fig_dd = go.Figure()
            
            fig_dd.add_trace(go.Scatter(
                x=equity["timestamp"],
                y=-drawdown,
                mode='lines',
                fill='tozeroy',
                fillcolor='rgba(255, 0, 0, 0.3)',
                line=dict(color='red', width=2),
                name='Drawdown %',
                hovertemplate='<b>Time:</b> %{x}<br><b>Drawdown:</b> %{y:.2f}%<extra></extra>'
            ))
            
            fig_dd.update_layout(
                title="Drawdown Analysis",
                xaxis_title="Time", 
                yaxis_title="Drawdown (%)",
                height=400,
                template="plotly_white"
            )
            
            st.plotly_chart(fig_dd, width='stretch')
            
            # Drawdown statistics
            dd_cols = st.columns(4)
            with dd_cols[0]:
                st.metric("Max Drawdown", format_number(drawdown.max(), 2, suffix="%"))
            with dd_cols[1]:
                # Average drawdown
                avg_dd = drawdown[drawdown > 0].mean()
                st.metric("Avg Drawdown", format_number(avg_dd, 2, suffix="%"))
            with dd_cols[2]:
                # Current drawdown
                current_dd = drawdown.iloc[-1]
                st.metric("Current Drawdown", format_number(current_dd, 2, suffix="%"))
            with dd_cols[3]:
                # Time in drawdown
                in_dd_pct = len(drawdown[drawdown > 0.01]) / len(drawdown) * 100
                st.metric("Time in Drawdown", format_number(in_dd_pct, 1, suffix="%"))
    
    with equity_tab3:
        # Returns distribution
        if len(equity) > 1:
            returns = equity['equity'].pct_change().dropna() * 100  # Convert to percentage
            
            # Returns histogram
            fig_hist = go.Figure()
            
            fig_hist.add_trace(go.Histogram(
                x=returns,
                nbinsx=30,
                name='Returns Distribution',
                marker_color='lightblue',
                opacity=0.7
            ))
            
            fig_hist.update_layout(
                title="Returns Distribution",
                xaxis_title="Returns (%)",
                yaxis_title="Frequency",
                height=400,
                template="plotly_white"
            )
            
            st.plotly_chart(fig_hist, width='stretch')
            
            # Returns statistics
            ret_cols = st.columns(5)
            with ret_cols[0]:
                st.metric("Mean Return", format_number(returns.mean(), 3, suffix="%"))
            with ret_cols[1]:
                st.metric("Std Dev", format_number(returns.std(), 3, suffix="%"))
            with ret_cols[2]:
                st.metric("Skewness", format_number(returns.skew(), 3))
            with ret_cols[3]:
                st.metric("Best Day", format_number(returns.max(), 2, suffix="%"))
            with ret_cols[4]:
                st.metric("Worst Day", format_number(returns.min(), 2, suffix="%"))

# Enhanced Recent Trades with Advanced Analysis
if show_trades and trades is not None and not trades.empty:
    st.subheader("📋 Trading Activity Analysis")
    
    trades_tab1, trades_tab2, trades_tab3 = st.tabs(["Recent Trades", "Trade Analysis", "Performance by Strategy"])
    
    with trades_tab1:
        # Recent trades table with better formatting
        recent_trades = trades.tail(max_trades_display).copy()
        
        if not recent_trades.empty:
            # Prepare enhanced display data
            display_data = []
            
            for _, trade in recent_trades.iterrows():
                pnl_value = safe_get_value(trade, 'pnl', 0)
                pnl_class = "trade-profit" if pd.notna(pnl_value) and pnl_value > 0 else "trade-loss" if pd.notna(pnl_value) and pnl_value < 0 else ""
                
                row = {
                    'Time': trade['timestamp'].strftime('%m-%d %H:%M') if pd.notna(trade['timestamp']) else 'N/A',
                    'Symbol': safe_get_value(trade, 'symbol'),
                    'Type': safe_get_value(trade, 'type'),
                    'Price': format_number(safe_get_value(trade, 'price', 0), 4),
                    'Size': format_number(safe_get_value(trade, 'size', 0), 6),
                }
                
                # Add PnL with formatting
                if pd.notna(pnl_value) and pnl_value != 0:
                    row['PnL'] = format_number(pnl_value, 2, prefix="$")
                else:
                    row['PnL'] = "-"
                
                # Add strategy if available
                if 'strategy' in trade and pd.notna(trade['strategy']):
                    row['Strategy'] = str(trade['strategy']).title()
                
                # Add reason (truncated)
                if 'reason' in trade and pd.notna(trade['reason']):
                    reason = str(trade['reason'])
                    row['Reason'] = reason[:40] + "..." if len(reason) > 40 else reason
                
                display_data.append(row)
            
            # Create and style DataFrame
            display_df = pd.DataFrame(display_data)
            display_df = display_df.sort_values('Time', ascending=False).reset_index(drop=True)
            
            st.dataframe(
                display_df,
                width='stretch',
                hide_index=True,
                height=400
            )
            
            # Trade summary for recent period
            summary_cols = st.columns(4)
            
            with summary_cols[0]:
                entries = len(recent_trades[recent_trades['type'].str.contains('BUY|SELL', na=False)])
                st.metric("Recent Entries", entries)
            
            with summary_cols[1]:
                exits = len(recent_trades[recent_trades['type'].str.contains('PROFIT|STOP', na=False)])
                st.metric("Recent Exits", exits)
            
            with summary_cols[2]:
                if 'pnl' in recent_trades.columns:
                    recent_pnl = recent_trades['pnl'].sum()
                    st.metric("Recent PnL", format_number(recent_pnl, 2, prefix="$"))
            
            with summary_cols[3]:
                # Time since last trade
                if not recent_trades.empty:
                    last_trade_time = recent_trades['timestamp'].max()
                    time_since = datetime.now() - last_trade_time
                    hours = int(time_since.total_seconds() / 3600)
                    st.metric("Hours Since Last Trade", hours)
    
    with trades_tab2:
        # Trade analysis charts
        if 'pnl' in trades.columns:
            pnl_trades = trades[trades['pnl'].notna()]
            
            if not pnl_trades.empty:
                # PnL over time
                fig_pnl = go.Figure()
                
                colors = ['green' if x >= 0 else 'red' for x in pnl_trades['pnl']]
                fig_pnl.add_trace(go.Scatter(
                    x=pnl_trades['timestamp'],
                    y=pnl_trades['pnl'].cumsum(),
                    mode='lines+markers',
                    name='Cumulative PnL',
                    line=dict(width=2),
                    marker=dict(color=colors, size=6)
                ))
                
                fig_pnl.update_layout(
                    title="Cumulative PnL by Trade",
                    xaxis_title="Time",
                    yaxis_title="Cumulative PnL ($)",
                    height=400,
                    template="plotly_white"
                )
                
                st.plotly_chart(fig_pnl, width='stretch')
                
                # Trade size distribution
                col1, col2 = st.columns(2)
                
                with col1:
                    if 'size' in pnl_trades.columns:
                        fig_size = px.histogram(
                            pnl_trades, 
                            x='size', 
                            title="Trade Size Distribution",
                            nbins=20
                        )
                        fig_size.update_layout(height=300)
                        st.plotly_chart(fig_size, width='stretch')
                
                with col2:
                    # PnL distribution
                    fig_pnl_dist = px.histogram(
                        pnl_trades, 
                        x='pnl', 
                        title="PnL Distribution",
                        nbins=20,
                        color_discrete_sequence=['lightblue']
                    )
                    fig_pnl_dist.update_layout(height=300)
                    st.plotly_chart(fig_pnl_dist, width='stretch')
    
    with trades_tab3:
        # Performance by strategy
        if 'strategy' in trades.columns and 'pnl' in trades.columns:
            strategy_trades = trades[(trades['strategy'].notna()) & (trades['pnl'].notna())]
            
            if not strategy_trades.empty:
                strategy_stats = strategy_trades.groupby('strategy').agg({
                    'pnl': ['count', 'sum', 'mean'],
                    'size': 'mean'
                }).round(4)
                
                strategy_stats.columns = ['Trades', 'Total PnL', 'Avg PnL', 'Avg Size']
                strategy_stats['Win Rate %'] = strategy_trades.groupby('strategy')['pnl'].apply(lambda x: (x > 0).mean() * 100).round(2)
                
                st.subheader("Strategy Performance Breakdown")
                st.dataframe(strategy_stats, width='stretch')
                
                # Strategy PnL chart
                fig_strat = px.bar(
                    x=strategy_stats.index,
                    y=strategy_stats['Total PnL'],
                    title="Total PnL by Strategy",
                    color=strategy_stats['Total PnL'],
                    color_continuous_scale='RdYlGn'
                )
                fig_strat.update_layout(height=400)
                st.plotly_chart(fig_strat, width='stretch')

# System Logs Display
if show_logs and logs:
    st.subheader("📝 System Logs")
    
    # Filter logs by level
    log_level = st.selectbox("Log Level Filter", ["ALL", "INFO", "WARNING", "ERROR", "CRITICAL"])
    
    # Process and display logs
    filtered_logs = []
    for log in logs[-max_log_lines:]:
        if log_level == "ALL" or log_level in log.upper():
            # Color code logs
            if "ERROR" in log.upper() or "CRITICAL" in log.upper():
                log_color = "🔴"
            elif "WARNING" in log.upper():
                log_color = "🟡"
            elif "INFO" in log.upper():
                log_color = "🔵"
            else:
                log_color = "⚪"
            
            filtered_logs.append(f"{log_color} {log.strip()}")
    
    if filtered_logs:
        # Display in expandable text area
        log_text = "\n".join(filtered_logs)
        st.text_area("Recent Logs", log_text, height=300, disabled=True)
        
        # Log statistics
        log_cols = st.columns(4)
        with log_cols[0]:
            error_count = sum(1 for log in logs if "ERROR" in log.upper())
            st.metric("Errors", error_count)
        with log_cols[1]:
            warning_count = sum(1 for log in logs if "WARNING" in log.upper())
            st.metric("Warnings", warning_count)
        with log_cols[2]:
            info_count = sum(1 for log in logs if "INFO" in log.upper())
            st.metric("Info Messages", info_count)
        with log_cols[3]:
            st.metric("Total Log Lines", len(logs))
    else:
        st.info("No logs found matching the selected filter.")

# Debug Information
if show_debug_info:
    st.subheader("🔧 Debug Information")
    
    debug_tab1, debug_tab2, debug_tab3 = st.tabs(["File Status", "Configuration", "System Health"])
    
    with debug_tab1:
        st.write("**File System Status:**")
        
        for filename, status in files_status.items():
            if status.get('exists', False):
                size_mb = status['size'] / (1024*1024)
                age_minutes = status['age_seconds'] / 60
                mod_time = status['modified'].strftime('%Y-%m-%d %H:%M:%S')
                
                st.write(f"✅ **{filename}**")
                st.write(f"   - Size: {size_mb:.2f} MB")
                st.write(f"   - Modified: {mod_time}")
                st.write(f"   - Age: {age_minutes:.1f} minutes")
            else:
                st.write(f"❌ **{filename}** - Missing")
                if 'error' in status:
                    st.write(f"   - Error: {status['error']}")
    
    with debug_tab2:
        if config:
            st.write("**Current Configuration:**")
            
            # Display key config sections
            important_keys = ['simulation_mode', 'initial_capital', 'coins', 'timeframe', 'max_dd_percent']
            
            for key in important_keys:
                if key in config:
                    st.write(f"- **{key}**: {config[key]}")
            
            # Show strategy configs
            if 'rs75_strategy' in config:
                st.write("**Range Strategy Config:**")
                rs75_config = config['rs75_strategy']
                for key, value in list(rs75_config.items())[:5]:  # Show first 5 items
                    st.write(f"- {key}: {value}")
            
            if 'trend_strategy' in config:
                st.write("**Trend Strategy Config:**")
                trend_config = config['trend_strategy']
                for key, value in list(trend_config.items())[:5]:  # Show first 5 items
                    st.write(f"- {key}: {value}")
        else:
            st.error("Configuration not loaded")
    
    with debug_tab3:
        # System health indicators
        st.write("**System Health Indicators:**")
        
        # Check data freshness
        data_health = []
        
        if trades is not None:
            last_trade = trades['timestamp'].max()
            time_since_trade = datetime.now() - last_trade
            data_health.append(("Last Trade", f"{time_since_trade.total_seconds()/60:.1f} minutes ago"))
        
        if equity is not None:
            last_equity = equity['timestamp'].max()
            time_since_equity = datetime.now() - last_equity
            data_health.append(("Last Equity Update", f"{time_since_equity.total_seconds()/60:.1f} minutes ago"))
        
        # Display health indicators
        for indicator, value in data_health:
            st.write(f"- **{indicator}**: {value}")
        
        # Memory usage estimation
        total_records = 0
        if trades is not None:
            total_records += len(trades)
        if equity is not None:
            total_records += len(equity)
        
        st.write(f"- **Total Data Records**: {total_records:,}")

# Data Export Section
if export_data:
    st.subheader("💾 Data Export")
    
    export_cols = st.columns(3)
    
    with export_cols[0]:
        if trades is not None and st.button("Export Trades"):
            try:
                csv_data = trades.to_csv(index=False)
                st.download_button(
                    label="Download Trades CSV",
                    data=csv_data,
                    file_name=f"trades_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv"
                )
            except Exception as e:
                st.error(f"Export failed: {e}")
    
    with export_cols[1]:
        if equity is not None and st.button("Export Equity"):
            try:
                csv_data = equity.to_csv(index=False)
                st.download_button(
                    label="Download Equity CSV",
                    data=csv_data,
                    file_name=f"equity_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv"
                )
            except Exception as e:
                st.error(f"Export failed: {e}")
    
    with export_cols[2]:
        if position and st.button("Export Position"):
            try:
                json_data = json.dumps(position, indent=2, default=str)
                st.download_button(
                    label="Download Position JSON",
                    data=json_data,
                    file_name=f"position_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    mime="application/json"
                )
            except Exception as e:
                st.error(f"Export failed: {e}")

# Footer and Controls
st.markdown("---")

# Control buttons
st.subheader("🎛️ Dashboard Controls")
control_cols = st.columns(5)

with control_cols[0]:
    if st.button("🔄 Manual Refresh", type="primary"):
        st.cache_data.clear()
        st.rerun()

with control_cols[1]:
    if st.button("🗑️ Clear Cache"):
        st.cache_data.clear()
        st.success("Cache cleared!")
        time.sleep(1)
        st.rerun()

with control_cols[2]:
    if st.button("📊 Recalculate Stats"):
        st.cache_data.clear()
        st.success("Statistics recalculated!")
        time.sleep(1)
        st.rerun()

with control_cols[3]:
    # Quick stats refresh without full reload
    if st.button("⚡ Quick Update"):
        st.rerun()

with control_cols[4]:
    # Show current time
    if st.button("🕒 Current Time"):
        st.info(f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# Footer information
footer_cols = st.columns(3)

with footer_cols[0]:
    st.caption(f"Dashboard last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

with footer_cols[1]:
    if auto_refresh:
        st.caption(f"Auto-refresh: ON (every {refresh_interval}s)")
    else:
        st.caption("Auto-refresh: OFF")

with footer_cols[2]:
    # Show bot uptime if log file exists
    if files_status.get('ars3_bot.log', {}).get('exists', False):
        log_age = files_status['ars3_bot.log'].get('age_seconds', 0)
        uptime_hours = log_age / 3600
        st.caption(f"Bot uptime: ~{uptime_hours:.1f} hours")

# Help and troubleshooting
with st.expander("❓ Help & Troubleshooting"):
    st.markdown("""
    ### Common Issues & Solutions
    
    **🔴 No Data Showing:**
    - Ensure the trading bot is running (`python ars3_live_advanced_final.py`)
    - Check that CSV files are being generated in the bot directory
    - Verify file permissions
    
    **🟡 Stale Data:**
    - Bot may have crashed - check logs for errors
    - Restart the trading bot
    - Check network connectivity for live trading
    
    **🔵 Chart/Display Errors:**
    - Try refreshing the page (F5)
    - Clear dashboard cache using the button above
    - Check browser console for JavaScript errors
    
    **⚪ Performance Issues:**
    - Reduce max trades display count
    - Disable auto-refresh if not needed
    - Close unused browser tabs
    
    ### File Locations
    - **Trades Log**: `trades_live.csv`
    - **Equity Log**: `equity_live.csv` 
    - **Current Position**: `position.json`
    - **Bot Logs**: `ars3_bot.log`
    - **Configuration**: `ars3_config.yaml`
    - **Emergency Stop**: `EMERGENCY_STOP`
    
    ### Dashboard Features
    - **Real-time Monitoring**: Live trading activity and performance
    - **Multi-Strategy Support**: Range scalping (RS-75) and trend following
    - **Risk Management**: Drawdown tracking and position monitoring
    - **Emergency Controls**: Quick stop/start capabilities
    - **Data Export**: CSV/JSON export for external analysis
    
    ### Support
    For additional support, check the bot logs and configuration files.
    """)

# Version and credits
st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #666; font-size: 0.8em;'>
<strong>ARS-3 Advanced Trading Dashboard</strong><br>
Multi-Strategy Automated Trading System<br>
Enhanced Streamlit Interface v2.0
</div>
""", unsafe_allow_html=True)