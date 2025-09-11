import os
import platform
import subprocess
import time
import threading
import requests
import logging
import signal
import sys
from datetime import datetime
import psutil
import yaml
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    # Thêm encoding='utf-8' vào cả FileHandler và StreamHandler
    handlers=[
        logging.FileHandler('ars3_autostart.log', encoding='utf-8'),
        logging.StreamHandler() # Console trên Windows có thể vẫn bị, nhưng lỗi file log sẽ hết
    ]
)

logger = logging.getLogger(__name__)

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_SCRIPT = os.path.join(BASE_DIR, "ars3_live.py")
DASH_SCRIPT = os.path.join(BASE_DIR, "streamlit_ars3_advanced.py")
CONFIG_FILE = os.path.join(BASE_DIR, "ars3_config.yaml")
EMERGENCY_STOP_FILE = os.path.join(BASE_DIR, "EMERGENCY_STOP")

# Load configuration
try:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    SIMULATION_MODE = config.get("simulation_mode", True)
except Exception as e:
    logger.error(f"Failed to load config: {e}")
    SIMULATION_MODE = True

# Telegram config
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Process tracking
processes = {}
shutdown_flag = threading.Event()

class ProcessManager:
    def __init__(self, name, command, restart_delay=5, max_restarts=10):
        self.name = name
        self.command = command
        self.restart_delay = restart_delay
        self.max_restarts = max_restarts
        self.restart_count = 0
        self.process = None
        self.last_restart = None
        self.running = False
        self.consecutive_failures = 0
        
    def start(self):
        """Start the process"""
        try:
            if self.process and self.process.poll() is None:
                logger.warning(f"{self.name} is already running")
                return True
            
            logger.info(f"Starting {self.name}...")
            send_telegram(f"🚀 Starting {self.name}...")
            
            # Enhanced process creation with proper error handling
            if platform.system() == "Windows":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                self.process = subprocess.Popen(
                    self.command,
                    shell=True,
                    startupinfo=startupinfo,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
            else:
                self.process = subprocess.Popen(
                    self.command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid if hasattr(os, 'setsid') else None
                )
            
            self.running = True
            self.last_restart = datetime.now()
            self.consecutive_failures = 0
            
            # Reset restart count if process has been running for more than 1 hour
            if self.restart_count > 0 and self.last_restart:
                time_running = (datetime.now() - self.last_restart).total_seconds()
                if time_running > 3600:  # 1 hour
                    self.restart_count = 0
                    logger.info(f"Reset restart count for {self.name} after successful run")
            
            logger.info(f"✅ {self.name} started with PID {self.process.pid}")
            return True
            
        except Exception as e:
            self.consecutive_failures += 1
            logger.error(f"Failed to start {self.name}: {e}")
            send_telegram(f"❌ Failed to start {self.name}: {e}")
            return False
    
    def stop(self):
        """Stop the process gracefully"""
        try:
            if not self.process or self.process.poll() is not None:
                logger.info(f"{self.name} is not running")
                return True
            
            logger.info(f"Stopping {self.name}...")
            
            # Platform-specific graceful shutdown
            if platform.system() == "Windows":
                # Windows: Try terminate first, then kill
                self.process.terminate()
            else:
                # Unix: Try SIGTERM first
                if hasattr(os, 'killpg'):
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                else:
                    self.process.terminate()
            
            # Wait for graceful shutdown
            try:
                self.process.wait(timeout=15)
                logger.info(f"✅ {self.name} stopped gracefully")
            except subprocess.TimeoutExpired:
                logger.warning(f"Force killing {self.name}...")
                if platform.system() == "Windows":
                    self.process.kill()
                else:
                    if hasattr(os, 'killpg'):
                        os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                    else:
                        self.process.kill()
                
                self.process.wait()
                logger.info(f"⚠️ {self.name} force killed")
            
            self.running = False
            return True
            
        except Exception as e:
            logger.error(f"Error stopping {self.name}: {e}")
            return False
    
    def is_healthy(self):
        """Check if process is healthy"""
        try:
            if not self.process or self.process.poll() is not None:
                return False
            
            # Check if process is consuming reasonable resources
            try:
                proc = psutil.Process(self.process.pid)
                
                # Check if process is still alive
                if not proc.is_running():
                    return False
                
                cpu_percent = proc.cpu_percent()
                memory_mb = proc.memory_info().rss / 1024 / 1024
                
                # Resource usage warnings
                if cpu_percent > 95:
                    logger.warning(f"{self.name} using {cpu_percent:.1f}% CPU")
                if memory_mb > 2000:  # 2GB warning threshold
                    logger.warning(f"{self.name} using {memory_mb:.1f}MB memory")
                
                # Fail if consuming too much memory (protection against memory leaks)
                if memory_mb > 4000:  # 4GB hard limit
                    logger.error(f"{self.name} exceeded memory limit: {memory_mb:.1f}MB")
                    return False
                
                return True
                
            except psutil.NoSuchProcess:
                return False
            except psutil.AccessDenied:
                # Process exists but we can't access it - assume it's running
                return True
                
        except Exception as e:
            logger.error(f"Error checking {self.name} health: {e}")
            return False
    
    def restart(self):
        """Restart the process with exponential backoff"""
        if self.restart_count >= self.max_restarts:
            logger.error(f"{self.name} has reached max restart attempts ({self.max_restarts})")
            send_telegram(f"🚨 {self.name} failed permanently after {self.max_restarts} attempts")
            return False
        
        self.restart_count += 1
        
        # Exponential backoff for repeated failures
        delay = min(self.restart_delay * (2 ** self.consecutive_failures), 300)  # Max 5 minutes
        
        logger.info(f"Restarting {self.name} (attempt {self.restart_count}/{self.max_restarts}) after {delay}s delay")
        
        self.stop()
        time.sleep(delay)
        
        if shutdown_flag.is_set():
            return False
        
        success = self.start()
        if not success:
            self.consecutive_failures += 1
        
        return success

def send_telegram(msg):
    """Send telegram message with enhanced retry logic"""
    if not BOT_TOKEN or not CHAT_ID:
        return
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            
            # Truncate very long messages
            if len(msg) > 4000:
                msg = msg[:3900] + "... [truncated]"
            
            response = requests.post(
                url, 
                data={
                    "chat_id": CHAT_ID, 
                    "text": f"🤖 ARS-3 System:\n{msg}"
                }, 
                timeout=15
            )
            
            if response.status_code == 200:
                logger.debug("Telegram message sent successfully")
                return True
            else:
                logger.warning(f"Telegram send failed (attempt {attempt + 1}): {response.status_code}")
                
        except requests.RequestException as e:
            logger.error(f"Telegram request error (attempt {attempt + 1}): {e}")
        except Exception as e:
            logger.error(f"Unexpected telegram error (attempt {attempt + 1}): {e}")
        
        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)  # Exponential backoff
    
    logger.error("Failed to send telegram message after all attempts")
    return False

def check_prerequisites():
    """Check if all required files exist"""
    required_files = [BOT_SCRIPT, CONFIG_FILE]
    
    # Dashboard is optional - only warn if missing
    if not os.path.exists(DASH_SCRIPT):
        logger.warning(f"Dashboard script not found: {DASH_SCRIPT}")
        logger.warning("Dashboard component will be skipped")
    
    missing_critical = [f for f in required_files if not os.path.exists(f)]
    
    if missing_critical:
        logger.error(f"Missing critical files: {missing_critical}")
        send_telegram(f"❌ Missing critical files: {missing_critical}")
        return False
    
    # Check Python environment
    try:
        import pandas, numpy, ccxt, yaml, requests
        logger.info("✅ Required Python packages available")
    except ImportError as e:
        logger.error(f"Missing required Python package: {e}")
        send_telegram(f"❌ Missing Python package: {e}")
        return False
    
    return True

def check_emergency_stop():
    """Check for emergency stop signal"""
    return os.path.exists(EMERGENCY_STOP_FILE)

def create_emergency_stop():
    """Create emergency stop file"""
    try:
        with open(EMERGENCY_STOP_FILE, 'w') as f:
            f.write(f"Emergency stop created at {datetime.now()}")
        logger.info("Emergency stop file created")
        send_telegram("🚨 Emergency stop activated")
        return True
    except Exception as e:
        logger.error(f"Failed to create emergency stop file: {e}")
        return False

def remove_emergency_stop():
    """Remove emergency stop file"""
    try:
        if os.path.exists(EMERGENCY_STOP_FILE):
            os.remove(EMERGENCY_STOP_FILE)
            logger.info("Emergency stop file removed")
            send_telegram("✅ Emergency stop deactivated")
            return True
        return True
    except Exception as e:
        logger.error(f"Failed to remove emergency stop file: {e}")
        return False

def monitor_processes():
    """Monitor and restart processes as needed"""
    logger.info("Starting process monitoring...")
    
    emergency_stop_notified = False
    
    while not shutdown_flag.is_set():
        try:
            # Check for emergency stop
            if check_emergency_stop():
                if not emergency_stop_notified:
                    logger.warning("Emergency stop detected, halting all processes")
                    send_telegram("🚨 Emergency stop active - all processes halted")
                    emergency_stop_notified = True
                
                # Stop all running processes
                for process_manager in processes.values():
                    if process_manager.running:
                        process_manager.stop()
                
                # Wait for emergency stop to be removed
                while check_emergency_stop() and not shutdown_flag.is_set():
                    time.sleep(10)
                
                if not shutdown_flag.is_set():
                    logger.info("Emergency stop removed, restarting processes")
                    send_telegram("✅ Emergency stop cleared, restarting processes")
                    emergency_stop_notified = False
                    
                    # Restart all processes
                    for process_manager in processes.values():
                        if not shutdown_flag.is_set():
                            process_manager.start()
                            time.sleep(2)  # Stagger restarts
                    continue
            
            # Check health of each process
            for name, process_manager in processes.items():
                if not process_manager.is_healthy() and not shutdown_flag.is_set():
                    logger.warning(f"{name} is unhealthy, restarting...")
                    send_telegram(f"⚠️ {name} crashed, attempting restart...")
                    
                    if not process_manager.restart():
                        logger.error(f"Failed to restart {name}")
                        send_telegram(f"❌ Failed to restart {name} - manual intervention required")
                        
                        # If critical process fails, create emergency stop
                        if name == "ARS-3 Trading Bot":
                            logger.critical("Trading bot failed to restart - creating emergency stop")
                            create_emergency_stop()
            
            time.sleep(30)  # Check every 30 seconds
            
        except Exception as e:
            logger.error(f"Error in process monitoring: {e}")
            time.sleep(60)  # Wait longer on error

def system_health_check():
    """Perform comprehensive system health checks"""
    while not shutdown_flag.is_set():
        try:
            # Check disk space
            try:
                disk_usage = psutil.disk_usage('.')
                free_gb = disk_usage.free / (1024**3)
                
                if free_gb < 1:  # Less than 1GB free
                    logger.warning(f"Low disk space: {free_gb:.1f}GB free")
                    send_telegram(f"⚠️ Low disk space: {free_gb:.1f}GB free")
                elif free_gb < 0.5:  # Less than 500MB - critical
                    logger.critical(f"Critical disk space: {free_gb:.1f}GB free")
                    send_telegram(f"🚨 Critical disk space: {free_gb:.1f}GB free - creating emergency stop")
                    create_emergency_stop()
                    
            except Exception as e:
                logger.debug(f"Disk space check error: {e}")
            
            # Check memory usage
            try:
                memory = psutil.virtual_memory()
                if memory.percent > 95:
                    logger.warning(f"Critical memory usage: {memory.percent:.1f}%")
                    send_telegram(f"🚨 Critical memory usage: {memory.percent:.1f}%")
                elif memory.percent > 85:
                    logger.warning(f"High memory usage: {memory.percent:.1f}%")
            except Exception as e:
                logger.debug(f"Memory check error: {e}")
            
            # Check CPU usage
            try:
                cpu_percent = psutil.cpu_percent(interval=1)
                if cpu_percent > 95:
                    logger.warning(f"High CPU usage: {cpu_percent:.1f}%")
            except Exception as e:
                logger.debug(f"CPU check error: {e}")
            
            # Trading mode notification (periodic reminder)
            current_time = datetime.now()
            if not SIMULATION_MODE and current_time.minute == 0:  # Once per hour
                logger.info("🔴 LIVE TRADING MODE ACTIVE")
                
                # Send daily reminder at 9 AM
                if current_time.hour == 9:
                    send_telegram("🔴 Daily Reminder: LIVE TRADING MODE is active!")
            
            time.sleep(300)  # Check every 5 minutes
            
        except Exception as e:
            logger.error(f"Error in health check: {e}")
            time.sleep(300)

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    send_telegram("🛑 System shutdown initiated - stopping all processes...")
    shutdown_flag.set()
    
    # Stop all processes with timeout
    for name, process_manager in processes.items():
        logger.info(f"Stopping {name}...")
        try:
            process_manager.stop()
        except Exception as e:
            logger.error(f"Error stopping {name}: {e}")
    
    send_telegram("✅ System shutdown completed")
    logger.info("Graceful shutdown completed")
    sys.exit(0)

def main():
    """Main execution function"""
    logger.info("=" * 60)
    logger.info("ARS-3 ENHANCED AUTOSTART SYSTEM v2.0")
    logger.info("=" * 60)
    
    # Check prerequisites
    if not check_prerequisites():
        logger.error("Prerequisites check failed, exiting")
        sys.exit(1)
    
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Remove any existing emergency stop
    remove_emergency_stop()
    
    # Initialize process managers
    processes['bot'] = ProcessManager(
        name="ARS-3 Trading Bot",
        command=f"python \"{BOT_SCRIPT}\"",
        restart_delay=10,
        max_restarts=5
    )
    
    # Only add dashboard if file exists
    if os.path.exists(DASH_SCRIPT):
        processes['dashboard'] = ProcessManager(
            name="ARS-3 Dashboard",
            command=f"streamlit run \"{DASH_SCRIPT}\" --server.headless true --server.port 8501",
            restart_delay=5,
            max_restarts=10
        )
        logger.info("Dashboard component included")
    else:
        logger.info("Dashboard component skipped (file not found)")
    
    # Send startup notification
    mode = "SIMULATION" if SIMULATION_MODE else "LIVE TRADING"
    startup_msg = f"🚀 ARS-3 System Starting in {mode} MODE\nComponents: {len(processes)}"
    send_telegram(startup_msg)
    
    # Start processes
    failed_starts = []
    for name, process_manager in processes.items():
        if not process_manager.start():
            failed_starts.append(name)
            logger.error(f"Failed to start {name}")
        else:
            time.sleep(3)  # Stagger startup to avoid resource conflicts
    
    if failed_starts:
        if "ARS-3 Trading Bot" in failed_starts:
            logger.critical("Trading bot failed to start - cannot continue")
            send_telegram(f"❌ System startup failed: Trading bot could not start")
            sys.exit(1)
        else:
            logger.warning(f"Some non-critical components failed to start: {failed_starts}")
            send_telegram(f"⚠️ Some components failed to start: {failed_starts}")
    
    # Start monitoring threads
    monitor_thread = threading.Thread(target=monitor_processes, daemon=True)
    monitor_thread.start()
    
    health_thread = threading.Thread(target=system_health_check, daemon=True)
    health_thread.start()
    
    success_msg = "✅ ARS-3 System fully operational with enhanced monitoring"
    send_telegram(success_msg)
    logger.info("✅ All systems operational. Monitoring active.")
    logger.info("Controls:")
    logger.info("  Emergency stop: touch EMERGENCY_STOP")
    logger.info("  Shutdown: Ctrl+C or kill signal")
    logger.info("  Logs: tail -f ars3_autostart.log")
    
    try:
        # Keep main thread alive and responsive
        while not shutdown_flag.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        signal_handler(signal.SIGINT, None)

if __name__ == "__main__":
    main()