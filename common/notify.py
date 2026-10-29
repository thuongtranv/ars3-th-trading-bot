import logging
import threading
import time

import requests

from .env import get_env

log = logging.getLogger(__name__)
_lock = threading.Lock()
_last_sent = 0.0


def _post(method, data, files=None, min_interval=0.0):
    """POST to the Telegram Bot API; returns True on success. Never raises."""
    global _last_sent
    token, chat_id = get_env("TELEGRAM_BOT_TOKEN"), get_env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.warning("Telegram credentials not configured")
        return False
    with _lock:
        time.sleep(max(0.0, min_interval - (time.time() - _last_sent)))
        _last_sent = time.time()
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/{method}",
            data={"chat_id": chat_id, **data},
            files=files,
            timeout=15,
        )
    except requests.RequestException as e:
        log.error("Telegram request failed: %s", e)
        return False
    if r.status_code != 200:
        log.warning("Telegram API error: %s", r.text)
    return r.status_code == 200


def send_telegram(text, prefix=""):
    return _post("sendMessage", {"text": f"{prefix}{text}"}, min_interval=1.0)


def send_telegram_photo(path):
    with open(path, "rb") as f:
        return _post("sendPhoto", {}, files={"photo": f})
