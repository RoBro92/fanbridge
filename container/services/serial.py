import os, glob, hashlib, logging, re, stat, threading, time
from contextlib import contextmanager
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


def canonical_port(port: str | None) -> str:
    """Return the physical path used to de-duplicate serial aliases."""
    value = str(port or "").strip()
    if not value:
        return ""
    try:
        return os.path.realpath(value)
    except Exception:
        return os.path.normpath(value)


_PORT_LOCKS: dict[str, threading.RLock] = {}
_PORT_LOCKS_GUARD = threading.RLock()


def _port_lock(port: str | None) -> threading.RLock:
    key = canonical_port(port) or str(port or "")
    with _PORT_LOCKS_GUARD:
        lock = _PORT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PORT_LOCKS[key] = lock
        return lock


@contextmanager
def _physical_transaction(port: str | None):
    """Serialize a device both between threads and overlapping processes."""
    lock = _port_lock(port)
    with lock:
        fd = None
        try:
            candidate_fd = None
            try:
                import fcntl
                key = hashlib.sha256(canonical_port(port).encode("utf-8")).hexdigest()[:20]
                # /tmp is the only writable shared runtime path in the
                # read-only image. Refuse links and inherited descriptors so a
                # predictable lock name cannot be redirected to another file.
                lock_path = f"/tmp/fanbridge-serial-{key}.lock"  # nosec B108
                flags = os.O_RDWR | os.O_CREAT
                flags |= getattr(os, "O_CLOEXEC", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0)
                candidate_fd = os.open(lock_path, flags, 0o600)
                lock_stat = os.fstat(candidate_fd)
                if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_uid != os.geteuid():
                    raise OSError("unsafe serial lock file")
                os.fchmod(candidate_fd, 0o600)
                fcntl.flock(candidate_fd, fcntl.LOCK_EX)
                fd = candidate_fd
            except (ImportError, OSError):
                if candidate_fd is not None:
                    os.close(candidate_fd)
                fd = None
            yield
        finally:
            if fd is not None:
                try:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)


_HARDWARE_UID_RE = re.compile(r"^[a-f0-9]{16,64}$")


def normalise_hardware_uid(value: Any) -> str | None:
    uid = str(value or "").strip().lower()
    return uid if _HARDWARE_UID_RE.fullmatch(uid) else None


class _Ctx:
    def __init__(
        self,
        port: str,
        baud: int,
        expected_type: str = "unknown",
        expected_uid: str | None = None,
    ):
        self.configured = str(port or "").strip()
        self.preferred = self.configured
        self.physical = canonical_port(self.preferred)
        self.baud = int(baud)
        self.expected_type = str(expected_type or "unknown").strip().lower()
        self.expected_uid = normalise_hardware_uid(expected_uid)
        self.last_good: str | None = None
        self.identity: dict | None = None
        self.identity_checked_at = 0.0
        # Locks are shared by physical port, so aliases cannot be used to run
        # overlapping transactions against the same controller.
        self.lock = _port_lock(self.preferred)

_CTXS: dict[str, _Ctx] = {}
_CTXS_LOCK = threading.RLock()
_RECONCILE_LOCK = threading.Lock()
_LAST_RECONCILE_AT = 0.0

_GLOBAL_LOGGER: logging.Logger | None = None
_GLOBAL_DBG_SHOULD = None
_GLOBAL_INC_OPEN_FAIL = None
_GLOBAL_INC_SERIAL_CMD = None

def init(*, logger: logging.Logger, dbg_should, inc_open_fail, inc_serial_cmd=None) -> None:
    global _GLOBAL_LOGGER, _GLOBAL_DBG_SHOULD, _GLOBAL_INC_OPEN_FAIL, _GLOBAL_INC_SERIAL_CMD
    _GLOBAL_LOGGER = logger
    _GLOBAL_DBG_SHOULD = dbg_should
    _GLOBAL_INC_OPEN_FAIL = inc_open_fail
    _GLOBAL_INC_SERIAL_CMD = inc_serial_cmd

def register_controller(
    cid: str,
    port: str,
    baud: int,
    expected_type: str = "unknown",
    expected_uid: str | None = None,
) -> bool:
    """Register a controller, rejecting duplicate ports and hardware IDs."""
    controller_id = str(cid or "").strip()
    if not controller_id:
        return False
    candidate = _Ctx(port, baud, expected_type, expected_uid)
    with _CTXS_LOCK:
        current = _CTXS.get(controller_id)
        if (
            current is not None
            and current.configured == candidate.configured
            and current.baud == candidate.baud
            and current.expected_type == candidate.expected_type
            and current.expected_uid == candidate.expected_uid
        ):
            # load_config() is called frequently. Do not discard the last good
            # path or verified reconnect identity for an unchanged controller.
            return True
        if candidate.expected_uid:
            for other_id, other in _CTXS.items():
                if other_id != controller_id and other.expected_uid == candidate.expected_uid:
                    _log().warning(
                        "controller hardware UID already registered | cid=%s existing_cid=%s uid=%s",
                        controller_id, other_id, candidate.expected_uid,
                    )
                    return False
        if candidate.physical:
            for other_id, other in _CTXS.items():
                if other_id != controller_id and other.physical == candidate.physical:
                    _log().warning(
                        "serial port already registered | cid=%s existing_cid=%s port=%s",
                        controller_id, other_id, port,
                    )
                    return False
        if current is not None:
            stopped = safe_stop_controller(controller_id)
            if not stopped.get("ok"):
                _log().warning(
                    "controller reconfigured before safe-stop was verified | cid=%s error=%s",
                    controller_id,
                    stopped.get("error") or "unknown",
                )
        _CTXS[controller_id] = candidate
    return True

def unregister_controller(cid: str) -> None:
    with _CTXS_LOCK:
        _CTXS.pop(cid, None)


def list_registered_controllers() -> list[dict]:
    with _CTXS_LOCK:
        return [
            {
                "id": cid,
                "configured": ctx.configured,
                "preferred": ctx.preferred,
                "physical": ctx.physical,
                "baud": ctx.baud,
                "expected_type": ctx.expected_type,
                "expected_uid": ctx.expected_uid,
                "last_good": ctx.last_good,
                "identity": dict(ctx.identity) if isinstance(ctx.identity, dict) else None,
            }
            for cid, ctx in _CTXS.items()
        ]


def controller_for_port(port: str | None) -> str | None:
    physical = canonical_port(port)
    if not physical:
        return None
    with _CTXS_LOCK:
        for cid, ctx in _CTXS.items():
            if ctx.physical == physical:
                return cid
    return None


def _get_ctx(cid: str) -> _Ctx | None:
    with _CTXS_LOCK:
        return _CTXS.get(cid)

def _log() -> logging.Logger:
    return _GLOBAL_LOGGER or logging.getLogger("fanbridge")


def _unique_order(seq):
    seen = set()
    out = []
    for x in seq:
        key = canonical_port(x)
        if x and key not in seen:
            out.append(x)
            seen.add(key)
    return out


def list_serial_ports():
    candidates = []
    candidates.extend(sorted(glob.glob("/dev/serial/by-id/*")))
    candidates.extend(sorted(glob.glob("/dev/ttyACM*")))
    candidates.extend(sorted(glob.glob("/dev/ttyUSB*")))
    if os.environ.get("FANBRIDGE_DEV_SERIAL", "0") == "1":
        candidates.extend(sorted(glob.glob("/dev/pts/*")))
        candidates.extend(sorted(glob.glob("/tmp/ttyFAN*")))  # nosec B108 - explicit dev mode only
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
        with _physical_transaction(port):
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
                    with _physical_transaction(cu_port):
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


_IDENTITY_RE = re.compile(
    r"^FANBRIDGE_(DIY|OFFICIAL)\s+protocol=(\d+)\s+board=([A-Za-z0-9._-]{1,64})"
    r"\s+channels=(\d{1,2})(?:\s+uid=([A-Fa-f0-9]{16,64}))?$"
)
_SUPPORTED_PROTOCOLS = {1, 2}


def parse_identity_response(response: str | None) -> dict | None:
    raw = str(response or "").strip()
    # Firmware 2.2 and earlier returned only this token. Recognise it so the
    # UI can give an actionable upgrade message, but never treat it as safe
    # for automatic output (it booted at 0% and diagnostics renewed its lease).
    if raw in {"FANBRIDGE_DIY", "FANBRIDGE_OFFICIAL"}:
        return {
            "type": "diy" if raw.endswith("DIY") else "official",
            "protocol": 0,
            "board": "legacy-unknown",
            "channels": 1,
            "raw": raw,
            "supported": False,
            "legacy": True,
        }
    match = _IDENTITY_RE.fullmatch(raw)
    if not match:
        return None
    protocol = int(match.group(2))
    channels = int(match.group(4))
    hardware_uid = normalise_hardware_uid(match.group(5))
    if not 1 <= channels <= 32:
        return None
    # Protocol 2 exists specifically to make controller identity persistent.
    # Accepting a v2 response without a UID would silently undo that contract.
    if protocol >= 2 and hardware_uid is None:
        return None
    return {
        "type": "diy" if match.group(1) == "DIY" else "official",
        "protocol": protocol,
        "board": match.group(3),
        "channels": channels,
        "hardware_uid": hardware_uid,
        "raw": match.group(0),
        "supported": protocol in _SUPPORTED_PROTOCOLS,
        "legacy": False,
    }


def identify_port_details(port: str, timeout: float = 0.5) -> dict | None:
    if serial is None:
        return None
    try:
        with _physical_transaction(port):
            s = serial.Serial(port=port, baudrate=115200, timeout=timeout)
            try:
                s.reset_input_buffer()
                s.reset_output_buffer()

                def read_matching(predicate, max_lines: int = 4) -> str | None:
                    for _ in range(max_lines):
                        line = s.readline().decode("utf-8", errors="ignore").strip()
                        if not line:
                            continue
                        if predicate(line):
                            return line
                    return None

                resp = None
                details = None
                legacy_unknown = False
                # CDC startup banners can arrive after reset_input_buffer().
                # Read a bounded number of lines and retry once instead of
                # permanently quarantining a healthy newly booted 2.3 board.
                for _ in range(2):
                    s.write(b"ID?\n")
                    s.flush()
                    resp = read_matching(
                        lambda line: parse_identity_response(line) is not None
                        or line.startswith("Unknown. Use:")
                    )
                    details = parse_identity_response(resp)
                    legacy_unknown = bool(resp and resp.startswith("Unknown. Use:"))
                    if details or legacy_unknown:
                        break
                if details and details.get("legacy"):
                    s.write(b"100\n")
                    s.flush()
                    acknowledgement = read_matching(lambda line: line == "Set fan to 100%")
                    details["safe_stop_ok"] = acknowledgement == "Set fan to 100%"
                    return details
                if details:
                    return details

                # The published DIY 2.1/2.2 images predate ID?. Their command
                # parser nevertheless renews its unsafe 0% startup lease for
                # every unknown diagnostic. Identify by the version response,
                # immediately force 100%, and quarantine it until upgraded.
                s.write(b"VERSION\n")
                s.flush()
                version = read_matching(
                    lambda line: re.fullmatch(
                        r"2\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", line
                    ) is not None
                ) or ""
                if re.fullmatch(r"2\.[12]\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", version):
                    s.write(b"100\n")
                    s.flush()
                    acknowledgement = read_matching(lambda line: line == "Set fan to 100%")
                    return {
                        "type": "diy",
                        "protocol": 0,
                        "board": "legacy-pico",
                        "channels": 1,
                        "raw": resp,
                        "version": version,
                        "supported": False,
                        "legacy": True,
                        "safe_stop_ok": acknowledgement == "Set fan to 100%",
                    }
                return None
            finally:
                s.close()
    except Exception:
        pass
    return None


def identify_port(port: str, timeout: float = 0.5) -> str:
    details = identify_port_details(port, timeout=timeout)
    if details:
        return str(details["type"])
    return "unknown"


def identify_unregistered_controller(
    port: str,
    timeout: float = 0.35,
    excluded_hardware_uids: set[str] | None = None,
) -> dict:
    """Request a bounded physical identify signal without registering a board.

    This deliberately exposes only the fixed IDENTIFY command. It cannot be
    used as a pre-enrollment raw serial or PWM path.
    """
    selected_port = str(port or "").strip()
    if not selected_port:
        return {"ok": False, "error": "no port specified", "code": "invalid_port"}
    if serial is None:
        return {"ok": False, "error": "pyserial not available", "code": "serial_unavailable"}
    owner = controller_for_port(selected_port)
    if owner:
        return {
            "ok": False,
            "error": f"serial port is already assigned to controller {owner}",
            "code": "already_assigned",
        }
    try:
        with _physical_transaction(selected_port):
            owner = controller_for_port(selected_port)
            if owner:
                return {
                    "ok": False,
                    "error": f"serial port is already assigned to controller {owner}",
                    "code": "already_assigned",
                }
            s = serial.Serial(port=selected_port, baudrate=115200, timeout=timeout)
            try:
                s.reset_input_buffer()
                s.reset_output_buffer()
                details = None
                for _ in range(2):
                    s.write(b"ID?\n")
                    s.flush()
                    for _line in range(4):
                        response = s.readline().decode("utf-8", errors="ignore").strip()
                        details = parse_identity_response(response)
                        if details:
                            break
                    if details:
                        break
                if (
                    not details
                    or details.get("type") != "diy"
                    or details.get("board") != "rp2040-zero"
                    or details.get("channels") != 1
                ):
                    return {
                        "ok": False,
                        "error": "device did not identify as a DIY RP2040-Zero controller",
                        "code": "identity_failed",
                    }
                if not details.get("supported") or not details.get("hardware_uid"):
                    return {
                        "ok": False,
                        "error": "controller firmware does not support persistent identification",
                        "code": "upgrade_required",
                        "identity": details,
                    }
                excluded = {
                    uid for value in (excluded_hardware_uids or set())
                    if (uid := normalise_hardware_uid(value))
                }
                if details["hardware_uid"] in excluded:
                    return {
                        "ok": False,
                        "error": "controller hardware identity is already configured",
                        "code": "already_assigned",
                        "identity": details,
                    }
                s.write(b"IDENTIFY\n")
                s.flush()
                acknowledgement = None
                for _ in range(4):
                    response = s.readline().decode("utf-8", errors="ignore").strip()
                    if re.fullmatch(r"IDENTIFYING duration_ms=10000", response):
                        acknowledgement = response
                        break
                if acknowledgement is None:
                    return {
                        "ok": False,
                        "error": "controller does not support LED identification; install DIY firmware 2.5.0 or newer",
                        "code": "upgrade_required",
                        "identity": details,
                    }
                return {
                    "ok": True,
                    "port": selected_port,
                    "duration_ms": 10000,
                    "reply": acknowledgement,
                    "identity": details,
                }
            finally:
                s.close()
    except Exception as exc:
        _log().warning("controller identify failed | port=%s err=%s", selected_port, exc)
        return {"ok": False, "error": str(exc), "code": "serial_error"}


def _activate_port(ctx: _Ctx, port: str, details: dict | None = None) -> None:
    ctx.preferred = str(port or "").strip()
    ctx.physical = canonical_port(ctx.preferred)
    ctx.lock = _port_lock(ctx.preferred)
    ctx.last_good = ctx.preferred or None
    ctx.identity = dict(details) if isinstance(details, dict) else None
    ctx.identity_checked_at = time.monotonic() if details else 0.0


def reconcile_controller_ports(*, force: bool = False, min_interval: float = 2.0) -> dict:
    """Rebind persisted hardware UIDs to the serial paths currently visible.

    The scan is deliberately all-or-nothing: paths are identified first, then
    contexts are updated together. A duplicated UID is never used for binding.
    """
    global _LAST_RECONCILE_AT
    now = time.monotonic()
    if not force and now - _LAST_RECONCILE_AT < max(0.0, min_interval):
        return {"scanned": False, "reason": "throttled", "bindings": {}}
    if not _RECONCILE_LOCK.acquire(blocking=False):
        return {"scanned": False, "reason": "scan_in_progress", "bindings": {}}
    try:
        now = time.monotonic()
        if not force and now - _LAST_RECONCILE_AT < max(0.0, min_interval):
            return {"scanned": False, "reason": "throttled", "bindings": {}}
        scanned: dict[str, dict] = {}
        uid_ports: dict[str, list[str]] = {}
        for port in list_serial_ports():
            details = identify_port_details(port)
            if not isinstance(details, dict):
                continue
            scanned[port] = details
            uid = normalise_hardware_uid(details.get("hardware_uid"))
            if uid:
                uid_ports.setdefault(uid, []).append(port)
        _LAST_RECONCILE_AT = time.monotonic()

        unique = {
            uid: ports[0]
            for uid, ports in uid_ports.items()
            if len(ports) == 1
        }
        duplicate_uids = sorted(uid for uid, ports in uid_ports.items() if len(ports) != 1)
        bindings: dict[str, str] = {}
        with _CTXS_LOCK:
            # Exact UID ownership wins a visible path. Displace any stale
            # context first so it cannot accidentally drive the replacement.
            claimed: dict[str, str] = {}
            targets: dict[str, str] = {}
            for cid, ctx in _CTXS.items():
                if ctx.expected_uid and ctx.expected_uid in unique:
                    target = unique[ctx.expected_uid]
                    physical = canonical_port(target)
                    if physical and physical not in claimed:
                        targets[cid] = target
                        claimed[physical] = cid

            for cid, ctx in _CTXS.items():
                target = targets.get(cid)
                if target:
                    details = scanned[target]
                    if ctx.preferred != target:
                        _log().info(
                            "controller rebound by hardware UID | cid=%s uid=%s old_port=%s new_port=%s",
                            cid, ctx.expected_uid, ctx.preferred or "none", target,
                        )
                    _activate_port(ctx, target, details)
                    bindings[cid] = target
                    continue

                if ctx.expected_uid in duplicate_uids:
                    _activate_port(ctx, "")
                    continue

                current_physical = canonical_port(ctx.preferred)
                owner = claimed.get(current_physical)
                if owner and owner != cid:
                    _log().warning(
                        "serial path reassigned to its persisted hardware UID | displaced_cid=%s owner_cid=%s port=%s",
                        cid, owner, ctx.preferred,
                    )
                    _activate_port(ctx, "")
                    continue

                if ctx.expected_uid:
                    current_details = scanned.get(ctx.preferred)
                    current_uid = normalise_hardware_uid(
                        current_details.get("hardware_uid") if current_details else None
                    )
                    if current_details is not None and current_uid != ctx.expected_uid:
                        _activate_port(ctx, "")
                    elif ctx.preferred and not os.path.exists(ctx.preferred):
                        _activate_port(ctx, "")

        if duplicate_uids:
            _log().error(
                "duplicate controller hardware UID detected; automatic binding refused | uids=%s",
                ",".join(duplicate_uids),
            )
        return {
            "scanned": True,
            "bindings": bindings,
            "duplicate_uids": duplicate_uids,
            "identified_ports": len(scanned),
        }
    finally:
        _RECONCILE_LOCK.release()


def verify_controller_identity(cid: str, max_age: float = 2.0) -> tuple[bool, dict | None, str | None]:
    """Re-identify reused/re-enumerated ports before treating them as control targets."""
    ctx = _get_ctx(cid)
    if not ctx:
        return False, None, "unknown controller id"
    now = time.monotonic()
    if isinstance(ctx.identity, dict) and ctx.identity.get("quarantined"):
        details = dict(ctx.identity)
    elif isinstance(ctx.identity, dict) and now - ctx.identity_checked_at <= max(0.0, max_age):
        details = dict(ctx.identity)
    else:
        details = identify_port_details(ctx.preferred) if ctx.preferred else None
        ctx.identity_checked_at = now
        ctx.identity = dict(details) if isinstance(details, dict) else None
    expected_uid = ctx.expected_uid
    actual_uid = normalise_hardware_uid(details.get("hardware_uid")) if details else None
    if expected_uid and actual_uid != expected_uid:
        reconcile_controller_ports()
        ctx = _get_ctx(cid)
        if not ctx:
            return False, None, "unknown controller id"
        details = dict(ctx.identity) if isinstance(ctx.identity, dict) else None
        if details is None and ctx.preferred:
            details = identify_port_details(ctx.preferred)
            ctx.identity = dict(details) if isinstance(details, dict) else None
            ctx.identity_checked_at = time.monotonic()
        actual_uid = normalise_hardware_uid(details.get("hardware_uid")) if details else None
    if not details and expected_uid:
        return False, None, "persisted controller hardware UID is not present on any visible serial port"
    if not details:
        # A configured port may contain the released pre-ID DIY firmware. The
        # preceding ID?/VERSION probes renewed its legacy lease, so issue one
        # maximum-cooling command even when version identification was noisy,
        # then suppress further diagnostics until the port disappears/replugs.
        result = serial_send_line(cid, "100", expect_reply=True, timeout=0.5)
        safe_stop_ok = bool(
            result.get("ok") and result.get("reply") == "Set fan to 100%"
        )
        details = {
            "type": "unknown",
            "protocol": None,
            "board": "unverified",
            "channels": None,
            "supported": False,
            "legacy": True,
            "quarantined": True,
            "safe_stop_ok": safe_stop_ok,
        }
        ctx.identity = dict(details)
        return False, details, (
            "unverified controller was forced to 100% and quarantined; install DIY firmware 2.4.0 or newer"
            if safe_stop_ok else
            "controller identity and 100% safe-stop could not be verified; disconnect it and check cooling"
        )
    if not details.get("supported"):
        details = dict(details)
        details["quarantined"] = True
        ctx.identity = dict(details)
        if details.get("legacy") and details.get("type") == "diy":
            safe_note = "forced to 100%" if details.get("safe_stop_ok") else "100% safe-stop unverified"
            return False, details, f"legacy DIY firmware {safe_note}; install DIY firmware 2.4.0 or newer"
        return False, details, "controller protocol is not supported by this FanBridge version"
    if expected_uid and actual_uid != expected_uid:
        return False, details, "persisted controller hardware UID is not present on any visible serial port"
    expected = ctx.expected_type
    if expected in {"diy", "official"} and details.get("type") != expected:
        return False, details, f"controller identity changed (expected {expected})"
    if details.get("type") == "official":
        return False, details, "six-channel custom-controller actuation is not implemented in this host release"
    if details.get("type") == "diy" and details.get("channels") != 1:
        return False, details, "DIY controller reported an invalid channel count"
    return True, details, None

def open_serial(cid: str, timeout: float = 1.0) -> tuple[SerialProto | None, str | None]:
    if serial is None:
        return None, "pyserial not available"
    ctx = _get_ctx(cid)
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
    stripped_line = str(line or "").strip()
    kind = "pwm" if stripped_line.isdigit() else (stripped_line.split(None, 1)[0].lower() if stripped_line else "empty")

    def record(status: str) -> None:
        try:
            if _GLOBAL_INC_SERIAL_CMD:
                _GLOBAL_INC_SERIAL_CMD(kind, status)
        except Exception:
            pass

    ctx = _get_ctx(cid)
    if not ctx:
        out["error"] = "unknown controller id"
        record("error")
        return out
    with _physical_transaction(ctx.preferred):
        s, err = open_serial(cid, timeout=timeout)
        if err:
            out["error"] = err
            record("error")
            return out
        if s is None:
            out["error"] = "serial not available"
            record("error")
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
                if not resp:
                    out["error"] = "no reply from controller"
                    record("error")
                    return out
            out["ok"] = True
            record("ok")
            return out
        except Exception as e:
            out["error"] = str(e)
            record("error")
            return out
        finally:
            try:
                s.close()
            except Exception:
                pass


def serial_set_pwm_percent(cid: str, value: Any) -> dict:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return {"ok": False, "error": "invalid value"}
    try:
        if isinstance(value, float) and not value.is_integer():
            raise ValueError
        v = int(value)
    except Exception:
        return {"ok": False, "error": "invalid value"}
    if v < 0 or v > 100:
        return {"ok": False, "error": "PWM percent must be between 0 and 100"}
    # Never actuate from a cached handshake: a USB path can be reused by a
    # different board between control cycles.
    identity_ok, identity, identity_error = verify_controller_identity(cid, max_age=0.0)
    if not identity_ok:
        return {
            "ok": False,
            "error": identity_error or "controller identity could not be verified",
            "identity": identity,
            "value": v,
        }
    res = serial_send_line(cid, str(v), expect_reply=True)
    res["value"] = v
    if res.get("ok"):
        match = re.fullmatch(r"Set fan to (\d{1,3})%", str(res.get("reply") or ""))
        if not match or int(match.group(1)) != v:
            res["ok"] = False
            res["error"] = "controller returned an invalid PWM acknowledgement"
    res["identity"] = identity
    return res


def safe_stop_controller(cid: str) -> dict:
    """Best-effort transition to maximum cooling before ownership is removed."""
    ctx = _get_ctx(cid)
    if not ctx:
        return {"ok": False, "error": "unknown controller id", "value": 100}
    if isinstance(ctx.identity, dict) and ctx.identity.get("quarantined"):
        if ctx.identity.get("safe_stop_ok"):
            return {
                "ok": True,
                "value": 100,
                "already_safe": True,
                "identity": dict(ctx.identity),
            }
        return {
            "ok": False,
            "value": 100,
            "error": "quarantined controller safe-stop was not verified",
            "identity": dict(ctx.identity),
        }
    return serial_set_pwm_percent(cid, 100)


def get_serial_status(cid: str, full: bool = True):
    ctx = _get_ctx(cid)
    if not ctx:
        return {
            "preferred": "",
            "ports": list_serial_ports() if full else None,
            "available": False,
            "connected": False,
            "baud": 115200,
            "message": f"unknown controller {cid}"
        }

    ports = list_serial_ports()
    available = bool(ports)
    connected = False
    identity = None
    message = "no ports detected"

    # A stable-ID controller may have moved to another visible USB path since
    # the last poll. Reconcile before probing so a missing old path is not
    # treated as the final result.
    if ctx.expected_uid:
        reconcile_controller_ports()
        ctx = _get_ctx(cid) or ctx
    preferred = ctx.preferred

    try:
        if (not available) and preferred and not os.path.exists(preferred):
            message = f"preferred port not present: {preferred}"
    except Exception:
        pass

    if preferred:
        with ctx.lock:
            ok, msg = probe_serial_open(preferred, ctx.baud)
        connected = ok
        message = msg
        identity = None
        if connected:
            connected, identity, identity_error = verify_controller_identity(cid)
            if not connected:
                message = identity_error or "controller identity could not be verified"
        else:
            # A physical disconnect is the explicit rescan boundary. This lets
            # a manually upgraded legacy board identify after it re-enumerates.
            ctx.identity = None
            ctx.identity_checked_at = 0.0
        
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
        "configured": ctx.configured,
        "preferred": preferred,
        "ports": ports if full else None,
        "available": available,
        "connected": (connected and not (preferred or "").startswith("/dev/ttyS")),
        "baud": ctx.baud,
        "message": message,
        "identity": identity,
        "hardware_uid": ctx.expected_uid,
        "persistent_identity": bool(ctx.expected_uid),
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
