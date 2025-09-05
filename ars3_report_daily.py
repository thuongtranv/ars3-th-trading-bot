import pandas as pd
import matplotlib.pyplot as plt
import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
import requests
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

# Configuration
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
EMAIL_USER = os.getenv("EMAIL_USER")       # optional
EMAIL_PASS = os.getenv("EMAIL_PASS")       # optional
EMAIL_TO = os.getenv("EMAIL_TO")           # optional

# File paths
TRADES_FILE = "trades_live.csv"
EQUITY_FILE = "equity_live.csv"
CHART_FILE = "equity_report.png"

def load_data_safely(file_path, date_columns=None):
    """Safely load CSV data with error handling"""
    try:
        if not os.path.exists(file_path):
            logger.warning(f"File not found: {file_path}")
            return pd.DataFrame()
        
        df = pd.read_csv(file_path)
        
        # Parse date columns if specified
        if date_columns and not df.empty:
            for col in date_columns:
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], errors='coerce')
        
        logger.info(f"Loaded {len(df)} records from {file_path}")
        return df
        
    except Exception as e:
        logger.error(f"Error loading {file_path}: {e}")
        return pd.DataFrame()

def calculate_statistics(trades_df, equity_df):
    """Calculate comprehensive trading statistics"""
    stats = {
        'total_pnl': 0.0,
        'max_drawdown': 0.0,
        'best_symbol': 'N/A',
        'total_trades': 0,
        'winning_trades': 0,
        'losing_trades': 0,
        'win_rate': 0.0,
        'avg_win': 0.0,
        'avg_loss': 0.0,
        'largest_win': 0.0,
        'largest_loss': 0.0,
        'current_equity': 0.0,
        'daily_return_pct': 0.0
    }
    
    try:
        # Calculate P&L from trades
        if not trades_df.empty and 'pnl' in trades_df.columns:
            pnl_trades = trades_df[trades_df['pnl'].notna()]
            if not pnl_trades.empty:
                stats['total_pnl'] = round(pnl_trades['pnl'].sum(), 2)
                
                winning_trades = pnl_trades[pnl_trades['pnl'] > 0]
                losing_trades = pnl_trades[pnl_trades['pnl'] < 0]
                
                stats['winning_trades'] = len(winning_trades)
                stats['losing_trades'] = len(losing_trades)
                stats['total_trades'] = len(pnl_trades)
                
                if stats['total_trades'] > 0:
                    stats['win_rate'] = round((stats['winning_trades'] / stats['total_trades']) * 100, 2)
                
                if not winning_trades.empty:
                    stats['avg_win'] = round(winning_trades['pnl'].mean(), 2)
                    stats['largest_win'] = round(winning_trades['pnl'].max(), 2)
                
                if not losing_trades.empty:
                    stats['avg_loss'] = round(losing_trades['pnl'].mean(), 2)
                    stats['largest_loss'] = round(losing_trades['pnl'].min(), 2)
        
        # Calculate from equity curve if P&L data unavailable
        if stats['total_pnl'] == 0 and not equity_df.empty:
            initial_equity = equity_df['equity'].iloc[0] if len(equity_df) > 0 else 10000
            final_equity = equity_df['equity'].iloc[-1] if len(equity_df) > 0 else initial_equity
            stats['total_pnl'] = round(final_equity - initial_equity, 2)
            stats['current_equity'] = round(final_equity, 2)
            stats['daily_return_pct'] = round((stats['total_pnl'] / initial_equity) * 100, 2)
        
        # Drawdown calculation
        if not equity_df.empty:
            equity_series = equity_df['equity']
            peak = equity_series.expanding().max()
            drawdown = (peak - equity_series) / peak * 100
            stats['max_drawdown'] = round(drawdown.max(), 2)
        
        # Best performing symbol
        if not trades_df.empty and 'symbol' in trades_df.columns:
            # Get the most recent symbol or most traded symbol
            if 'pnl' in trades_df.columns:
                # Find symbol with highest total PnL
                symbol_pnl = trades_df.groupby('symbol')['pnl'].sum()
                if not symbol_pnl.empty:
                    stats['best_symbol'] = symbol_pnl.idxmax()
            else:
                # Most recent symbol
                stats['best_symbol'] = trades_df['symbol'].iloc[-1] if not trades_df.empty else 'N/A'
        elif not trades_df.empty and 'signal' in trades_df.columns:  # Fallback
            stats['best_symbol'] = trades_df['signal'].iloc[-1]
        
        return stats
        
    except Exception as e:
        logger.error(f"Error calculating statistics: {e}")
        return stats

def create_equity_chart(equity_df, stats):
    """Create comprehensive equity and performance chart"""
    try:
        if equity_df.empty:
            # Create placeholder chart
            plt.figure(figsize=(12, 6))
            plt.text(0.5, 0.5, 'No equity data available', 
                    horizontalalignment='center', verticalalignment='center',
                    transform=plt.gca().transAxes, fontsize=16)
            plt.title("ARS-3 Daily Report - No Data Available")
            plt.savefig(CHART_FILE, dpi=300, bbox_inches='tight')
            plt.close()
            logger.info("Created placeholder chart")
            return True
        
        # Create comprehensive chart with multiple subplots
        fig, axes = plt.subplots(3, 1, figsize=(15, 12))
        
        # 1. Equity curve
        axes[0].plot(equity_df['timestamp'], equity_df['equity'], 
                    label="Equity", color="blue", linewidth=2)
        axes[0].fill_between(equity_df['timestamp'], equity_df['equity'].min(), 
                           equity_df['equity'], alpha=0.3, color="lightblue")
        
        # Add horizontal line for initial balance if available
        if stats['current_equity'] > 0:
            initial_balance = equity_df['equity'].iloc[0]
            axes[0].axhline(y=initial_balance, color='red', linestyle='--', alpha=0.7, 
                          label=f'Initial: ${initial_balance:.2f}')
        
        axes[0].set_title("ARS-3 Equity Curve", fontsize=16, fontweight='bold')
        axes[0].set_ylabel("Equity (USDT)", fontsize=12)
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)
        
        # 2. Drawdown analysis
        if len(equity_df) > 1:
            equity_series = equity_df['equity']
            peak = equity_series.expanding().max()
            drawdown = (peak - equity_series) / peak * 100
            
            axes[1].fill_between(equity_df['timestamp'], 0, -drawdown, 
                               alpha=0.7, color="red", label="Drawdown %")
            axes[1].set_title("Portfolio Drawdown", fontsize=14)
            axes[1].set_ylabel("Drawdown (%)", fontsize=12)
            axes[1].legend()
            axes[1].grid(True, alpha=0.3)
        else:
            axes[1].text(0.5, 0.5, 'Insufficient data for drawdown analysis', 
                        horizontalalignment='center', verticalalignment='center',
                        transform=axes[1].transAxes, fontsize=12)
            axes[1].set_title("Portfolio Drawdown", fontsize=14)
        
        # 3. Performance metrics summary
        axes[2].axis('off')  # Turn off axes for text display
        
        # Create performance summary text
        summary_text = f"""
DAILY PERFORMANCE SUMMARY

Financial Metrics:
• Total P&L: ${stats['total_pnl']:+.2f} USDT
• Current Equity: ${stats['current_equity']:.2f} USDT
• Daily Return: {stats['daily_return_pct']:+.2f}%
• Max Drawdown: {stats['max_drawdown']:.2f}%

Trading Statistics:
• Total Trades: {stats['total_trades']}
• Winning Trades: {stats['winning_trades']}
• Losing Trades: {stats['losing_trades']}
• Win Rate: {stats['win_rate']:.1f}%

Trade Analysis:
• Average Win: ${stats['avg_win']:.2f}
• Average Loss: ${stats['avg_loss']:.2f}
• Largest Win: ${stats['largest_win']:.2f}
• Largest Loss: ${stats['largest_loss']:.2f}

Best Symbol: {stats['best_symbol']}
Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        """
        
        axes[2].text(0.05, 0.95, summary_text, transform=axes[2].transAxes,
                    fontsize=11, verticalalignment='top', fontfamily='monospace',
                    bbox=dict(boxstyle="round,pad=0.5", facecolor="lightgray", alpha=0.8))
        
        plt.tight_layout()
        plt.savefig(CHART_FILE, dpi=300, bbox_inches='tight', facecolor='white')
        plt.close()
        
        logger.info(f"Equity chart saved to {CHART_FILE}")
        return True
        
    except Exception as e:
        logger.error(f"Error creating equity chart: {e}")
        # Create simple fallback chart
        try:
            plt.figure(figsize=(8, 4))
            plt.text(0.5, 0.5, f'Chart generation error: {str(e)[:100]}', 
                    horizontalalignment='center', verticalalignment='center',
                    transform=plt.gca().transAxes, fontsize=12)
            plt.title("ARS-3 Daily Report - Chart Error")
            plt.savefig(CHART_FILE, dpi=150, bbox_inches='tight')
            plt.close()
            return False
        except Exception as fallback_error:
            logger.error(f"Fallback chart creation failed: {fallback_error}")
            return False

def format_report_message(stats):
    """Format comprehensive report message"""
    # Determine day performance
    if stats['total_pnl'] > 0:
        performance_emoji = "✅"
        performance_text = "Profitable Day!"
    elif stats['total_pnl'] < 0:
        performance_emoji = "❌" 
        performance_text = "Loss Day"
    else:
        performance_emoji = "➖"
        performance_text = "Break-even Day"
    
    # Risk assessment
    risk_level = ""
    if stats['max_drawdown'] > 10:
        risk_level = "\n⚠️ HIGH DRAWDOWN WARNING"
    elif stats['max_drawdown'] > 5:
        risk_level = "\n⚡ Moderate drawdown detected"
    
    report_msg = f"""📊 ARS-3 DAILY PERFORMANCE REPORT

💰 Financial Summary:
• P&L Today: ${stats['total_pnl']:+.2f} USDT
• Current Equity: ${stats['current_equity']:.2f} USDT  
• Daily Return: {stats['daily_return_pct']:+.2f}%
• Max Drawdown: {stats['max_drawdown']:.2f}%{risk_level}

📈 Trading Activity:
• Total Trades: {stats['total_trades']}
• Win Rate: {stats['win_rate']:.1f}% ({stats['winning_trades']}W/{stats['losing_trades']}L)
• Average Win: ${stats['avg_win']:.2f}
• Average Loss: ${stats['avg_loss']:.2f}
• Best/Worst: ${stats['largest_win']:.2f}/${stats['largest_loss']:.2f}

🎯 Top Symbol: {stats['best_symbol']}
🕐 Report Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{performance_emoji} {performance_text}"""

    return report_msg

def send_telegram_report(message, chart_path=None):
    """Send report via Telegram with enhanced error handling"""
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning("Telegram credentials not configured")
        return False
    
    success = True
    
    # Send text message
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        response = requests.post(url, data={
            "chat_id": CHAT_ID, 
            "text": f"🤖 ARS-3 Bot:\n{message}",
            "parse_mode": "HTML"
        }, timeout=15)
        
        if response.status_code == 200:
            logger.info("Daily report sent via Telegram")
        else:
            logger.error(f"Failed to send Telegram message: {response.status_code}")
            success = False
            
    except Exception as e:
        logger.error(f"Telegram message error: {e}")
        success = False
    
    # Send chart if available
    if chart_path and os.path.exists(chart_path):
        try:
            url_photo = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
            with open(chart_path, 'rb') as f:
                response = requests.post(url_photo, 
                                       data={"chat_id": CHAT_ID, "caption": "Daily Performance Chart"}, 
                                       files={"photo": f}, timeout=30)
                
            if response.status_code == 200:
                logger.info("Chart sent via Telegram")
            else:
                logger.error(f"Failed to send chart: {response.status_code}")
                success = False
                
        except Exception as e:
            logger.error(f"Error sending chart: {e}")
            success = False
    
    return success

def send_email_report(message, chart_path=None):
    """Send report via email with attachments"""
    if not all([EMAIL_USER, EMAIL_PASS, EMAIL_TO]):
        logger.info("Email credentials not configured (optional)")
        return False
    
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = EMAIL_TO
        msg['Subject'] = f"ARS-3 Daily Trading Report - {datetime.now().strftime('%Y-%m-%d')}"
        
        # Add text content
        html_content = f"""
        <html>
        <body>
        <h2>ARS-3 Daily Trading Report</h2>
        <pre style="font-family: monospace; background-color: #f5f5f5; padding: 10px;">
{message}
        </pre>
        <p><i>Generated by ARS-3 Trading Bot</i></p>
        </body>
        </html>
        """
        msg.attach(MIMEText(html_content, 'html'))
        
        # Add chart as attachment
        if chart_path and os.path.exists(chart_path):
            with open(chart_path, 'rb') as f:
                img = MIMEImage(f.read())
                img.add_header('Content-Disposition', 'attachment', filename='daily_chart.png')
                msg.attach(img)
        
        # Send email
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
        
        logger.info("Daily report sent via email")
        return True
        
    except Exception as e:
        logger.error(f"Email sending error: {e}")
        return False

def cleanup_files():
    """Clean up temporary files"""
    try:
        if os.path.exists(CHART_FILE):
            # Keep the chart for a day, then remove
            file_age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(CHART_FILE))
            if file_age > timedelta(days=1):
                os.remove(CHART_FILE)
                logger.info("Old chart file cleaned up")
    except Exception as e:
        logger.debug(f"Cleanup error: {e}")

def main():
    """Main report generation function"""
    logger.info("=" * 60)
    logger.info("ARS-3 DAILY REPORT GENERATOR")
    logger.info("=" * 60)
    
    try:
        # Load data
        logger.info("Loading trading data...")
        trades_df = load_data_safely(TRADES_FILE, ['timestamp'])
        equity_df = load_data_safely(EQUITY_FILE, ['timestamp'])
        
        if trades_df.empty and equity_df.empty:
            error_msg = "No trading data available to generate report."
            logger.error(error_msg)
            
            # Send error notification
            if BOT_TOKEN and CHAT_ID:
                send_telegram_report("⚠️ No trading data available for daily report generation.")
            
            print(f"\n{error_msg}")
            return False
        
        # Calculate statistics
        logger.info("Calculating performance statistics...")
        stats = calculate_statistics(trades_df, equity_df)
        
        # Create visualization
        logger.info("Generating performance chart...")
        chart_created = create_equity_chart(equity_df, stats)
        
        # Format report message
        report_message = format_report_message(stats)
        
        # Send notifications
        logger.info("Sending notifications...")
        telegram_success = send_telegram_report(report_message, CHART_FILE if chart_created else None)
        email_success = send_email_report(report_message, CHART_FILE if chart_created else None)
        
        # Console output
        print("\n" + "=" * 60)
        print("ARS-3 DAILY REPORT SUMMARY")
        print("=" * 60)
        print(report_message)
        print("=" * 60)
        
        # Report delivery status
        delivery_status = []
        if telegram_success:
            delivery_status.append("Telegram")
        if email_success:
            delivery_status.append("Email")
        
        if delivery_status:
            print(f"\n✅ Report delivered via: {', '.join(delivery_status)}")
        else:
            print(f"\n⚠️ Report generated but delivery failed")
        
        # Cleanup
        cleanup_files()
        
        logger.info("Daily report generation completed successfully")
        return True
        
    except Exception as e:
        error_msg = f"Critical error in report generation: {e}"
        logger.error(error_msg)
        
        # Send error notification
        if BOT_TOKEN and CHAT_ID:
            try:
                send_telegram_report(f"🚨 Daily Report Error: {str(e)[:200]}")
            except:
                pass
        
        print(f"\n❌ {error_msg}")
        return False

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⏹️ Report generation interrupted by user")
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        logger.critical(f"Unexpected error: {e}", exc_info=True)