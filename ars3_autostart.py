import os
import platform
import subprocess
import time
import threading

from common.notify import send_telegram

# ---- Paths ----
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_SCRIPT = os.path.join(BASE_DIR, "ars3_live_advanced_final.py")
DASH_SCRIPT = os.path.join(BASE_DIR, "streamlit_ars3_advanced.py")

# ---- Function to start process with auto-restart + alert ----
def start_process(command, name):
    while True:
        try:
            print(f"🌟 Starting {name}...")
            send_telegram(f"🌟 ARS-3: Starting {name}...")
            proc = subprocess.Popen(command, shell=True)
            proc.wait()
            print(f"⚠️ {name} exited! Restarting in 5s...")
            send_telegram(f"⚠️ ARS-3: {name} crashed! Restarting in 5s...")
            time.sleep(5)
        except Exception as e:
            print(f"❌ Error in {name}: {e}, retry in 5s")
            send_telegram(f"❌ ARS-3 Error in {name}: {e}, retry in 5s")
            time.sleep(5)

# ---- Run bot & dashboard ----
def run_full_stack():
    if platform.system() == "Windows":
        # Windows: run each in separate CMD window
        threading.Thread(target=start_process, args=(f"python \"{BOT_SCRIPT}\"", "ARS-3 Bot")).start()
        time.sleep(2)
        threading.Thread(target=start_process, args=(f"streamlit run \"{DASH_SCRIPT}\"", "ARS-3 Dashboard")).start()
    else:
        # Linux / Mac: run in threads
        threading.Thread(target=start_process, args=(["python3", BOT_SCRIPT], "ARS-3 Bot")).start()
        time.sleep(2)
        threading.Thread(target=start_process, args=(["streamlit", "run", DASH_SCRIPT], "ARS-3 Dashboard")).start()

if __name__ == "__main__":
    print("🚀 Starting ARS-3 Full Stack with Auto-Restart + Telegram Alert...")
    send_telegram("🚀 ARS-3 Full Stack starting with Auto-Restart + Telegram Alert")
    run_full_stack()
    print("✅ ARS-3 Auto-Restart + Alerts enabled. Bot & Dashboard will restart automatically if crash.")
