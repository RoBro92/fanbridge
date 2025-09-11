import os, glob, configparser, logging
from typing import List, Dict, Set, Optional


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


def _sysfs(dev: str, rel: str) -> Optional[str]:
    d = _unquote(dev)
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
    ctrl = dev.split("n", 1)[0]
    candidates = glob.glob(f"/sys/class/nvme/{ctrl}/device/hwmon/hwmon*/temp*_input")
    for p in candidates:
        val = _read_file(p)
        if val and val.strip().isdigit():
            n = int(val.strip())
            if n > 1000:
                n = n // 1000
            if 0 <= n <= 120:
                return n
    return None


def _is_hdd(dev_name: str) -> bool:
    d = _unquote(dev_name)
    if d.startswith("nvme"):
        return False
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


def read_unraid_disks(disks_ini: str, excludes: Set[str]) -> List[Dict]:
    if not os.path.exists(disks_ini):
        return []
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(disks_ini, encoding="utf-8")
    except Exception as e:
        logging.getLogger("fanbridge").exception("Failed to parse %s: %s", disks_ini, e)
        return []

    drives: List[Dict] = []
    for section in cp.sections():
        dev = _unquote(cp.get(section, "device", fallback=""))
        slot = _unquote(cp.get(section, "name", fallback=""))
        if not dev:
            continue
        temp_raw = _unquote(cp.get(section, "temp", fallback=""))
        temp: Optional[int] = None
        if temp_raw.isdigit():
            t = int(temp_raw)
            if 0 <= t <= 120:
                temp = t
        spundown = _unquote(cp.get(section, "spundown", fallback="0")) == "1"
        ss = _spin_state_from_sysfs(dev)
        if (spundown is False) and (ss is True):
            spundown = True
        dclean = _unquote(dev)
        if temp is None and dclean.startswith("nvme") and not spundown:
            t_nv = _nvme_temp_sysfs(dclean)
            if isinstance(t_nv, int):
                temp = t_nv
        dtype = "SSD" if not _is_hdd(dclean) else "HDD"
        if dtype == "HDD":
            state = "down" if spundown else "up"
        else:
            state = "spun down" if spundown else ("on" if temp is not None else "N/A")
        drives.append({
            "dev": dclean,
            "slot": slot,
            "type": dtype,
            "temp": temp,
            "state": state,
            "excluded": (dclean in (excludes or set())),
        })
    return drives

