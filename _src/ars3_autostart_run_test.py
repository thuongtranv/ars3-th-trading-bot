import os
import subprocess
import time
import psutil
import logging

# --- CẤU HÌNH ---
# Tên các file script chính của bot và dashboard
BOT_SCRIPT_NAMES = ["ars3_live.py", "ars3_streamlit.py", "ars3_autostart.py"]

# Danh sách các file log và dữ liệu cần xóa trước mỗi lần chạy
FILES_TO_CLEAN = [
    "..\\logs\\ars3_bot.log",
    "..\\logs\\ars3_autostart.log",
    "..\\trading\\trades_live.csv",
    "..\\trading\\equity_live.csv",
    "position.json",
    "..\\__pycache__\\ars3_broker_ccxt.cpython-313.pyc",
    "..\\__pycache__\\ars3_strategy_rs75.cpython-313.pyc",
    "..\\__pycache__\\ars3_strategy_trend.cpython-313.pyc",
    "..\\__pycache__\\ars3_strategy_utils.cpython-313.pyc",
    # Thêm file .log của các thư viện nếu có, ví dụ:
    # "werkzeug.log" 
]

# File để khởi chạy lại
AUTOSTART_SCRIPT = "ars3_live.py"

# --- THIẾT LẬP LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    handlers=[logging.StreamHandler()]
)

def kill_existing_processes():
    """
    Tìm và dừng tất cả các tiến trình Python đang chạy các script của bot.
    """
    logging.info("--- Bước 1: Dừng các tiến trình bot đang chạy ---")
    current_pid = os.getpid()
    processes_killed = 0

    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            # Chỉ kiểm tra các tiến trình Python
            if 'python' in proc.info['name'].lower():
                cmdline = proc.info['cmdline']
                if not cmdline or len(cmdline) < 2:
                    continue

                # Kiểm tra xem script có nằm trong danh sách cần kill không
                script_path = cmdline[1]
                for script_name in BOT_SCRIPT_NAMES:
                    if script_name in script_path and proc.pid != current_pid:
                        logging.warning(f"Đang dừng tiến trình '{' '.join(cmdline)}' (PID: {proc.pid})...")
                        p = psutil.Process(proc.pid)
                        p.terminate()  # Gửi tín hiệu dừng nhẹ nhàng trước
                        processes_killed += 1
                        break # Chuyển sang process tiếp theo
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception as e:
            logging.error(f"Lỗi khi dừng tiến trình {proc.pid}: {e}")

    if processes_killed > 0:
        logging.info(f"Đã dừng {processes_killed} tiến trình. Chờ 3 giây để hệ thống ổn định...")
        time.sleep(3)
    else:
        logging.info("Không tìm thấy tiến trình nào cần dừng.")


def clean_log_and_data_files():
    """
    Xóa các file log và dữ liệu cũ để bắt đầu một phiên test mới.
    """
    logging.info("--- Bước 2: Dọn dẹp file log và dữ liệu cũ ---")
    for filename in FILES_TO_CLEAN:
        try:
            if os.path.exists(filename):
                os.remove(filename)
                logging.info(f"Đã xóa file: {filename}")
            else:
                logging.debug(f"File không tồn tại, bỏ qua: {filename}")
        except OSError as e:
            logging.error(f"Lỗi khi xóa file {filename}: {e}")

def start_autostart_script():
    """
    Khởi chạy lại file ars3_autostart.py trong một tiến trình mới.
    """
    logging.info(f"--- Bước 3: Khởi chạy lại {AUTOSTART_SCRIPT} ---")
    try:
        # Sử dụng Popen để chạy script trong một tiến trình riêng biệt
        # và không bị tắt khi script này kết thúc.
        if os.name == 'nt': # Windows
             subprocess.Popen(
                f"python {AUTOSTART_SCRIPT}", 
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else: # Linux/macOS
             subprocess.Popen(
                ["python3", AUTOSTART_SCRIPT],
                start_new_session=True
            )
        logging.info(f"Đã gửi yêu cầu khởi chạy {AUTOSTART_SCRIPT}. Vui lòng kiểm tra cửa sổ terminal mới.")
    except FileNotFoundError:
        logging.critical(f"LỖI: Không tìm thấy file '{AUTOSTART_SCRIPT}'. Vui lòng kiểm tra lại đường dẫn.")
    except Exception as e:
        logging.critical(f"Lỗi nghiêm trọng khi khởi chạy lại autostart: {e}")

if __name__ == "__main__":
    logging.info("=================================================")
    logging.info(" BẮT ĐẦU KỊCH BẢN CHẠY LẠI THỬ NGHIỆM ARS-3 ")
    logging.info("=================================================")
    
    kill_existing_processes()
    clean_log_and_data_files()
    start_autostart_script()
    
    logging.info("--- Kịch bản đã hoàn thành ---")