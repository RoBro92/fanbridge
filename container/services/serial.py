import os, glob, logging
from typing import Protocol, runtime_checkable, Any

try:
    import serial  # type: ignore
    from serial.tools import list_ports  # type: ignore
except Exception:  # pragma: no cover
    serial = None  # type: ignore[assignment]
    list_ports = None  # type: ignore[assignment]


@runtime_checkable
class SerialProto(Protocol):
    @property
    def port(self) -> str | None: ...
    def write(self, b: bytes | bytearray | memoryview, /) -> int | None: ...
    def flush(self) -> None: ...
    def readline(self) -> bytes: ...
    def close(self) -> None: ...
    def reset_input_buffer(self) -> None: ...
    def reset_output_buffer(self) -> None: ...


class _Ctx:
    baud: int = 115200
    preferred: str = ""
    logger: logging.Logger | None = None
    dbg_should = None  # callable(tag: str, interval_s: int) -> bool
    inc_open_fail = None  # callable() -> None


_CTX = _Ctx()
_SERIAL_LAST_GOOD: str | None = None


def init(*, baud: int, preferred: str, logger: logging.Logger, dbg_should, inc_open_fail) -> None:
    _CTX.baud = int(baud)
    _CTX.preferred = preferred or ""
    _CTX.logger = logger
    _CTX.dbg_should = dbg_should
    _CTX.inc_open_fail = inc_open_fail


def _log() -> logging.Logger:
    return _CTX.logger or logging.getLogger("fanbridge")


def _unique_order(seq):
    seen = set()
    out = []
    for x in seq:
        if x and x not in seen:
            out.append(x)
            seen.add(x)
    return out


def list_serial_ports():
    candidates = []
    candidates.extend(sorted(glob.glob("/dev/serial/by-id/*")))
    candidates.extend(sorted(glob.glob("/dev/ttyACM*")))
    candidates.extend(sorted(glob.glob("/dev/ttyUSB*")))
    if list_ports:
        try:
            for p in list_ports.comports():
                dev = p.device or ""
                if dev.startswith("/dev/serial/by-id/") or dev.startswith("/dev/ttyACM") or dev.startswith("/dev/ttyUSB"):
                    candidates.append(dev)
        except Exception:
            pass
    return _unique_order(candidates)


def probe_serial_open(port: str, baud: int | None = None):
    if not port:
        return False, "no port specified"
    if port.startswith("/dev/ttyS"):
        return False, "not a USB CDC device"
    if serial is None:
        return False, "pyserial not available"
    try:
        s = serial.Serial(port=port, baudrate=baud or _CTX.baud, timeout=0.2)
        try:
            ok = True
        finally:
            s.close()
        return ok, "ok"
    except Exception as e:
        msg = str(e)
        try:
            if port.startswith("/dev/tty."):
                cu_port = "/dev/cu." + port.split("/dev/tty.", 1)[1]
                if os.path.exists(cu_port):
                    s2 = serial.Serial(port=cu_port, baudrate=baud or _CTX.baud, timeout=0.2)
                    try:
                        ok2 = True
                    finally:
                        s2.close()
                    return ok2, f"ok ({cu_port})"
        except Exception:
            pass
        lower = msg.lower()
        if any(k in lower for k in ("permission", "denied", "operation not permitted")):
            msg = (
                f"{msg} (hint: map the device into the container using --device={port} or Unraid's Device field; "
                "do not bind-mount the TTY. Also map /dev/serial/by-id (ro) and optionally set FANBRIDGE_SERIAL_PORT to the by-id path)"
            )
        try:
            _log().warning("serial open failed | port=%s baud=%s err=%s", port, baud or _CTX.baud, msg)
            if _CTX.inc_open_fail:
                _CTX.inc_open_fail()
        except Exception:
            pass
        return False, msg


def _choose_best_port(candidates: list[str]) -> str:
    byid = [p for p in candidates if "/serial/by-id/" in p]
    if byid:
        return byid[0]
    return candidates[0] if candidates else ""


def preferred_serial_port() -> str:
    ports = list_serial_ports()
    if globals().get("_SERIAL_LAST_GOOD") and globals()["_SERIAL_LAST_GOOD"] in ports:
        return str(globals()["_SERIAL_LAST_GOOD"])  # type: ignore
    if _CTX.preferred and os.path.exists(_CTX.preferred):
        return _CTX.preferred
    return _choose_best_port(ports)


def open_serial(port: str | None = None, baud: int | None = None, timeout: float = 1.0) -> tuple[SerialProto | None, str | None]:
    if serial is None:
        return None, "pyserial not available"
    ports = list_serial_ports()
    cand = []
    if port:
        cand.append(port)
    pref = preferred_serial_port()
    if pref:
        cand.append(pref)
    cand.extend(ports)
    tried = set()
    last_err = None
    for p in _unique_order(cand):
        if not p or p in tried:
            continue
        tried.add(p)
        try:
            s = serial.Serial(port=p, baudrate=baud or _CTX.baud, timeout=timeout)
            s_proto: SerialProto = s
            try:
                s_proto.reset_input_buffer()
                s_proto.reset_output_buffer()
            except Exception:
                pass
            try:
                globals()["_SERIAL_LAST_GOOD"] = getattr(s_proto, "port", p)
            except Exception:
                pass
            return s_proto, None
        except Exception as e:
            last_err = str(e)
            continue
    try:
        _log().warning("serial open failed | port=%s baud=%s err=%s", (port or pref or ""), baud or _CTX.baud, last_err or "no candidates")
    except Exception:
        pass
    return None, (last_err or "no serial ports detected")


def serial_send_line(line: str, expect_reply: bool = True, timeout: float = 1.0) -> dict:
    out = {"ok": False, "port": None, "echo": line, "reply": None, "error": None}
    s, err = open_serial(timeout=timeout)
    if err:
        out["error"] = err
        return out
    if s is None:
        out["error"] = "serial not available"
        return out
    out["port"] = (s.port if hasattr(s, "port") else None)
    try:
        payload = (line or "").strip() + "\n"
        data = payload.encode("utf-8", errors="ignore")
        s.write(data)
        s.flush()
        if expect_reply:
            resp = s.readline().decode("utf-8", errors="ignore").strip()
            out["reply"] = resp if resp else None
        out["ok"] = True
        return out
    except Exception as e:
        out["error"] = str(e)
        return out
    finally:
        try:
            s.close()
        except Exception:
            pass


def serial_set_pwm_percent(value: Any) -> dict:
    if not isinstance(value, (int, float, str)):
        return {"ok": False, "error": "invalid value"}
    try:
        v = int(value)
    except Exception:
        return {"ok": False, "error": "invalid value"}
    if v < 0: v = 0
    if v > 100: v = 100
    res = serial_send_line(str(v), expect_reply=True)
    res["value"] = v
    return res


def get_serial_status(full: bool = True):
    ports = list_serial_ports()
    preferred = preferred_serial_port()
    available = bool(ports)
    connected = False
    message = "no ports detected"

    try:
        if (not available) and _CTX.preferred and not os.path.exists(_CTX.preferred):
            message = f"preferred port not present: {_CTX.preferred}"
    except Exception:
        pass

    if preferred:
        ok, msg = probe_serial_open(preferred, _CTX.baud)
        connected = ok
        message = msg
        if not ok and available:
            for p in ports:
                if p == preferred:
                    continue
                o2, m2 = probe_serial_open(p, _CTX.baud)
                if o2:
                    preferred = p
                    connected = True
                    message = m2
                    try:
                        globals()["_SERIAL_LAST_GOOD"] = p
                    except Exception:
                        pass
                    break
        if not connected:
            try:
                lvl = logging.WARNING if any(s in str(message).lower() for s in ("denied", "permission", "not opened", "busy", "no such device")) else logging.INFO
                _log().log(
                    lvl,
                    "serial not connected | port=%s baud=%s reason=%s (map device and grant permissions)",
                    preferred, _CTX.baud, message,
                )
            except Exception:
                pass
    elif available:
        message = "ports detected but none selected"

    data = {
        "preferred": preferred,
        "ports": ports if full else None,
        "available": available,
        "connected": (connected and not (preferred or "").startswith("/dev/ttyS")),
        "baud": _CTX.baud,
        "message": message,
    }
    if not full:
        data.pop("ports", None)
    try:
        if _CTX.dbg_should and _CTX.dbg_should("serial", 8):
            _log().debug(
                "serial | preferred=%s available=%s connected=%s baud=%s msg=%s ports=%s",
                data.get("preferred"), data.get("available"), data.get("connected"), data.get("baud"), data.get("message"), len(ports)
            )
    except Exception:
        pass
    return data


def usb_info_for_port(port: str | None) -> dict:
    info: dict = {}
    if not port:
        return info
    if list_ports:
        try:
            for p in list_ports.comports():
                if p.device == port:
                    info = {
                        "device": p.device,
                        "vid": getattr(p, "vid", None),
                        "pid": getattr(p, "pid", None),
                        "manufacturer": getattr(p, "manufacturer", None),
                        "product": getattr(p, "product", None),
                        "serial_number": getattr(p, "serial_number", None),
                        "hwid": getattr(p, "hwid", None),
                        "location": getattr(p, "location", None),
                    }
                    break
        except Exception:
            pass
    return info

