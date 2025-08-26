import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import time
import os
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="ARS-3 Advanced Dashboard", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for better styling
st.markdown("""
<style>
.metric-card {
    background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
    padding: 1rem;
    border-radius: 10px;
    color: white;
    margin: 0.5rem 0;
}
.stAlert > div {
    padding: 0.5rem;
}
.status-success {
    background-color: #d4edda;
    border: 1px solid #c3e6cb;
    color: #155724;
    padding: 0.5rem;
    border-radius: 0.25rem;
    margin: 0.5rem 0;
}
.status-error {
    background-color: #f8d7da;
    border: 1px solid #f5c6cb;
    color: #721c24;
    padding: 0.5rem;
    border-radius: 0.25rem;
    margin: 0.5rem 0;
}
</style>
""", unsafe_allow_html=True)

st.title("ARS-3 Advanced Live Bot Dashboard")
st.markdown("---")

# Sidebar controls
st.sidebar.header("Dashboard Controls")
show_trades = st.sidebar.checkbox("Show recent trades", True)
show_equity = st.sidebar.checkbox("Show equity curve", True)
show_metrics = st.sidebar.checkbox("Show performance metrics", True)
show_position = st.sidebar.checkbox("Show current position", True)
auto_refresh = st.sidebar.checkbox("Auto refresh (30s)", False)
max_trades_display = st.sidebar.slider("Max trades to display", 5, 50, 10)

# Refresh interval
refresh_interval = st.sidebar.selectbox(
    "Refresh interval",
    [10, 30, 60, 300],
    index=1,
    format_func=lambda x: f"{x}s" if x < 60 else f"{x//60}m"
)

# Auto refresh functionality
if auto_refresh:
    placeholder = st.empty()
    time.sleep(1)
    st.rerun()

# Enhanced data loading with validation
@st.cache_data(ttl=30)
def load_data():
    """Load data with comprehensive error handling."""
    trades_data = None
    equity_data = None
    position_data = None
    
    # Load trades data
    try:
        trades_file = "trades_live.csv"
        if os.path.exists(trades_file) and os.path.getsize(trades_file) > 0:
            trades_data = pd.read_csv(trades_file)
            
            # Validate required columns
            if not trades_data.empty:
                # Check for required columns
                required_cols = ['timestamp', 'type']
                missing_cols = [col for col in required_cols if col not in trades_data.columns]
                
                if missing_cols:
                    logger.warning(f"Trades CSV missing columns: {missing_cols}")
                    trades_data = None
                else:
                    # Convert timestamp with error handling
                    try:
                        trades_data['timestamp'] = pd.to_datetime(trades_data['timestamp'], errors='coerce')
                        # Remove rows with invalid timestamps
                        trades_data = trades_data.dropna(subset=['timestamp'])
                        
                        if trades_data.empty:
                            logger.warning("All trades have invalid timestamps")
                            trades_data = None
                        else:
                            # Sort by timestamp
                            trades_data = trades_data.sort_values('timestamp').reset_index(drop=True)
                    except Exception as e:
                        logger.error(f"Error processing trades timestamps: {e}")
                        trades_data = None
            else:
                trades_data = None
    except Exception as e:
        logger.error(f"Error loading trades data: {e}")
        trades_data = None
    
    # Load equity data
    try:
        equity_file = "equity_live.csv"
        if os.path.exists(equity_file) and os.path.getsize(equity_file) > 0:
            equity_data = pd.read_csv(equity_file)
            
            if not equity_data.empty:
                required_cols = ['timestamp', 'equity']
                missing_cols = [col for col in required_cols if col not in equity_data.columns]
                
                if missing_cols:
                    logger.warning(f"Equity CSV missing columns: {missing_cols}")
                    equity_data = None
                else:
                    try:
                        equity_data['timestamp'] = pd.to_datetime(equity_data['timestamp'], errors='coerce')
                        equity_data = equity_data.dropna(subset=['timestamp', 'equity'])
                        
                        if equity_data.empty:
                            equity_data = None
                        else:
                            equity_data = equity_data.sort_values('timestamp').reset_index(drop=True)
                    except Exception as e:
                        logger.error(f"Error processing equity data: {e}")
                        equity_data = None
            else:
                equity_data = None
    except Exception as e:
        logger.error(f"Error loading equity data: {e}")
        equity_data = None
    
    # Load position data
    try:
        position_file = "position.json"
        if os.path.exists(position_file) and os.path.getsize(position_file) > 0:
            import json
            with open(position_file, 'r') as f:
                position_data = json.load(f)
                
            # Validate position data structure
            if not isinstance(position_data, dict) or not position_data:
                position_data = None
    except Exception as e:
        logger.error(f"Error loading position data: {e}")
        position_data = None
    
    return trades_data, equity_data, position_data

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
        return f"{prefix}{value:.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return "N/A"

def calculate_trade_stats(trades_data):
    """Calculate trade statistics safely."""
    try:
        if trades_data is None or trades_data.empty:
            return {}
        
        # Filter for trades with PnL data
        pnl_trades = trades_data[trades_data['pnl'].notna()] if 'pnl' in trades_data.columns else pd.DataFrame()
        
        stats = {
            'total_trades': len(trades_data[trades_data['type'].str.contains('BUY|SELL', na=False)]) if 'type' in trades_data.columns else 0,
            'total_pnl': pnl_trades['pnl'].sum() if not pnl_trades.empty else 0,
            'winning_trades': len(pnl_trades[pnl_trades['pnl'] > 0]) if not pnl_trades.empty else 0,
            'losing_trades': len(pnl_trades[pnl_trades['pnl'] < 0]) if not pnl_trades.empty else 0,
            'avg_win': pnl_trades[pnl_trades['pnl'] > 0]['pnl'].mean() if not pnl_trades.empty and (pnl_trades['pnl'] > 0).any() else 0,
            'avg_loss': pnl_trades[pnl_trades['pnl'] < 0]['pnl'].mean() if not pnl_trades.empty and (pnl_trades['pnl'] < 0).any() else 0
        }
        
        # Calculate win rate
        stats['win_rate'] = (stats['winning_trades'] / stats['total_trades'] * 100) if stats['total_trades'] > 0 else 0
        
        return stats
    except Exception as e:
        logger.error(f"Error calculating trade stats: {e}")
        return {}

# Load the data
trades, equity, position = load_data()

# Status indicators
st.subheader("System Status")
col1, col2, col3, col4 = st.columns(4)

with col1:
    if trades is not None:
        st.success("Trades Data: Connected")
    else:
        st.error("Trades Data: No Data")

with col2:
    if equity is not None:
        st.success("Equity Data: Connected")  
    else:
        st.error("Equity Data: No Data")

with col3:
    if position is not None and position:
        st.warning("Position: ACTIVE")
    else:
        st.info("Position: None")

with col4:
    st.info(f"Last Updated: {datetime.now().strftime('%H:%M:%S')}")

# Current position status
if show_position and position:
    st.subheader("Current Position")
    
    try:
        pos_col1, pos_col2, pos_col3, pos_col4 = st.columns(4)
        
        with pos_col1:
            st.metric("Symbol", safe_get_value(position, 'symbol'))
        
        with pos_col2:
            pos_type = safe_get_value(position, 'type')
            st.metric("Type", pos_type)
        
        with pos_col3:
            entry_price = safe_get_value(position, 'entry_price', 0)
            st.metric("Entry Price", format_number(entry_price, 4))
        
        with pos_col4:
            size = safe_get_value(position, 'size', 0)
            st.metric("Size", format_number(size, 6))
        
        # Additional position details
        pos_col5, pos_col6, pos_col7, pos_col8 = st.columns(4)
        
        with pos_col5:
            stop_loss = safe_get_value(position, 'stop_loss', 0)
            st.metric("Stop Loss", format_number(stop_loss, 4))
        
        with pos_col6:
            tp1 = safe_get_value(position, 'tp1', 0)
            st.metric("TP1", format_number(tp1, 4))
        
        with pos_col7:
            tp2 = safe_get_value(position, 'tp2', 0)
            st.metric("TP2", format_number(tp2, 4))
        
        with pos_col8:
            has_hit_tp1 = safe_get_value(position, 'has_hit_tp1', False)
            st.metric("TP1 Hit", "Yes" if has_hit_tp1 else "No")
            
    except Exception as e:
        st.error(f"Error displaying position: {e}")

# Trading status
st.subheader("Trading Status")

if trades is not None and not trades.empty:
    try:
        # Get current trading pair
        if 'symbol' in trades.columns:
            current_symbol = trades['symbol'].iloc[-1]
        else:
            current_symbol = "N/A"
        
        # Get last trade info
        last_trade_time = trades['timestamp'].iloc[-1].strftime('%Y-%m-%d %H:%M:%S')
        last_trade_type = trades['type'].iloc[-1] if 'type' in trades.columns else "Unknown"
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Current Symbol", current_symbol)
        with col2:
            st.metric("Last Trade", last_trade_type)
        with col3:
            st.metric("Last Activity", last_trade_time)
    except Exception as e:
        st.error(f"Error displaying trading status: {e}")
else:
    st.warning("No trading data available. Make sure the bot is running!")

# Performance metrics
if show_metrics and equity is not None and not equity.empty:
    st.subheader("Performance Metrics")
    
    try:
        # Calculate equity metrics
        initial_balance = equity['equity'].iloc[0]
        current_balance = equity['equity'].iloc[-1]
        total_return = ((current_balance - initial_balance) / initial_balance) * 100
        
        # Calculate drawdown
        peak = equity['equity'].expanding().max()
        drawdown = ((peak - equity['equity']) / peak * 100)
        max_drawdown = drawdown.max()
        
        # Get trade statistics
        trade_stats = calculate_trade_stats(trades)
        
        # Display metrics
        col1, col2, col3, col4, col5 = st.columns(5)
        
        with col1:
            st.metric(
                "Total Return", 
                format_number(total_return, 2, suffix="%"),
                delta=format_number(total_return, 2, suffix="%")
            )
        
        with col2:
            st.metric(
                "Current Balance", 
                format_number(current_balance, 2, prefix="$"),
                delta=format_number(current_balance - initial_balance, 2, prefix="$")
            )
        
        with col3:
            st.metric(
                "Max Drawdown",
                format_number(max_drawdown, 2, suffix="%")
            )
        
        with col4:
            st.metric("Total Trades", trade_stats.get('total_trades', 0))
        
        with col5:
            win_rate = trade_stats.get('win_rate', 0)
            st.metric("Win Rate", format_number(win_rate, 1, suffix="%"))
        
        # Additional metrics row
        col6, col7, col8, col9, col10 = st.columns(5)
        
        with col6:
            st.metric("Total PnL", format_number(trade_stats.get('total_pnl', 0), 2, prefix="$"))
        
        with col7:
            st.metric("Avg Win", format_number(trade_stats.get('avg_win', 0), 2, prefix="$"))
        
        with col8:
            st.metric("Avg Loss", format_number(trade_stats.get('avg_loss', 0), 2, prefix="$"))
        
        with col9:
            winning_trades = trade_stats.get('winning_trades', 0)
            st.metric("Winning Trades", winning_trades)
        
        with col10:
            losing_trades = trade_stats.get('losing_trades', 0)
            st.metric("Losing Trades", losing_trades)
            
    except Exception as e:
        st.error(f"Error calculating performance metrics: {e}")

# Equity curve visualization
if show_equity and equity is not None and not equity.empty:
    st.subheader("Equity Curve")
    
    try:
        # Create main equity chart
        fig = go.Figure()
        
        # Add equity line
        fig.add_trace(go.Scatter(
            x=equity["timestamp"], 
            y=equity["equity"],
            mode='lines',
            name='Equity',
            line=dict(color='#1f77b4', width=2),
            hovertemplate='<b>Time:</b> %{x}<br><b>Equity:</b> $%{y:.2f}<extra></extra>'
        ))
        
        # Add initial balance reference line
        initial_balance = equity['equity'].iloc[0]
        fig.add_hline(
            y=initial_balance, 
            line_dash="dash", 
            line_color="red",
            annotation_text="Initial Balance"
        )
        
        fig.update_layout(
            title="Equity Curve Over Time",
            xaxis_title="Time",
            yaxis_title="Equity (USDT)",
            hovermode='x unified',
            showlegend=True,
            height=500,
            template="plotly_white"
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
        # Add drawdown chart
        if len(equity) > 1:
            peak = equity['equity'].expanding().max()
            drawdown = (peak - equity['equity']) / peak * 100
            
            fig_dd = go.Figure()
            fig_dd.add_trace(go.Scatter(
                x=equity["timestamp"],
                y=-drawdown,
                mode='lines',
                fill='tozeroy',
                fillcolor='rgba(255, 0, 0, 0.3)',
                line=dict(color='red'),
                name='Drawdown %',
                hovertemplate='<b>Time:</b> %{x}<br><b>Drawdown:</b> %{y:.2f}%<extra></extra>'
            ))
            
            fig_dd.update_layout(
                title="Drawdown Over Time",
                xaxis_title="Time", 
                yaxis_title="Drawdown (%)",
                height=300,
                template="plotly_white"
            )
            
            st.plotly_chart(fig_dd, use_container_width=True)
            
    except Exception as e:
        st.error(f"Error creating equity charts: {e}")

# Recent trades table
if show_trades and trades is not None and not trades.empty:
    st.subheader("Recent Trades")
    
    try:
        # Get recent trades
        recent_trades = trades.tail(max_trades_display).copy()
        
        # Prepare display data
        if not recent_trades.empty:
            display_data = []
            
            for _, trade in recent_trades.iterrows():
                row = {
                    'Time': trade['timestamp'].strftime('%Y-%m-%d %H:%M:%S') if pd.notna(trade['timestamp']) else 'N/A',
                    'Symbol': safe_get_value(trade, 'symbol'),
                    'Type': safe_get_value(trade, 'type'),
                    'Price': format_number(safe_get_value(trade, 'price', 0), 4),
                    'Size': format_number(safe_get_value(trade, 'size', 0), 6)
                }
                
                # Add PnL if available
                if 'pnl' in trade and pd.notna(trade['pnl']):
                    row['PnL'] = format_number(trade['pnl'], 2, prefix="$")
                
                # Add reason if available
                if 'reason' in trade and pd.notna(trade['reason']):
                    row['Reason'] = str(trade['reason'])[:50] + "..." if len(str(trade['reason'])) > 50 else str(trade['reason'])
                
                display_data.append(row)
            
            # Create DataFrame and display
            display_df = pd.DataFrame(display_data)
            display_df = display_df.sort_values('Time', ascending=False).reset_index(drop=True)
            
            # Style the dataframe
            st.dataframe(
                display_df,
                use_container_width=True,
                hide_index=True,
                height=400
            )
        else:
            st.info("No recent trades to display")
            
    except Exception as e:
        st.error(f"Error displaying recent trades: {e}")

# System information
st.subheader("System Information")
col1, col2, col3 = st.columns(3)

with col1:
    try:
        if os.path.exists("ars3_bot.log"):
            log_size = os.path.getsize("ars3_bot.log") / 1024  # KB
            st.metric("Log File Size", f"{log_size:.1f} KB")
        else:
            st.metric("Log File Size", "N/A")
    except Exception:
        st.metric("Log File Size", "Error")

with col2:
    try:
        if trades is not None:
            data_age = datetime.now() - trades['timestamp'].max()
            st.metric("Data Age", f"{data_age.seconds // 60} min")
        else:
            st.metric("Data Age", "N/A")
    except Exception:
        st.metric("Data Age", "Error")

with col3:
    st.metric("Refresh Rate", f"{refresh_interval}s")

# Footer
st.markdown("---")
col1, col2 = st.columns(2)

with col1:
    st.caption(f"Dashboard updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

with col2:
    if auto_refresh:
        st.caption(f"Auto-refresh: ON ({refresh_interval}s)")
    else:
        st.caption("Auto-refresh: OFF")

# Control buttons
st.subheader("Controls")
col1, col2, col3 = st.columns(3)

with col1:
    if st.button("Manual Refresh", type="primary"):
        st.cache_data.clear()
        st.rerun()

with col2:
    if st.button("Clear Cache"):
        st.cache_data.clear()
        st.success("Cache cleared!")

with col3:
    if st.button("Download Data"):
        try:
            if trades is not None:
                csv = trades.to_csv(index=False)
                st.download_button(
                    label="Download Trades CSV",
                    data=csv,
                    file_name=f"trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv"
                )
        except Exception as e:
            st.error(f"Error preparing download: {e}")

# Error recovery section
with st.expander("Troubleshooting"):
    st.write("""
    **Common Issues:**
    - **No Data**: Make sure the bot is running and generating trades
    - **Stale Data**: Check if bot process is active and not crashed
    - **Chart Errors**: Try refreshing the page or clearing cache
    - **Performance Issues**: Reduce max trades display or disable auto-refresh
    
    **File Locations:**
    - Trades: `trades_live.csv`
    - Equity: `equity_live.csv`
    - Position: `position.json`
    - Logs: `ars3_bot.log`
    """)
    
    if st.button("Check Files"):
        files_status = {}
        for filename in ["trades_live.csv", "equity_live.csv", "position.json", "ars3_bot.log"]:
            if os.path.exists(filename):
                try:
                    size = os.path.getsize(filename)
                    modified = datetime.fromtimestamp(os.path.getmtime(filename))
                    files_status[filename] = f"Size: {size} bytes, Modified: {modified.strftime('%Y-%m-%d %H:%M:%S')}"
                except Exception as e:
                    files_status[filename] = f"Error: {e}"
            else:
                files_status[filename] = "File not found"
        
        for filename, status in files_status.items():
            st.write(f"**{filename}**: {status}")