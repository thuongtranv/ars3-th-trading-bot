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

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('ars3_autostart.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_SCRIPT = os.path.join(BASE_DIR, "ars3_live_advanced_final.py")
DASH_SCRIPT = os.path.join(BASE_DIR, "streamlit_ars3_advanced.py")
CONFIG_FILE = os.path.join(BASE_DIR, "ars3_config.yaml")
EMERGENCY_STOP_FILE = os.path.join(BASE_DIR, "EMERGENCY_STOP")

# Load configuration
try:
    with open(CONFIG_FILE, "r") as f:
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
        
    def start(self):
        """Start the process"""
        try:
            if self.process and self.process.poll() is None:
                logger.warning(f"{self.name} is already running")
                return True
            
            logger.info(f"Starting {self.name}...")
            send_telegram(f"🚀 Starting {self.name}...")
            
            if platform.system() == "Windows":
                self.process = subprocess.Popen(
                    self.command,
                    shell=True,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
            else:
                self.process = subprocess.Popen(
                    self.command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
            
            self.running = True
            self.last_restart = datetime.now()
            
            # Reset restart count if process has been running for more than 1 hour
            if self.restart_count > 0 and self.last_restart:
                if (datetime.now() - self.last_restart).total_seconds() > 3600:
                    self.restart_count = 0
            
            logger.info(f"✅ {self.name} started with PID {self.process.pid}")
            return True
            
        except Exception as e:
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
            
            # Try graceful shutdown first
            if platform.system() == "Windows":
                self.process.terminate()
            else:
                self.process.send_signal(signal.SIGTERM)
            
            # Wait for graceful shutdown
            try:
                self.process.wait(timeout=10)
                logger.info(f"✅ {self.name} stopped gracefully")
            except subprocess.TimeoutExpired:
                logger.warning(f"Force killing {self.name}...")
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
                cpu_percent = proc.cpu_percent()
                memory_mb = proc.memory_info().rss / 1024 / 1024
                
                # Alert if consuming too much resources
                if cpu_percent > 90:
                    logger.warning(f"{self.name} using {cpu_percent:.1f}% CPU")
                if memory_mb > 1000:  # 1GB
                    logger.warning(f"{self.name} using {memory_mb:.1f}MB memory")
                
                return True
            except psutil.NoSuchProcess:
                return False
                
        except Exception as e:
            logger.error(f"Error checking {self.name} health: {e}")
            return False
    
    def restart(self):
        """Restart the process"""
        if self.restart_count >= self.max_restarts:
            logger.error(f"{self.name} has reached max restart attempts ({self.max_restarts})")
            send_telegram(f"🚨 {self.name} failed permanently after {self.max_restarts} attempts")
            return False
        
        self.restart_count += 1
        logger.info(f"Restarting {self.name} (attempt {self.restart_count}/{self.max_restarts})")
        
        self.stop()
        time.sleep(self.restart_delay)
        
        if shutdown_flag.is_set():
            return False
        
        return self.start()

def send_telegram(msg):
    """Send telegram message with retry logic"""
    if not BOT_TOKEN or not CHAT_ID:
        return
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            response = requests.post(
                url, 
                data={"chat_id": CHAT_ID, "text": f"🤖 ARS-3 System:\n{msg}"}, 
                timeout=10
            )
            if response.status_code == 200:
                return
        except Exception as e:
            logger.error(f"Telegram send error (attempt {attempt + 1}): {e}")
        
        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)

def check_prerequisites():
    """Check if all required files exist"""
    required_files = [BOT_SCRIPT, DASH_SCRIPT, CONFIG_FILE]
    missing_files = [f for f in required_files if not os.path.exists(f)]
    
    if missing_files:
        logger.error(f"Missing required files: {missing_files}")
        send_telegram(f"❌ Missing files: {missing_files}")
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
    except Exception as e:
        logger.error(f"Failed to create emergency stop file: {e}")

def remove_emergency_stop():
    """Remove emergency stop file"""
    try:
        if os.path.exists(EMERGENCY_STOP_FILE):
            os.remove(EMERGENCY_STOP_FILE)
            logger.info("Emergency stop file removed")
            send_telegram("✅ Emergency stop deactivated")
    except Exception as e:
        logger.error(f"Failed to remove emergency stop file: {e}")

def monitor_processes():
    """Monitor and restart processes as needed"""
    logger.info("Starting process monitoring...")
    
    while not shutdown_flag.is_set():
        try:
            # Check for emergency stop
            if check_emergency_stop():
                logger.warning("Emergency stop detected, halting all processes")
                for process_manager in processes.values():
                    if process_manager.running:
                        process_manager.stop()
                
                # Wait for emergency stop to be removed
                while check_emergency_stop() and not shutdown_flag.is_set():
                    time.sleep(10)
                
                if not shutdown_flag.is_set():
                    logger.info("Emergency stop removed, restarting processes")
                    send_telegram("✅ Emergency stop cleared, restarting processes")
                    for process_manager in processes.values():
                        process_manager.start()
                    continue
            
            # Check health of each process
            for name, process_manager in processes.items():
                if not process_manager.is_healthy() and not shutdown_flag.is_set():
                    logger.warning(f"{name} is unhealthy, restarting...")
                    send_telegram(f"⚠️ {name} crashed, restarting...")
                    
                    if not process_manager.restart():
                        logger.error(f"Failed to restart {name}")
                        send_telegram(f"❌ Failed to restart {name}")
            
            time.sleep(30)  # Check every 30 seconds
            
        except Exception as e:
            logger.error(f"Error in process monitoring: {e}")
            time.sleep(60)  # Wait longer on error

def system_health_check():
    """Perform system health checks"""
    while not shutdown_flag.is_set():
        try:
            # Check disk space
            disk_usage = psutil.disk_usage('.')
            free_gb = disk_usage.free / (1024**3)
            if free_gb < 1:  # Less than 1GB free
                logger.warning(f"Low disk space: {free_gb:.1f}GB free")
                send_telegram(f"⚠️ Low disk space: {free_gb:.1f}GB free")
            
            # Check memory usage
            memory = psutil.virtual_memory()
            if memory.percent > 90:
                logger.warning(f"High memory usage: {memory.percent:.1f}%")
                send_telegram(f"⚠️ High memory usage: {memory.percent:.1f}%")
            
            # Check if trading in live mode
            if not SIMULATION_MODE:
                logger.info("🔴 LIVE TRADING MODE ACTIVE")
            
            time.sleep(300)  # Check every 5 minutes
            
        except Exception as e:
            logger.error(f"Error in health check: {e}")
            time.sleep(300)

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    send_telegram("🛑 System shutdown initiated")
    shutdown_flag.set()
    
    # Stop all processes
    for name, process_manager in processes.items():
        logger.info(f"Stopping {name}...")
        process_manager.stop()
    
    logger.info("Graceful shutdown completed")
    sys.exit(0)

def main():
    """Main execution function"""
    logger.info("=" * 60)
    logger.info("ARS-3 ENHANCED AUTOSTART SYSTEM")
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
    
    processes['dashboard'] = ProcessManager(
        name="ARS-3 Dashboard",
        command=f"streamlit run \"{DASH_SCRIPT}\" --server.headless true --server.port 8501",
        restart_delay=5,
        max_restarts=10
    )
    
    # Send startup notification
    mode = "SIMULATION" if SIMULATION_MODE else "LIVE TRADING"
    send_telegram(f"🚀 ARS-3 System Starting in {mode} MODE")
    
    # Start processes
    for name, process_manager in processes.items():
        if not process_manager.start():
            logger.error(f"Failed to start {name}, aborting")
            send_telegram(f"❌ System startup failed: {name} could not start")
            sys.exit(1)
        time.sleep(2)  # Stagger startup
    
    # Start monitoring threads
    monitor_thread = threading.Thread(target=monitor_processes, daemon=True)
    monitor_thread.start()
    
    health_thread = threading.Thread(target=system_health_check, daemon=True)
    health_thread.start()
    
    send_telegram("✅ ARS-3 System fully operational with enhanced monitoring")
    logger.info("✅ All systems operational. Monitoring active.")
    logger.info("To emergency stop: touch EMERGENCY_STOP")
    logger.info("To shutdown: Ctrl+C or kill signal")
    
    try:
        # Keep main thread alive
        while not shutdown_flag.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        signal_handler(signal.SIGINT, None)

if __name__ == "__main__":
    main()