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
    def __init__(self, port: str, baud: int):
        self.preferred = port
        self.baud = baud
        self.last_good: str | None = None

_CTXS: dict[str, _Ctx] = {}

_GLOBAL_LOGGER: logging.Logger | None = None
_GLOBAL_DBG_SHOULD = None
_GLOBAL_INC_OPEN_FAIL = None

def init(*, logger: logging.Logger, dbg_should, inc_open_fail) -> None:
    global _GLOBAL_LOGGER, _GLOBAL_DBG_SHOULD, _GLOBAL_INC_OPEN_FAIL
    _GLOBAL_LOGGER = logger
    _GLOBAL_DBG_SHOULD = dbg_should
    _GLOBAL_INC_OPEN_FAIL = inc_open_fail

def register_controller(cid: str, port: str, baud: int) -> None:
    if cid in _CTXS:
        _CTXS[cid].preferred = port
        _CTXS[cid].baud = baud
    else:
        _CTXS[cid] = _Ctx(port, baud)

def unregister_controller(cid: str) -> None:
    if cid in _CTXS:
        del _CTXS[cid]

def _log() -> logging.Logger:
    return _GLOBAL_LOGGER or logging.getLogger("fanbridge")


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
    candidates.extend(sorted(glob.glob("/dev/pts/*")))
    candidates.extend(sorted(glob.glob("/tmp/ttyFAN*")))
    if list_ports:
        try:
            for p in list_ports.comports():
                dev = p.device or ""
                if dev.startswith("/dev/serial/by-id/") or dev.startswith("/dev/ttyACM") or dev.startswith("/dev/ttyUSB"):
                    candidates.append(dev)
        except Exception:
            pass
    return _unique_order(candidates)


def probe_serial_open(port: str, baud: int):
    if not port:
        return False, "no port specified"
    if port.startswith("/dev/ttyS"):
        return False, "not a USB CDC device"
    if serial is None:
        return False, "pyserial not available"
    try:
        s = serial.Serial(port=port, baudrate=baud, timeout=0.2)
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
                    s2 = serial.Serial(port=cu_port, baudrate=baud, timeout=0.2)
                    try:
                        ok2 = True
                    finally:
                        s2.close()
                    return ok2, f"ok ({cu_port})"
        except Exception:
            pass
        lower = msg.lower()
        if any(k in lower for k in ("permission", "denied", "operation not permitted")):
            msg = f"{msg} (hint: map the device into the container using --device={port})"
        try:
            _log().warning("serial open failed | port=%s baud=%s err=%s", port, baud, msg)
            if _GLOBAL_INC_OPEN_FAIL:
                _GLOBAL_INC_OPEN_FAIL()
        except Exception:
            pass
        return False, msg


def identify_port(port: str, timeout: float = 0.5) -> str:
    if serial is None:
        return "unknown"
    try:
        s = serial.Serial(port=port, baudrate=115200, timeout=timeout)
        try:
            s.reset_input_buffer()
            s.reset_output_buffer()
            s.write(b"ID?\n")
            s.flush()
            resp = s.readline().decode("utf-8", errors="ignore").strip()
            if "FANBRIDGE_OFFICIAL" in resp:
                return "official"
            if "FANBRIDGE_DIY" in resp:
                return "diy"
        finally:
            s.close()
    except Exception:
        pass
    return "unknown"

def open_serial(cid: str, timeout: float = 1.0) -> tuple[SerialProto | None, str | None]:
    if serial is None:
        return None, "pyserial not available"
    ctx = _CTXS.get(cid)
    if not ctx:
        return None, "unknown controller id"
    port = ctx.preferred
    if not port:
        return None, "no port configured"
        
    try:
        s = serial.Serial(port=port, baudrate=ctx.baud, timeout=timeout)
        s_proto: SerialProto = s
        try:
            s_proto.reset_input_buffer()
            s_proto.reset_output_buffer()
        except Exception:
            pass
        ctx.last_good = getattr(s_proto, "port", port)
        return s_proto, None
    except Exception as e:
        last_err = str(e)
        try:
            _log().warning("serial open failed | cid=%s port=%s baud=%s err=%s", cid, port, ctx.baud, last_err)
            if _GLOBAL_INC_OPEN_FAIL:
                _GLOBAL_INC_OPEN_FAIL()
        except Exception:
            pass
        return None, last_err


def serial_send_line(cid: str, line: str, expect_reply: bool = True, timeout: float = 1.0) -> dict:
    out = {"ok": False, "port": None, "echo": line, "reply": None, "error": None}
    s, err = open_serial(cid, timeout=timeout)
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
        _log().debug("Serial TX [%s]: %s", cid, (line or "").strip())
        s.write(data)
        s.flush()
        if expect_reply:
            resp = s.readline().decode("utf-8", errors="ignore").strip()
            out["reply"] = resp if resp else None
            _log().debug("Serial RX [%s]: %s", cid, out["reply"])
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


def serial_set_pwm_percent(cid: str, value: Any) -> dict:
    if not isinstance(value, (int, float, str)):
        return {"ok": False, "error": "invalid value"}
    try:
        v = int(value)
    except Exception:
        return {"ok": False, "error": "invalid value"}
    if v < 0: v = 0
    if v > 100: v = 100
    res = serial_send_line(cid, str(v), expect_reply=True)
    res["value"] = v
    return res


def get_serial_status(cid: str, full: bool = True):
    ctx = _CTXS.get(cid)
    if not ctx:
        return {
            "preferred": "",
            "ports": list_serial_ports() if full else None,
            "available": False,
            "connected": False,
            "baud": 115200,
            "message": f"unknown controller {cid}"
        }

    preferred = ctx.preferred
    ports = list_serial_ports()
    available = bool(ports)
    connected = False
    message = "no ports detected"

    try:
        if (not available) and preferred and not os.path.exists(preferred):
            message = f"preferred port not present: {preferred}"
    except Exception:
        pass

    if preferred:
        ok, msg = probe_serial_open(preferred, ctx.baud)
        connected = ok
        message = msg
        
        if not connected:
            try:
                lvl = logging.WARNING if any(s in str(message).lower() for s in ("denied", "permission", "not opened", "busy", "no such device")) else logging.INFO
                _log().log(
                    lvl,
                    "serial not connected | cid=%s port=%s baud=%s reason=%s",
                    cid, preferred, ctx.baud, message,
                )
            except Exception:
                pass
    elif available:
        message = "ports detected but no port configured for controller"

    data = {
        "preferred": preferred,
        "ports": ports if full else None,
        "available": available,
        "connected": (connected and not (preferred or "").startswith("/dev/ttyS")),
        "baud": ctx.baud,
        "message": message,
    }
    if not full:
        data.pop("ports", None)
    try:
        if _GLOBAL_DBG_SHOULD and _GLOBAL_DBG_SHOULD("serial", 8):
            _log().debug(
                "serial [%s] | preferred=%s available=%s connected=%s baud=%s msg=%s",
                cid, data.get("preferred"), data.get("available"), data.get("connected"), data.get("baud"), data.get("message")
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

