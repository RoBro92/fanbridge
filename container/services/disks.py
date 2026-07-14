import os, glob, configparser, logging, re
from typing import List, Dict, Set, Optional, Tuple


_DEVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_NVME_RE = re.compile(r"^(nvme\d+)n\d+(?:p\d+)?$")
_MAX_CAPACITY_BYTES = (1 << 63) - 1


def is_valid_device_name(dev: str | None) -> bool:
    """Accept kernel block names, never paths or traversal components."""
    value = _unquote(dev)
    return bool(value and _DEVICE_RE.fullmatch(value) and ".." not in value)


def _base_block_device(dev: str) -> str:
    value = _unquote(dev)
    nvme = _NVME_RE.fullmatch(value)
    if nvme:
        # An NVMe namespace is itself represented in /sys/block.  Only strip
        # an optional partition suffix.
        return re.sub(r"p\d+$", "", value)
    mmc = re.fullmatch(r"(mmcblk\d+)(?:p\d+)?", value)
    if mmc:
        return mmc.group(1)
    traditional = re.fullmatch(r"((?:sd|hd|vd|xvd)[a-z]+)\d*", value)
    if traditional:
        return traditional.group(1)
    return value


def _read_file(path: str) -> Optional[str]:
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except Exception:
        return None


def _unquote(s: Optional[str]) -> str:
    if s is None:
        return ""
    s = s.strip()
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        s = s[1:-1]
    return s.strip()


def _capacity_bytes(section: configparser.SectionProxy) -> Optional[int]:
    """Convert Unraid disk capacity fields to bytes without trusting overflow."""
    size_kib = _unquote(section.get("size", fallback=""))
    if size_kib.isdigit():
        value = int(size_kib)
        if 0 < value <= _MAX_CAPACITY_BYTES // 1024:
            return value * 1024
    sectors = _unquote(section.get("sectors", fallback=""))
    sector_size = _unquote(section.get("sector_size", fallback=""))
    if sectors.isdigit() and sector_size.isdigit():
        count = int(sectors)
        width = int(sector_size)
        if count > 0 and width > 0 and count <= _MAX_CAPACITY_BYTES // width:
            return count * width
    return None


def _sysfs(dev: str, rel: str) -> Optional[str]:
    d = _base_block_device(dev)
    if not is_valid_device_name(d):
        return None
    return _read_file(f"/sys/block/{d}/{rel}")


def _spin_state_from_sysfs(dev: str) -> Optional[bool]:
    st = _sysfs(dev, "device/state")
    if st:
        s = st.lower()
        if "running" in s or "active" in s:
            return False
        if "offline" in s or "suspended" in s or "standby" in s:
            return True
    rs = _sysfs(dev, "power/runtime_status")
    if rs:
        r = rs.lower()
        if "active" in r:
            return False
        if "suspend" in r:
            return True
    return None


def _nvme_temp_sysfs(dev: str) -> Optional[int]:
    match = _NVME_RE.fullmatch(_unquote(dev))
    if not match:
        return None
    ctrl = match.group(1)
    candidates = glob.glob(f"/sys/class/nvme/{ctrl}/device/hwmon/hwmon*/temp*_input")
    for p in candidates:
        val = _read_file(p)
        if val and val.strip().isdigit():
            n = int(val.strip())
            if n > 1000:
                n = n // 1000
            if 1 <= n <= 120:
                return n
    return None


def _is_hdd(dev_name: str) -> bool:
    d = _base_block_device(dev_name)
    if d.startswith("nvme"):
        return False
    if not is_valid_device_name(d):
        return True
    rot = _read_file(f"/sys/block/{d}/queue/rotational")
    if rot is not None:
        return rot.strip() == "1"
    return True


def is_bind_mounted_file(path: str) -> bool:
    try:
        if os.path.isdir(path):
            return False
        with open("/proc/self/mountinfo", "r", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                try:
                    left = ln.split(" - ", 1)[0]
                    fields = left.split()
                    if len(fields) >= 5 and fields[4] == path:
                        return True
                except Exception:
                    continue
    except Exception:
        return False
    return False


def read_unraid_disks_with_status(disks_ini: str, excludes: Set[str]) -> Tuple[List[Dict], Dict]:
    """Parse Unraid disk telemetry and retain source-quality information."""
    if not os.path.exists(disks_ini):
        return [], {"ok": False, "error": "missing", "invalid_devices": []}
    cp = configparser.ConfigParser(interpolation=None)
    try:
        with open(disks_ini, "r", encoding="utf-8") as stream:
            cp.read_file(stream)
    except Exception as e:
        logging.getLogger("fanbridge").exception("Failed to parse %s: %s", disks_ini, e)
        return [], {"ok": False, "error": "parse_invalid", "invalid_devices": []}

    drives: List[Dict] = []
    invalid_devices: List[str] = []
    for section in cp.sections():
        dev = _unquote(cp.get(section, "device", fallback=""))
        slot = _unquote(cp.get(section, "name", fallback=""))
        unraid_type = _unquote(cp.get(section, "type", fallback=""))
        status = _unquote(cp.get(section, "status", fallback=""))
        # disks.ini describes more than the storage devices FanBridge should
        # cool.  In particular, USB/internal boot media commonly has no useful
        # temperature sensor, and empty array slots are represented as
        # DISK_NP entries.  Including either would create a permanent
        # missing-temperature fail-safe on otherwise healthy systems.
        if unraid_type.lower() in {"boot", "flash"} or status.upper() == "DISK_NP":
            continue
        if not dev:
            continue
        if not is_valid_device_name(dev):
            invalid_devices.append(dev)
            logging.getLogger("fanbridge").warning(
                "Ignoring invalid block device name | section=%s device=%r", section, dev
            )
            continue
        temp_raw = _unquote(cp.get(section, "temp", fallback=""))
        temp: Optional[int] = None
        if temp_raw.isdigit():
            t = int(temp_raw)
            if 1 <= t <= 120:
                temp = t
        ini_spundown = _unquote(cp.get(section, "spundown", fallback="0")) == "1"
        spundown = ini_spundown
        spin_state_conflict = False
        ss = _spin_state_from_sysfs(dev)
        if not ini_spundown and ss is True:
            spundown = True
        elif ini_spundown and ss is False:
            # A wake-up can reach sysfs before Unraid replaces disks.ini. In
            # that disagreement, treating the cached temperature as safely
            # asleep could select an idle PWM while the disk is active. Mark
            # it active; a missing/old temperature then drives fail-safe.
            spundown = False
            spin_state_conflict = True
        dclean = _unquote(dev)
        # Unraid can retain a previous temperature while a drive is asleep;
        # never treat that cached value as current telemetry.
        if spundown:
            temp = None
        elif spin_state_conflict:
            temp = None
        if temp is None and dclean.startswith("nvme") and not spundown:
            t_nv = _nvme_temp_sysfs(dclean)
            if isinstance(t_nv, int):
                temp = t_nv
        rotational = _unquote(cp.get(section, "rotational", fallback=""))
        if rotational in {"0", "1"}:
            dtype = "HDD" if rotational == "1" else "SSD"
        else:
            dtype = "SSD" if not _is_hdd(dclean) else "HDD"
        if spundown:
            state = "down" if dtype == "HDD" else "spun down"
            temp_status = "spun_down"
        elif temp is None:
            state = "N/A"
            temp_status = "missing_active"
        else:
            state = "up" if dtype == "HDD" else "on"
            temp_status = "ok"
        stable_id = _unquote(cp.get(section, "id", fallback=""))
        serial = _unquote(cp.get(section, "serial", fallback="")) or stable_id
        drives.append({
            "dev": dclean,
            "slot": slot,
            "id": stable_id,
            "serial": serial or None,
            "capacity_bytes": _capacity_bytes(cp[section]),
            "section": section,
            "unraid_type": unraid_type or None,
            "unraid_status": status or None,
            "type": dtype,
            "temp": temp,
            "state": state,
            "spun_down": spundown,
            "spin_state_conflict": spin_state_conflict,
            "temp_status": temp_status,
            "excluded": (dclean in (excludes or set())),
        })
    if invalid_devices:
        return drives, {
            "ok": False,
            "error": "invalid_device",
            "invalid_devices": invalid_devices,
        }
    if not drives:
        return [], {"ok": False, "error": "no_valid_drives", "invalid_devices": []}
    return drives, {"ok": True, "error": None, "invalid_devices": []}


def read_unraid_disks(disks_ini: str, excludes: Set[str]) -> List[Dict]:
    """Backward-compatible list-only parser."""
    drives, _status = read_unraid_disks_with_status(disks_ini, excludes)
    return drives
