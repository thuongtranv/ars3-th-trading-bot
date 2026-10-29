import pandas as pd
import matplotlib.pyplot as plt
import os
from datetime import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

from common.env import get_env
from common.notify import send_telegram, send_telegram_photo

EMAIL_USER = get_env("EMAIL_USER")       # optional
EMAIL_PASS = get_env("EMAIL_PASS")       # optional
EMAIL_TO = get_env("EMAIL_TO")           # optional

# Load logs
trades_file = "trades_live.csv"
equity_file = "equity_live.csv"

if os.path.exists(trades_file) and os.path.exists(equity_file):
    trades = pd.read_csv(trades_file, parse_dates=["timestamp"])
    equity = pd.read_csv(equity_file, parse_dates=["timestamp"])

    # Tổng lãi/lỗ từ các trades có PnL
    if not trades.empty:
        # Filter trades that have PnL data
        pnl_trades = trades[trades['pnl'].notna()] if 'pnl' in trades.columns else pd.DataFrame()
        total_pnl = pnl_trades['pnl'].sum() if not pnl_trades.empty else 0.0
        
        # If no PnL data available, estimate from equity curve
        if total_pnl == 0 and not equity.empty:
            initial_equity = equity['equity'].iloc[0] if len(equity) > 0 else 10000
            final_equity = equity['equity'].iloc[-1]
            total_pnl = final_equity - initial_equity
        
        total_pnl = round(total_pnl, 2)
    else:
        total_pnl = 0.0

    # Drawdown calculation
    if not equity.empty:
        equity['peak'] = equity['equity'].cummax()
        equity['dd'] = (equity['peak'] - equity['equity']) / equity['peak'] * 100
        max_dd = round(equity['dd'].max(), 2)
    else:
        max_dd = 0.0

    # Best coin today - FIXED: use 'symbol' column instead of 'signal'
    best_coin_today = "N/A"
    if not trades.empty:
        if 'symbol' in trades.columns:
            # Get the most recent symbol
            best_coin_today = trades['symbol'].iloc[-1]
        elif 'signal' in trades.columns:  # Fallback to old column name
            best_coin_today = trades['signal'].iloc[-1]
    
    # Additional statistics
    total_trades = len(trades[trades['type'].str.contains('BUY|SELL', na=False)]) if not trades.empty else 0
    winning_trades = len(trades[(trades['pnl'] > 0) & trades['pnl'].notna()]) if 'pnl' in trades.columns else 0
    losing_trades = len(trades[(trades['pnl'] < 0) & trades['pnl'].notna()]) if 'pnl' in trades.columns else 0
    win_rate = round((winning_trades / total_trades * 100), 2) if total_trades > 0 else 0.0

    # Create equity chart
    if not equity.empty:
        plt.figure(figsize=(12, 8))
        
        # Main equity plot
        plt.subplot(2, 1, 1)
        plt.plot(equity['timestamp'], equity['equity'], label="Equity", color="blue", linewidth=2)
        plt.fill_between(equity['timestamp'], equity['equity'].min(), equity['equity'], alpha=0.3, color="lightblue")
        plt.title("ARS-3 Equity Curve - Today", fontsize=16)
        plt.xlabel("Time")
        plt.ylabel("Equity (USDT)")
        plt.grid(True, alpha=0.3)
        plt.legend()
        
        # Drawdown plot
        plt.subplot(2, 1, 2)
        plt.fill_between(equity['timestamp'], 0, -equity['dd'], alpha=0.7, color="red", label="Drawdown %")
        plt.title("Drawdown", fontsize=14)
        plt.xlabel("Time")
        plt.ylabel("Drawdown (%)")
        plt.grid(True, alpha=0.3)
        plt.legend()
        
        plt.tight_layout()
        chart_file = "equity_report.png"
        plt.savefig(chart_file, dpi=300, bbox_inches='tight')
        plt.close()
    else:
        # Create a simple placeholder chart
        plt.figure(figsize=(8, 4))
        plt.text(0.5, 0.5, 'No equity data available', horizontalalignment='center', 
                 verticalalignment='center', transform=plt.gca().transAxes, fontsize=16)
        plt.title("ARS-3 Daily Report - No Data")
        chart_file = "equity_report.png"
        plt.savefig(chart_file)
        plt.close()

    # Enhanced Telegram report message
    report_msg = f"""📊 ARS-3 DAILY REPORT
    
💰 P&L Today: {total_pnl:+.2f} USDT
📉 Max Drawdown: {max_dd}%
🏆 Best Coin Today: {best_coin_today}

📈 Trade Statistics:
• Total Trades: {total_trades}
• Winning Trades: {winning_trades}
• Losing Trades: {losing_trades}
• Win Rate: {win_rate}%

🕒 Report Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    
{'✅ Profitable Day!' if total_pnl > 0 else '❌ Loss Day' if total_pnl < 0 else '➖ Break-even Day'}"""

    print("✅ Daily report sent via Telegram" if send_telegram(report_msg) else "❌ Failed to send Telegram message")
    try:
        print("✅ Chart sent via Telegram" if send_telegram_photo(chart_file) else "❌ Failed to send chart")
    except OSError as e:
        print(f"❌ Error sending chart: {e}")

    # Optional: Email report
    if EMAIL_USER and EMAIL_PASS and EMAIL_TO:
        try:
            msg_email = MIMEMultipart()
            msg_email['From'] = EMAIL_USER
            msg_email['To'] = EMAIL_TO
            msg_email['Subject'] = f"ARS-3 Daily Report - {datetime.now().strftime('%Y-%m-%d')}"
            
            # Add text content
            msg_email.attach(MIMEText(report_msg, 'plain'))
            
            # Add chart as attachment
            if os.path.exists(chart_file):
                with open(chart_file, 'rb') as f:
                    img = MIMEImage(f.read())
                    img.add_header('Content-Disposition', 'attachment', filename=chart_file)
                    msg_email.attach(img)
            
            # Send email
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg_email)
            server.quit()
            print("✅ Daily report sent via email")
            
        except Exception as e:
            print(f"❌ Email error: {e}")
    else:
        print("📧 Email credentials not configured (optional)")

    # Print summary to console
    print("\n" + "="*50)
    print("ARS-3 DAILY REPORT SUMMARY")
    print("="*50)
    print(report_msg)
    print("="*50)

else:
    error_msg = "❌ Chưa có dữ liệu trades hoặc equity để tạo report."
    print(error_msg)
    
    send_telegram(error_msg, prefix="🤖 ARS-3 Daily Report:\n")