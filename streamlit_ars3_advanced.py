import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import time
import os
import logging
import yaml
import json
import psutil
from datetime import datetime

# --- SETUP ---
st.set_page_config(
    page_title="ARS-3 Dashboard",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for a more professional look
st.markdown("""
<style>
    .metric-container {
        border: 1px solid #2e3440;
        border-radius: 10px;
        padding: 10px;
        margin: 5px;
        background-color: #f8f9fa; /* Light background for metrics */
    }
    .stButton>button {
        width: 100%;
    }
    .emergency-button button {
        background-color: #d9534f;
        color: white;
        border-color: #d43f3a;
    }
    .emergency-button button:hover {
        background-color: #c9302c;
        border-color: #ac2925;
    }
    .resume-button button {
        background-color: #5cb85c;
        color: white;
        border-color: #4cae4c;
    }
    .resume-button button:hover {
        background-color: #449d44;
        border-color: #398439;
    }
</style>
""", unsafe_allow_html=True)

# --- UTILITY FUNCTIONS ---

# Find project base directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_SCRIPT_NAME = "ars3_live.py"
EMERGENCY_STOP_FILE = os.path.join(BASE_DIR, "EMERGENCY_STOP")
POSITION_FILE = os.path.join(BASE_DIR, "position.json")
EQUITY_FILE = os.path.join(BASE_DIR, "equity_live.csv")
TRADES_FILE = os.path.join(BASE_DIR, "trades_live.csv")
LOG_FILE = os.path.join(BASE_DIR, "ars3_bot.log")

@st.cache_data(ttl=15)
def get_bot_status():
    """Check if the main bot process is running."""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if BOT_SCRIPT_NAME in " ".join(proc.info['cmdline']):
                return "RUNNING", proc.info['pid']
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    if os.path.exists(EMERGENCY_STOP_FILE):
        return "EMERGENCY STOP", None
    return "STOPPED", None

@st.cache_data(ttl=15)
def load_all_data():
    """Load and process all required data files."""
    data = {"trades": None, "equity": None, "position": None, "logs": "Log file not found."}
    
    # Load position
    if os.path.exists(POSITION_FILE):
        try:
            with open(POSITION_FILE, 'r') as f:
                pos_data = json.load(f)
                if pos_data: data["position"] = pos_data
        except (json.JSONDecodeError, IOError):
            pass

    # Load equity
    if os.path.exists(EQUITY_FILE):
        try:
            equity_df = pd.read_csv(EQUITY_FILE)
            equity_df['timestamp'] = pd.to_datetime(equity_df['timestamp'])
            data["equity"] = equity_df
        except Exception:
            pass

    # Load trades
    if os.path.exists(TRADES_FILE):
        try:
            trades_df = pd.read_csv(TRADES_FILE)
            trades_df['timestamp'] = pd.to_datetime(trades_df['timestamp'])
            data["trades"] = trades_df.sort_values(by="timestamp", ascending=False)
        except Exception:
            pass

    # Load logs
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                data["logs"] = "".join(lines[-50:]) # Last 50 lines
        except Exception:
            data["logs"] = "Error reading log file."
            
    return data

def calculate_metrics(equity_df, trades_df):
    """Calculate performance metrics from data."""
    if equity_df is None or equity_df.empty:
        return {}
    
    metrics = {}
    initial_balance = equity_df['equity'].iloc[0]
    current_balance = equity_df['equity'].iloc[-1]
    metrics['total_return_pct'] = ((current_balance - initial_balance) / initial_balance) * 100
    metrics['current_balance'] = current_balance
    
    peak = equity_df['equity'].expanding(min_periods=1).max()
    drawdown = (equity_df['equity'] - peak) / peak
    metrics['max_drawdown_pct'] = -drawdown.min() * 100
    
    if trades_df is not None and not trades_df.empty and 'pnl' in trades_df.columns:
        exit_trades = trades_df[trades_df['pnl'].notna()]
        metrics['total_trades'] = len(exit_trades)
        metrics['total_pnl'] = exit_trades['pnl'].sum()
        winning_trades = exit_trades[exit_trades['pnl'] > 0]
        losing_trades = exit_trades[exit_trades['pnl'] < 0]
        metrics['win_rate'] = (len(winning_trades) / metrics['total_trades'] * 100) if metrics['total_trades'] > 0 else 0
        metrics['profit_factor'] = winning_trades['pnl'].sum() / abs(losing_trades['pnl'].sum()) if abs(losing_trades['pnl'].sum()) > 0 else float('inf')
    else:
        metrics.update({'total_trades': 0, 'total_pnl': 0, 'win_rate': 0, 'profit_factor': 0})
        
    return metrics

def manage_emergency_stop(action):
    """Create or remove the emergency stop file."""
    if action == 'create' and not os.path.exists(EMERGENCY_STOP_FILE):
        with open(EMERGENCY_STOP_FILE, 'w') as f:
            f.write(f"STOPPED from dashboard at {datetime.now()}")
        st.toast("🚨 EMERGENCY STOP ACTIVATED!", icon="🚨")
    elif action == 'remove' and os.path.exists(EMERGENCY_STOP_FILE):
        os.remove(EMERGENCY_STOP_FILE)
        st.toast("✅ Trading Resumed!", icon="✅")
    time.sleep(1)
    st.rerun()

# --- SIDEBAR ---
st.sidebar.title("ARS-3 Mission Control")
auto_refresh_interval = st.sidebar.slider("Auto-refresh (seconds)", 5, 60, 15, 5)
auto_refresh = st.sidebar.toggle("Enable Auto-Refresh", value=True)
st.sidebar.markdown("---")
st.sidebar.header("Display Options")
show_position = st.sidebar.checkbox("Show Current Position", True)
show_metrics = st.sidebar.checkbox("Show Performance Metrics", True)
show_equity = st.sidebar.checkbox("Show Equity & Regime Charts", True)
show_trades = st.sidebar.checkbox("Show Recent Trades", True)
show_logs = st.sidebar.checkbox("Show Live Logs", True)

# --- MAIN DASHBOARD ---
st.title("ARS-3 Institutional Dashboard")
st.caption(f"Last update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# Load all data
app_data = load_all_data()
bot_status, bot_pid = get_bot_status()
equity, trades, position = app_data["equity"], app_data["trades"], app_data["position"]
metrics = calculate_metrics(equity, trades)

# --- TOP STATUS BAR ---
st.header("System At-a-Glance")
cols = st.columns(4)

status_color = {"RUNNING": "green", "STOPPED": "orange", "EMERGENCY STOP": "red"}
cols[0].metric("Bot Status", bot_status, delta=f"PID: {bot_pid}" if bot_pid else "N/A", delta_color="off")
st.markdown(f"<div style='margin-top:-2.2em; text-align:left; color:{status_color.get(bot_status, 'grey')};'>●</div>", unsafe_allow_html=True)

current_regime = equity['regime'].iloc[-1] if equity is not None and 'regime' in equity.columns and not equity.empty else "Undefined"
cols[1].metric("Market Regime", current_regime.upper())

active_strategy = position.get("strategy", "Idle").capitalize() if position else "Idle"
cols[2].metric("Active Strategy", active_strategy)

daily_pnl = equity['daily_pnl'].iloc[-1] if equity is not None and 'daily_pnl' in equity.columns and not equity.empty else 0
cols[3].metric("Daily PnL", f"${daily_pnl:,.2f}", delta=f"{daily_pnl:,.2f}", delta_color="normal" if daily_pnl >= 0 else "inverse")

st.markdown("---")

# --- CURRENT POSITION ---
if show_position and position:
    st.header("Current Position")
    p_cols = st.columns(5)
    p_cols[0].metric("Symbol", position.get("symbol", "N/A"))
    p_cols[1].metric("Type", position.get("type", "N/A"))
    p_cols[2].metric("Entry Price", f"{position.get('entry_price', 0):,.4f}")
    p_cols[3].metric("Size", f"{position.get('size', 0):.6f}")
    p_cols[4].metric("Stop Loss", f"{position.get('stop_loss', 0):,.4f}")

# --- PERFORMANCE METRICS ---
if show_metrics:
    st.header("Performance Metrics")
    m_cols = st.columns(5)
    m_cols[0].metric("Total Return", f"{metrics.get('total_return_pct', 0):.2f}%")
    m_cols[1].metric("Current Balance", f"${metrics.get('current_balance', 0):,.2f}")
    m_cols[2].metric("Max Drawdown", f"{metrics.get('max_drawdown_pct', 0):.2f}%")
    m_cols[3].metric("Total Trades", f"{metrics.get('total_trades', 0)}")
    m_cols[4].metric("Win Rate", f"{metrics.get('win_rate', 0):.2f}%")
    
# --- EQUITY & REGIME CHARTS ---
if show_equity and equity is not None and not equity.empty:
    st.header("Performance Visuals")
    
    # Equity Curve
    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(x=equity["timestamp"], y=equity["equity"], mode='lines', name='Equity', line=dict(color='#1f77b4')))
    fig_eq.update_layout(title="Equity Curve", yaxis_title="Balance (USDT)", template="plotly_white")
    st.plotly_chart(fig_eq, use_container_width=True)
    
    # Regime Chart
    if 'regime' in equity.columns:
        regime_map = {"trending": 1, "ranging": -1, "compression": 0, "undefined": 0.5}
        equity['regime_numeric'] = equity['regime'].map(regime_map).fillna(0.5)
        
        fig_regime = go.Figure()
        fig_regime.add_trace(go.Scatter(
            x=equity["timestamp"], y=equity["regime_numeric"], mode='lines',
            line_shape='hv', name='Market Regime', line=dict(color='#ff7f0e')
        ))
        fig_regime.update_layout(
            title="Market Regime Over Time",
            yaxis=dict(
                tickmode='array',
                tickvals=[1, 0, -1],
                ticktext=['Trending', 'Compression', 'Ranging']
            ),
            template="plotly_white"
        )
        st.plotly_chart(fig_regime, use_container_width=True)

# --- RECENT TRADES ---
if show_trades and trades is not None and not trades.empty:
    st.header("Recent Trades")
    display_trades = trades.head(20)
    # Ensure all required columns exist, adding them with default values if not
    for col in ['symbol', 'type', 'price', 'size', 'pnl', 'strategy', 'reason']:
        if col not in display_trades.columns:
            display_trades[col] = "N/A"
    
    st.dataframe(display_trades[[
        'timestamp', 'symbol', 'type', 'price', 'size', 'pnl', 'strategy', 'reason'
    ]], hide_index=True, use_container_width=True)

# --- LIVE LOGS & CONTROLS ---
st.markdown("---")
log_cols = st.columns([2, 1])

with log_cols[0]:
    if show_logs:
        st.subheader("Live Log Viewer")
        st.text_area("Last 50 log entries", value=app_data["logs"], height=300, disabled=True)

with log_cols[1]:
    st.subheader("Emergency Controls")
    st.warning("Use with caution. This directly controls the bot.")
    
    with st.container():
        st.markdown('<div class="emergency-button">', unsafe_allow_html=True)
        if st.button("🚨 EMERGENCY STOP", key="stop_button", help="Stops the bot and prevents restarts."):
            manage_emergency_stop('create')
        st.markdown('</div>', unsafe_allow_html=True)
    
    with st.container():
        st.markdown('<div class="resume-button">', unsafe_allow_html=True)
        if st.button("✅ Resume Trading", key="resume_button", help="Removes the emergency stop file."):
            manage_emergency_stop('remove')
        st.markdown('</div>', unsafe_allow_html=True)
        
# --- AUTO-REFRESH LOGIC ---
if auto_refresh:
    time.sleep(auto_refresh_interval)
    st.rerun()