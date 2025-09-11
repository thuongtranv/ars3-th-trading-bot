#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Script khắc phục nhanh lỗi dashboard ARS-3
Chạy script này trước khi start autostart
"""

import os
import sys
import subprocess
import psutil
import socket
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def kill_streamlit_processes():
    """Kill tất cả Streamlit processes"""
    killed_count = 0
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if proc.info['name'] and 'python' in proc.info['name'].lower():
                    cmdline = proc.info.get('cmdline', [])
                    if any('streamlit' in str(cmd) for cmd in cmdline):
                        logger.info(f"Killing Streamlit process {proc.info['pid']}")
                        proc.kill()
                        killed_count += 1
                        time.sleep(0.5)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception as e:
        logger.error(f"Error killing processes: {e}")
    
    logger.info(f"Killed {killed_count} Streamlit processes")
    return killed_count

def check_port_availability(port):
    """Kiểm tra port có khả dụng không"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('localhost', port))
            return True
    except OSError:
        return False

def find_available_port(start_port=8501, max_attempts=20):
    """Tìm port khả dụng"""
    for port in range(start_port, start_port + max_attempts):
        if check_port_availability(port):
            logger.info(f"Found available port: {port}")
            return port
    return None

def check_dependencies():
    """Kiểm tra dependencies"""
    required_packages = [
        'streamlit', 'pandas', 'plotly', 'numpy', 'yaml'
    ]
    
    missing = []
    for package in required_packages:
        try:
            __import__(package)
            logger.info(f"✅ {package} - OK")
        except ImportError:
            missing.append(package)
            logger.error(f"❌ {package} - MISSING")
    
    return missing

def test_dashboard_manual():
    """Test chạy dashboard thủ công"""
    dashboard_script = "ars3_streamlit.py"
    
    if not os.path.exists(dashboard_script):
        logger.error(f"Dashboard script not found: {dashboard_script}")
        return False
    
    available_port = find_available_port()
    if not available_port:
        logger.error("No available ports found")
        return False
    
    try:
        logger.info(f"Testing dashboard on port {available_port}...")
        cmd = [
            sys.executable, "-m", "streamlit", "run", 
            dashboard_script, 
            "--server.headless", "true",
            "--server.port", str(available_port),
            "--server.runOnSave", "false"
        ]
        
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(5)  # Cho dashboard khởi động
        
        if proc.poll() is None:  # Process vẫn chạy
            logger.info("✅ Dashboard test successful")
            proc.terminate()
            proc.wait()
            return True
        else:
            stdout, stderr = proc.communicate()
            logger.error(f"Dashboard failed: {stderr.decode()}")
            return False
            
    except Exception as e:
        logger.error(f"Dashboard test error: {e}")
        return False

def check_file_permissions():
    """Kiểm tra quyền truy cập files"""
    files_to_check = [
        "trades_live.csv", "equity_live.csv", "position.json", 
        "ars3_bot.log", "ars3_config.yaml"
    ]
    
    issues = []
    for filename in files_to_check:
        if os.path.exists(filename):
            try:
                # Test read
                with open(filename, 'r') as f:
                    f.read(100)  # Read first 100 chars
                logger.info(f"✅ {filename} - readable")
                
                # Test write (if not critical file)
                if filename not in ['ars3_config.yaml']:
                    with open(filename, 'a') as f:
                        pass  # Just test opening for append
                    logger.info(f"✅ {filename} - writable")
                    
            except PermissionError:
                issues.append(f"{filename} - permission denied")
                logger.error(f"❌ {filename} - permission denied")
            except Exception as e:
                issues.append(f"{filename} - {str(e)}")
                logger.error(f"❌ {filename} - {e}")
        else:
            logger.info(f"ℹ️ {filename} - not found (will be created)")
    
    return issues

def main():
    logger.info("=" * 50)
    logger.info("ARS-3 DASHBOARD DIAGNOSTIC & FIX")
    logger.info("=" * 50)
    
    # 1. Kill existing processes
    logger.info("Step 1: Cleaning up existing processes...")
    kill_streamlit_processes()
    time.sleep(2)
    
    # 2. Check port availability
    logger.info("Step 2: Checking port availability...")
    if check_port_availability(8501):
        logger.info("✅ Port 8501 available")
    else:
        logger.warning("⚠️ Port 8501 in use")
        available_port = find_available_port()
        if available_port:
            logger.info(f"Alternative port found: {available_port}")
        else:
            logger.error("❌ No available ports")
            return False
    
    # 3. Check dependencies
    logger.info("Step 3: Checking dependencies...")
    missing = check_dependencies()
    if missing:
        logger.error(f"❌ Missing packages: {missing}")
        logger.info("Install with: pip install " + " ".join(missing))
        return False
    
    # 4. Check file permissions
    logger.info("Step 4: Checking file permissions...")
    perm_issues = check_file_permissions()
    if perm_issues:
        logger.warning(f"⚠️ Permission issues: {perm_issues}")
    
    # 5. Test dashboard
    logger.info("Step 5: Testing dashboard...")
    if test_dashboard_manual():
        logger.info("✅ Dashboard test passed")
    else:
        logger.error("❌ Dashboard test failed")
        return False
    
    logger.info("=" * 50)
    logger.info("✅ ALL CHECKS PASSED - Dashboard should work now")
    logger.info("=" * 50)
    
    return True

if __name__ == "__main__":
    success = main()
    if not success:
        logger.error("❌ Some issues found. Fix them before running autostart.")
        sys.exit(1)
    else:
        logger.info("🚀 Ready to start autostart system!")