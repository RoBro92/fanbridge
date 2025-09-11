import logging, sys, time, os
from collections import deque
from typing import Any

try:
    from collections import deque as _deque  # noqa: F401
    import threading
    LOG_RING = deque(maxlen=2000)
    LOG_LOCK = threading.Lock()
    _LOG_NEXT_ID = 1
except Exception:  # pragma: no cover - very defensive
    LOG_RING = []  # type: ignore[assignment]
    LOG_LOCK = None  # type: ignore[assignment]
    _LOG_NEXT_ID = 1


class RingBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:  # type: ignore[override]
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(getattr(record, 'message', ''))
        try:
            global _LOG_NEXT_ID
            item = {
                "id": int(_LOG_NEXT_ID),
                "ts": int(getattr(record, 'created', time.time())),
                "level": str(record.levelname),
                "name": str(record.name),
                "msg": msg,
            }
            _LOG_NEXT_ID += 1
            if LOG_LOCK is not None:
                with LOG_LOCK:
                    LOG_RING.append(item)
            else:
                LOG_RING.append(item)  # type: ignore[union-attr]
        except Exception:
            pass


def setup_logging() -> None:
    lvl_name = os.environ.get("FANBRIDGE_LOG_LEVEL") or (os.environ.get("FLASK_DEBUG") and "DEBUG") or "INFO"
    level = getattr(logging, str(lvl_name).upper(), logging.INFO)

    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(stream=sys.stdout)
        fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        handler.setFormatter(logging.Formatter(fmt))
        root.addHandler(handler)
    try:
        if not any(isinstance(h, RingBufferHandler) for h in root.handlers):
            root.addHandler(RingBufferHandler())
    except Exception:
        pass
    root.setLevel(level)

    try:
        if os.environ.get("FLASK_DEBUG"):
            logging.getLogger("werkzeug").setLevel(logging.INFO)
        else:
            logging.getLogger("werkzeug").setLevel(logging.WARNING)
    except Exception:
        pass
    logging.getLogger("urllib3").setLevel(logging.WARNING)
