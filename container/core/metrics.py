import threading

try:
    _LOCK = threading.Lock()
except Exception:  # pragma: no cover
    _LOCK = None  # type: ignore[assignment]

HTTP = {}              # type: dict[tuple[str, int], int]
SERIAL_CMD = {}        # type: dict[tuple[str, str], int]
SERIAL_OPEN_FAIL = 0   # type: int


def m_inc_http(method: str, code: int) -> None:
    key = (str(method).upper(), int(code))
    global HTTP
    if _LOCK:
        with _LOCK:
            HTTP[key] = HTTP.get(key, 0) + 1
    else:
        HTTP[key] = HTTP.get(key, 0) + 1


def m_inc_serial_cmd(kind: str, status: str) -> None:
    key = (str(kind), str(status))
    global SERIAL_CMD
    if _LOCK:
        with _LOCK:
            SERIAL_CMD[key] = SERIAL_CMD.get(key, 0) + 1
    else:
        SERIAL_CMD[key] = SERIAL_CMD.get(key, 0) + 1


def m_inc_serial_open_fail() -> None:
    global SERIAL_OPEN_FAIL
    if _LOCK:
        with _LOCK:
            SERIAL_OPEN_FAIL += 1
    else:
        SERIAL_OPEN_FAIL += 1

