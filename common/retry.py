import functools
import logging
import time

log = logging.getLogger(__name__)


def retry(times=3, base_delay=1.0, on=(Exception,)):
    """Retry with exponential backoff on the given exceptions; re-raise the last one."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(times):
                try:
                    return fn(*args, **kwargs)
                except on as e:
                    if attempt == times - 1:
                        raise
                    delay = base_delay * 2**attempt
                    log.warning("%s failed (%s); retrying in %.1fs", fn.__name__, e, delay)
                    time.sleep(delay)

        return wrapper

    return decorator
