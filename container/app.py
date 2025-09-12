from flask import Flask, jsonify, request, render_template, session, redirect, url_for, make_response, g
import os, time, yaml, glob, pathlib, logging, sys, datetime, secrets
from typing import Protocol, runtime_checkable
from services import serial as serial_svc
from api.serial import bp as serial_bp
from api.appinfo import bp as appinfo_bp
from api.logs import bp as logs_bp
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from dotenv import load_dotenv  
except Exception:
    load_dotenv = None  

_BASE = pathlib.Path(__file__).resolve().parent
_PROJECT_ROOT = _BASE.parent 


@runtime_checkable
class SerialProto(Protocol):
    pass  # maintained for legacy type references; implementation moved to services.serial

def _secret_path() -> pathlib.Path:
    # persist a stable session secret across restarts
    return (_BASE / "secret.key") if not _in_docker() else pathlib.Path("/config/secret.key")
    # Ensure gunicorn workers share a stable secret key.
    # - If the file exists: read it
    # - Else: atomically create then re-read
    # Handles first-boot worker race with brief retries.
def _load_or_create_secret() -> str:
    p = _secret_path()
    try:
        # fast path
        if p.exists():
            key = p.read_text(encoding="utf-8").strip()
            if key:
                return key

        # create if missing/empty
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        key = secrets.token_urlsafe(32)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(key)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)

        # re-read to be 100% sure all workers see identical bytes
        for _ in range(3):
            k2 = p.read_text(encoding="utf-8").strip()
            if k2:
                return k2
            time.sleep(0.05)
        return key  # fallback
    except Exception:
        # absolute last resort (won't persist across workers)
        return secrets.token_urlsafe(32)

def _in_docker() -> bool:
    try:
        return os.path.exists("/.dockerenv")
    except Exception:
        return False

# Local-only default serial port for macOS RP2040 testing
def _local_default_serial_port() -> str:
    try:
        # Prefer explicit RP2040 path if present (use call-out device on macOS)
        path = "/dev/cu.usbmodem101"
        if os.path.exists(path):
            return path
        # Fallback: first matching usbmodem device on macOS, prefer cu over tty
        for patt in ("/dev/cu.usbmodem*", "/dev/tty.usbmodem*"):
            matches = sorted(glob.glob(patt))
            if matches:
                return matches[0]
    except Exception:
        pass
    return ""

if load_dotenv:
    for _p in (
        _PROJECT_ROOT / ".env",
        _BASE / ".env",
        pathlib.Path("/config/.env"),
    ):
        try:
            load_dotenv(str(_p), override=False)
        except Exception:
            pass

# ---------- logging setup ----------
from core.logging_setup import (
    setup_logging as _setup_logging,
    ensure_handlers as _ensure_log_handlers,
    RingBufferHandler,
    LOG_RING as _LOG_RING,
    LOG_LOCK as _LOG_LOCK,
)

_setup_logging()
log = logging.getLogger("fanbridge")

_DBG_LAST: dict[str, float] = {}
def _dbg_should(tag: str, interval_s: int = 10) -> bool:
    # Skip throttling when explicit spam debug requested
    if os.environ.get("FANBRIDGE_DEBUG_SPAM") == "1":
        return True
    try:
        now = time.time()
        last = _DBG_LAST.get(tag, 0.0)
        if (now - last) >= max(1, interval_s):
            _DBG_LAST[tag] = now
            return True
    except Exception:
        return True
    return False

_WARN_ONCE: set[str] = set()
def _warn_once(key: str, message: str) -> None:
    try:
        if key in _WARN_ONCE:
            return
        _WARN_ONCE.add(key)
        logging.getLogger("fanbridge").warning(message)
    except Exception:
        pass

def _client_info() -> dict:
    try:
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
        ua = request.headers.get("User-Agent", "")
        return {"ip": ip, "ua": ua}
    except Exception:
        return {}

def _audit(event: str, **data) -> None:
    try:
        import json as _json
        payload = {"event": event, **data, **_client_info()}
        logging.getLogger("fanbridge").info("audit | %s", _json.dumps(payload, sort_keys=True))
    except Exception:
        pass

# --------- Minimal Prometheus metrics ---------
import core.metrics as _metrics
from core.metrics import (
    m_inc_http as _m_inc_http,
    m_inc_serial_cmd as _m_inc_serial_cmd,
    m_inc_serial_open_fail as _m_inc_serial_open_fail,
)

def _read_version_from_release() -> str | None:
    # Extract version from RELEASE.md/CHANGELOG.md.
    # Accepts formats: "Version: X.Y.Z", "# vX.Y.Z", "## 1.2.3", or "## [1.2.3]".
    # Returns the version string if found, else None.
    import re
    # Search typical locations both in dev (repo layout) and in container
    # In container we copy RELEASE.md into the same folder as app.py (/app)
    candidates = [
        _PROJECT_ROOT / "RELEASE.md",   # repo root (dev)
        _PROJECT_ROOT / "CHANGELOG.md",
        _BASE / "RELEASE.md",           # alongside app.py (container)
        pathlib.Path("RELEASE.md"),     # CWD fallback
    ]
    # Simple semver with optional pre-release/build, e.g. 1.2.3, 1.2, v1.2.3-dev, 1.2.3+meta
    SEMVER = r"v?([0-9]+(?:\.[0-9]+){1,2}(?:-[0-9A-Za-z\.-]+)?(?:\+[0-9A-Za-z\.-]+)?)"
    rx_list = [
        re.compile(rf"^\s*Version\s*:\s*{SEMVER}\b", re.I),
        re.compile(rf"^\s*#+\s*{SEMVER}\b"),
        re.compile(rf"^\s*\[{SEMVER}\]"),
    ]
    for p in candidates:
        try:
            if not p.exists():
                continue
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    for rx in rx_list:
                        m = rx.search(line)
                        if m:
                            return m.group(1)
        except Exception:
            continue
    return None

# Canonical version source: RELEASE.md only.
# If not found, leave empty so UI shows "—".
APP_VERSION = _read_version_from_release() or None
# When running this file directly (local dev), show version "local" early,
# so startup logs and UI are consistent without extra setup.
if __name__ == "__main__" and not os.environ.get("GUNICORN_WORKER"):  # heuristic: not under gunicorn
    APP_VERSION = "local"

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = _load_or_create_secret()

# Session/cookie hardening + predictability
_SECURE_COOKIES = (os.environ.get("FANBRIDGE_SECURE_COOKIES", "0") == "1")
app.config.update(
    SESSION_COOKIE_NAME="fanbridge_session",
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(_SECURE_COOKIES),  # enable in prod behind TLS via env
    SESSION_COOKIE_HTTPONLY=True,
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=30),
)
if os.environ.get("TEMPLATES_AUTO_RELOAD") == "1" or os.environ.get("FLASK_DEBUG"):
    try:
        app.config["TEMPLATES_AUTO_RELOAD"] = True
        app.jinja_env.auto_reload = True
    except Exception:
        pass
STARTED = time.time()

def _should_log_startup() -> bool:
    # Log once across Flask's reloader and always in non-reloader contexts.
    # - If WERKZEUG_RUN_MAIN is unset (no reloader/production), log.
    # - If set, only log when it's the reloader child (== 'true').
    rm = os.environ.get("WERKZEUG_RUN_MAIN")
    return (rm is None) or (rm == "true")

if _should_log_startup():
    # Re‑attach ring buffer handler in case a WSGI server reconfigured logging
    # after import (e.g., Gunicorn). This keeps the UI Logs view working.
    try:
        _ensure_log_handlers()
    except Exception:
        pass
    log.info("FanBridge starting | version=%s in_docker=%s", APP_VERSION or "unknown", str(_in_docker()).lower())


def _default_config_path() -> str:
    # When not in Docker (e.g., running `python3 app.py`), prefer a local file
    # so no special setup is required for development.
    return "/config/config.yml" if _in_docker() else str(_BASE / "config.local.yml")

CONFIG_PATH = os.environ.get("FANBRIDGE_CONFIG") or _default_config_path()
DISKS_INI = "/unraid/disks.ini"   # bind-mount to /var/local/emhttp/disks.ini on host
USERS_PATH = "/config/users.yml" if _in_docker() else str((_BASE / "users.local.yml"))

# Serial preference and baud configurable via environment
# - In Docker: assume RP2040 default at /dev/ttyACM0 (non-fatal if absent)
# - Local dev: prefer a discovered macOS-style RP2040 path
SERIAL_PREF = (
    os.environ.get("FANBRIDGE_SERIAL_PORT", "").strip()
    or ("/dev/ttyACM0" if _in_docker() else _local_default_serial_port())
)
SERIAL_BAUD = int(os.environ.get("FANBRIDGE_SERIAL_BAUD", "115200") or "115200")

# Remember the last successfully opened port to survive re-enumeration (e.g., ACM0→ACM1)
_SERIAL_LAST_GOOD: str | None = None

try:
    if _should_log_startup():
        log.info(
            "paths | config=%s users=%s disks_ini=%s exists=%s serial_pref=%s baud=%s",
            CONFIG_PATH, USERS_PATH, DISKS_INI, str(os.path.exists(DISKS_INI)).lower(), SERIAL_PREF, SERIAL_BAUD
        )
        # If running in Docker and a preferred serial port is configured but missing, log an error once.
        try:
            if _in_docker() and SERIAL_PREF and not os.path.exists(SERIAL_PREF):
                log.error(
                    "preferred serial port not present | port=%s (map the device or plug it in)",
                    SERIAL_PREF,
                )
        except Exception:
            pass
except Exception:
    pass

# App factory for WSGI servers
def create_app():
    # Register blueprints
    try:
        app.register_blueprint(serial_bp, url_prefix="/api/serial")
    except Exception:
        pass
    # Provide app-wide context for blueprints (read-only values/functions)
    app.config['FB_APP_INFO'] = {
        'CONFIG_PATH': CONFIG_PATH,
        'USERS_PATH': USERS_PATH,
        'DISKS_INI': DISKS_INI,
        'STARTED': STARTED,
        'APP_VERSION': APP_VERSION,
        'IN_DOCKER_FUNC': _in_docker,
    }
    try:
        app.register_blueprint(appinfo_bp, url_prefix="/api")
    except Exception:
        pass
    try:
        app.register_blueprint(logs_bp, url_prefix="/api")
    except Exception:
        pass
    try:
        # Final safeguard: ensure our ring handler is present once the app is built
        _ensure_log_handlers()
    except Exception:
        pass
    return app

# Initialize serial service context
try:
    serial_svc.init(
        baud=SERIAL_BAUD,
        preferred=SERIAL_PREF,
        logger=log,
        dbg_should=_dbg_should,
        inc_open_fail=_m_inc_serial_open_fail,
    )
except Exception:
    pass

DEFAULT_CONFIG = {
    "poll_interval_seconds": 7,     # UI refresh; clamped 3–60s
    "hdd_thresholds": [30,32,35,38,40,42,44,45],
    "hdd_pwm":        [0,20,30,40,50,60,80,100],
    "ssd_thresholds": [35,40,45,48,50,52,54,55],
    "ssd_pwm":        [0,20,30,40,55,70,85,100],
    "single_override_hdd_c": 45,
    "single_override_ssd_c": 60,
    "override_pwm": 100,
    "fallback_pwm": 10,
    "pwm_hysteresis": 3,
    "exclude_devices": [],
    "idle_cutoff_hdd_c": 30,  
    "idle_cutoff_ssd_c": 35,  
    "sim": { "drives": [] },
    # Auto-apply PWM to microcontroller (local/dev or container) — disabled by default
    "auto_apply": False,
    "auto_apply_min_interval_s": 3,          # minimum seconds between sends
    "auto_apply_hysteresis_duty": 5,         # minimum change in 0..255 units
    # RP firmware update settings
    "rp": {
        # URL hosting a manifest.json and UF2 files; adjustable in UI
        # Example expected: <base>/manifest.json -> { items: [{ board, version, url }] }
        "repo_url": "https://raw.githubusercontent.com/RoBro92/fanbridge-link/main",
        # Optional: override board name; default 'rp2040'
        "board": "rp2040",
        # Optional: preferred RP2 block device path (e.g., /dev/disk/by-id/...-part1 or /dev/sdX1)
        # When set and present, flashing will use this path first.
        "rp2_device": "",
    },
}

# Threshold for considering /unraid/disks.ini stale (seconds). Defaults to Unraid's
# poll_attributes default (30 minutes). Can be tuned via env.
try:
    DISKS_STALE_WARN_SEC = int(os.environ.get("FANBRIDGE_DISKS_STALE_WARN_SEC", "1800") or "1800")
except Exception:
    DISKS_STALE_WARN_SEC = 1800

from services.disks import is_bind_mounted_file as _is_bind_mounted_file

def _load_users() -> dict:
    try:
        if not os.path.exists(USERS_PATH):
            return {}
        with open(USERS_PATH, "r") as f:
            data = yaml.safe_load(f) or {}
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _save_users(users: dict) -> None:
    try:
        os.makedirs(os.path.dirname(USERS_PATH), exist_ok=True)
    except Exception:
        pass
    with open(USERS_PATH, "w") as f:
        yaml.safe_dump(users, f, sort_keys=False)

# Rate limiting (per-IP, per-key)
# _RATE maps (ip, key) -> [timestamps]
_RATE: dict[tuple[str, str], list[float]] = {}

def _allow(ip: str, key: str, *, limit: int = 20, window: int = 60) -> bool:
    """
    Return True if allowed for (ip,key), else False.
    - key groups similar endpoints, e.g. 'serial_send', 'settings', etc.
    - window in seconds, sliding.
    """
    try:
        now = time.time()
        k = (ip or "?", key or "*")
        arr = _RATE.get(k, [])
        # drop old
        arr = [t for t in arr if now - t < window]
        if len(arr) >= limit:
            _RATE[k] = arr
            return False
        arr.append(now)
        _RATE[k] = arr
        return True
    except Exception:
        # fail-open
        return True

def _ensure_csrf_token() -> str:
    tok = session.get("csrf_token")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["csrf_token"] = tok
    return tok

def _require_csrf() -> bool:
    sent = request.headers.get("X-CSRF-Token", "")
    good = session.get("csrf_token")
    if not good or not secrets.compare_digest(sent, good):
        return False
    return True

def _merge_defaults(user_cfg: dict, defaults: dict) -> dict:
    if not isinstance(user_cfg, dict):
        return defaults
    merged = {}
    for k, v_def in defaults.items():
        if k in user_cfg:
            v_usr = user_cfg[k]
            if isinstance(v_def, dict) and isinstance(v_usr, dict):
                merged[k] = _merge_defaults(v_usr, v_def)
            else:
                merged[k] = v_usr
        else:
            merged[k] = v_def
    for k, v in user_cfg.items():
        if k not in merged:
            merged[k] = v
    return merged

def ensure_config_exists():
    if not os.path.exists(CONFIG_PATH):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            yaml.safe_dump(DEFAULT_CONFIG, f, sort_keys=False)
        log.info("Created default config at %s", CONFIG_PATH)

def load_config():
    ensure_config_exists()
    try:
        with open(CONFIG_PATH, "r") as f:
            user_cfg = yaml.safe_load(f) or {}
    except Exception:
        with open(CONFIG_PATH, "w") as wf:
            yaml.safe_dump(DEFAULT_CONFIG, wf, sort_keys=False)
        log.warning("Config unreadable; rewrote defaults at %s", CONFIG_PATH)
        return DEFAULT_CONFIG
    merged = _merge_defaults(user_cfg, DEFAULT_CONFIG)
    if merged != user_cfg:
        try:
            with open(CONFIG_PATH, "w") as f:
                yaml.safe_dump(merged, f, sort_keys=False)
            log.info("Normalised config with defaults (saved)")
        except Exception:
            pass
    return merged

def save_config(cfg: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

cfg = load_config()

    

from services.disks import read_unraid_disks

    # Serial helpers moved to services.serial

# ---------- Auto-apply PWM state ----------
_AUTO_LAST_DUTY: int | None = None
_AUTO_LAST_TS: float | None = None
_AUTO_PAUSED_MSG: str | None = None

#
# ---------- Serial send helpers used by API ----------
#
# get currently preferred port (same logic as get_serial_status)
from typing import Any
import json as _json
import urllib.request
import urllib.error
import tempfile
import shutil
import subprocess
import stat


# ---------- RP update helpers ----------
def _has_cap_sys_admin() -> bool:
    """Return True only if CAP_SYS_ADMIN is effective in this process.

    The previous heuristic treated any non-zero CapEff as privileged and
    even fell back to checking for /dev/disk/by-label, which can be present
    in unprivileged containers via bind mounts. This precise check reads
    CapEff from /proc/self/status, interprets it as a hex bitmask, and
    returns True iff bit 21 (CAP_SYS_ADMIN) is set.
    """
    try:
        with open("/proc/self/status", "r", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                if ln.startswith("CapEff:"):
                    hexmask = ln.split(":", 1)[1].strip()
                    # CapEff is hex; CAP_SYS_ADMIN is bit 21
                    mask = int(hexmask, 16)
                    return bool(mask & (1 << 21))
    except Exception:
        pass
    return False

def _usb_info_for_port(port: str | None) -> dict:
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

def _http_get_json(url: str, timeout: float = 6.0) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "fanbridge/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                data = resp.read()
                return _json.loads(data.decode("utf-8", errors="ignore"))
    except Exception:
        return None
    return None

def _select_latest_for_board(items: list[dict], board: str) -> dict | None:
    # items: [{board, version, url}]
    def parse_ver(v: str) -> tuple:
        try:
            core = v.strip().lstrip("vV")
            parts = core.split("-")[0]
            nums = [int(x) for x in parts.split(".") if x.isdigit()]
            return tuple(nums + [0] * (3 - len(nums)))
        except Exception:
            return (0, 0, 0)
    candidates = [it for it in (items or []) if str(it.get("board", "")).lower() == str(board).lower()]
    candidates.sort(key=lambda it: parse_ver(str(it.get("version", "0"))), reverse=True)
    return candidates[0] if candidates else None

def _find_rp2_dev_symlink() -> str | None:
    try:
        path = "/dev/disk/by-label/RPI-RP2"
        if os.path.exists(path):
            tgt = os.path.realpath(path)
            if tgt and os.path.exists(tgt):
                return tgt
    except Exception:
        pass
    return None

def _is_block_device(dev: str) -> bool:
    try:
        st = os.stat(dev)
        return stat.S_ISBLK(st.st_mode)
    except Exception:
        return False

def _sys_read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    except Exception:
        return ""

def _probe_partition_is_rp2(dev: str) -> bool:
    # Best-effort: mount RO, check for INFO_UF2.TXT or INDEX.HTM
    tmp = tempfile.mkdtemp(prefix="rp2probe-")
    try:
        cp = subprocess.run(["mount", "-o", "ro", dev, tmp], capture_output=True, text=True, timeout=5)
        if cp.returncode != 0:
            return False
        try:
            names = set(os.listdir(tmp))
            return ("INFO_UF2.TXT" in names) or ("INDEX.HTM" in names)
        except Exception:
            return False
    finally:
        try:
            _umount(tmp)
        except Exception:
            pass
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass

def _find_rp2_block_device() -> str | None:
    # 1) Try the by-label symlink first
    dev = _find_rp2_dev_symlink()
    if dev and _is_block_device(dev):
        return dev
    # 2) Scan removable block devices and probe partitions
    try:
        for b in sorted(glob.glob("/sys/block/sd*")):
            # removable flag (1 = removable)
            name = os.path.basename(b)
            rem = _sys_read(f"/sys/block/{name}/removable")
            if rem != "1":
                continue
            # partitions (e.g., sda1, sdb1)
            parts = sorted(glob.glob(f"/sys/block/{name}/{name}[0-9]"))
            for p in parts:
                part = os.path.basename(p)
                devnode = f"/dev/{part}"
                if not _is_block_device(devnode):
                    continue
                try:
                    if _probe_partition_is_rp2(devnode):
                        return devnode
                except Exception:
                    continue
    except Exception:
        pass
    return None

def _mount_device(dev: str, mount_point: str) -> tuple[bool, str | None]:
    try:
        os.makedirs(mount_point, exist_ok=True)
        # Use sync + umask for permissive writes
        args = ["mount", "-o", "sync,umask=000", dev, mount_point]
        cp = subprocess.run(args, capture_output=True, text=True, timeout=8)
        if cp.returncode == 0:
            return True, None
        # Fallback: try explicitly as vfat (RP2 uses FAT)
        cp2 = subprocess.run(["mount", "-t", "vfat", "-o", "sync,umask=000", dev, mount_point], capture_output=True, text=True, timeout=8)
        if cp2.returncode == 0:
            return True, None
        err = (cp.stderr or cp.stdout or "").strip()
        if not err:
            err = (cp2.stderr or cp2.stdout or "").strip()
        return False, err or "mount failed"
    except Exception as e:
        return False, str(e)

def _umount(mount_point: str) -> None:
    try:
        subprocess.run(["umount", mount_point], capture_output=True, text=True, timeout=5)
    except Exception:
        pass


# ---------- PWM logic ----------
def map_temp_to_pwm(temp: int, thresholds: list[int], pwms: list[int]) -> int:
    step = 0
    for i, th in enumerate(thresholds):
        if temp >= th:
            step = i
        else:
            break
    return int(pwms[step])

def compute_status():
    global cfg
    cfg = load_config()

    # Production: prefer Unraid's disks.ini; do not fabricate drives in Docker.
    # Local dev (not in Docker) may still use the sim data in config for testing.
    if os.path.exists(DISKS_INI):
        mode = "unraid"
        try:
            excludes = set(cfg.get("exclude_devices") or [])
        except Exception:
            excludes = set()
        drives = read_unraid_disks(DISKS_INI, excludes)
    else:
        if _in_docker():
            # In Docker with no disks.ini mapped: run with empty drives (fallback PWM logic applies)
            mode = "unraid-missing"
            drives = []
        else:
            # Local dev-only: allow sim
            mode = "sim"
            drives = []
            for d in (cfg.get("sim", {}).get("drives", []) or []):
                _dtype = d.get("type", "HDD")
                _temp = d.get("temp")
                if _dtype == "HDD":
                    _state = "down" if _temp is None else "up"
                else:
                    _state = "spun down" if _temp is None else "on"
                drives.append({
                    "dev": d.get("name"),
                    "type": _dtype,
                    "temp": _temp,
                    "state": _state,
                    "excluded": False,
                })

    # log any N/A for visibility
    for d in drives:
        if d["state"] == "N/A":
            log.warning("disks.ini has no temp | dev=%s type=%s", d['dev'], d['type'])

    # Pool stats (respect user excludes)
    hdd_vals = [d["temp"] for d in drives if d.get("type") == "HDD" and not d.get("excluded") and d.get("temp") is not None]
    ssd_vals = [d["temp"] for d in drives if d.get("type") == "SSD" and not d.get("excluded") and d.get("temp") is not None]

    def stats(vals):
        if not vals:
            return {"avg": 0, "min": 0, "max": 0, "count": 0}
        return {"avg": int(sum(vals)/len(vals)), "min": min(vals), "max": max(vals), "count": len(vals)}

    hdd = stats(hdd_vals)
    ssd = stats(ssd_vals)

    # Overrides + curves
    override = False
    if hdd_vals and max(hdd_vals) >= int(cfg.get("single_override_hdd_c", 45)): override = True
    if ssd_vals and max(ssd_vals) >= int(cfg.get("single_override_ssd_c", 60)): override = True

    if override:
        recommended_pwm = int(cfg.get("override_pwm", 100))
    else:
        pwm_hdd = map_temp_to_pwm(hdd["avg"], cfg["hdd_thresholds"], cfg["hdd_pwm"]) if hdd["count"] else 0
        pwm_ssd = map_temp_to_pwm(ssd["avg"], cfg["ssd_thresholds"], cfg["ssd_pwm"]) if ssd["count"] else 0
        recommended_pwm = max(pwm_hdd, pwm_ssd)
        if hdd["count"] == 0 and ssd["count"] == 0:
            recommended_pwm = int(cfg.get("fallback_pwm", 10))

    disks_mtime = None
    try:
        if os.path.exists(DISKS_INI):
            disks_mtime = int(os.path.getmtime(DISKS_INI))
        else:
            _warn_once(
                "disks_ini_missing",
                f"Could not read {DISKS_INI}; Unraid mapping missing. Map /var/local/emhttp -> /unraid:ro or bind /var/local/emhttp/disks.ini -> /unraid/disks.ini:ro",
            )
    except Exception as e:
        try:
            logging.getLogger("fanbridge").warning("Failed to stat %s: %s", DISKS_INI, e)
        except Exception:
            pass

    # Auto-apply PWM to controller if enabled and safe
    auto_enabled = bool(cfg.get("auto_apply"))
    auto_last_duty = _AUTO_LAST_DUTY
    auto_last_ts = _AUTO_LAST_TS
    auto_paused_msg = None
    if auto_enabled:
        try:
            # Only attempt if serial looks connected
            sstat = serial_svc.get_serial_status(full=False)
            if not sstat.get("connected"):
                auto_paused_msg = "controller not connected"
            else:
                # Recommended PWM as percent 0–100
                pct = int(recommended_pwm)
                if pct < 0: pct = 0
                if pct > 100: pct = 100
                # Derive a duty 0..255 only for hysteresis comparison; we send percent to the controller.
                duty = int(round(pct * 255 / 100))
                # Apply hysteresis and min interval
                min_ivl = int(cfg.get("auto_apply_min_interval_s", 3) or 3)
                hyst = int(cfg.get("auto_apply_hysteresis_duty", 5) or 5)
                now_ts = time.time()
                delta_ok = (_AUTO_LAST_DUTY is None) or (abs(duty - int(_AUTO_LAST_DUTY)) >= max(0, hyst))
                ivl_ok = (_AUTO_LAST_TS is None) or ((now_ts - float(_AUTO_LAST_TS)) >= max(1, min_ivl))
                if delta_ok and ivl_ok:
                    res = serial_svc.serial_set_pwm_percent(pct)
                    if res.get("ok"):
                        auto_last_duty = duty
                        auto_last_ts = now_ts
                        globals()["_AUTO_LAST_DUTY"] = duty
                        globals()["_AUTO_LAST_TS"] = now_ts
                    else:
                        auto_paused_msg = str(res.get("error") or "send failed")
        except Exception as e:
            try:
                logging.getLogger("fanbridge").warning("auto-apply error: %s", e)
            except Exception:
                pass
            auto_paused_msg = "auto-apply error"

    payload = {
        "drives": drives,
        "hdd": hdd,
        "ssd": ssd,
        "override_hdd_c": int(cfg.get("single_override_hdd_c", 45)),
        "override_ssd_c": int(cfg.get("single_override_ssd_c", 60)),
        "exclude_devices": sorted(list(set(cfg.get("exclude_devices") or []))),
        "hdd_thresholds": cfg.get("hdd_thresholds", []),
        "hdd_pwm": cfg.get("hdd_pwm", []),
        "ssd_thresholds": cfg.get("ssd_thresholds", []),
        "ssd_pwm": cfg.get("ssd_pwm", []),
        "recommended_pwm": int(recommended_pwm),
        "override": override,
        "mode": mode,
        "version": APP_VERSION,
        "disks_ini_mtime": disks_mtime,
        "disks_stale_warn_s": int(DISKS_STALE_WARN_SEC),
        # Auto-apply reporting
        "auto_apply": auto_enabled,
        "auto_last_duty": int(auto_last_duty) if auto_last_duty is not None else None,
        "auto_last_ts": int(auto_last_ts) if auto_last_ts is not None else None,
        "auto_paused": bool(auto_paused_msg),
        "auto_message": auto_paused_msg,
        # Auto-apply config values for client UI
        "auto_apply_min_interval_s": int(cfg.get("auto_apply_min_interval_s", 3) or 3),
        "auto_apply_hysteresis_duty": int(cfg.get("auto_apply_hysteresis_duty", 5) or 5),
    }
    try:
        if _dbg_should("status", 10):
            log.debug(
                "status | mode=%s hdd_avg=%s ssd_avg=%s pwm=%s drives=%s",
                mode, hdd.get("avg"), ssd.get("avg"), payload["recommended_pwm"], len(drives)
            )
        # One-time advisory if disks.ini is bind-mounted as a single file (can go stale on host rename)
        try:
            if _dbg_should("disks_ini_bind_advice", 9999999) and _is_bind_mounted_file(DISKS_INI):
                log.warning("/unraid/disks.ini is bind-mounted as a single file; map the directory /var/local/emhttp -> /unraid:ro to see instant updates")
        except Exception:
            pass
        # Warn periodically if disks.ini appears stale (> DISKS_STALE_WARN_SEC)
        if disks_mtime:
            try:
                if (time.time() - float(disks_mtime)) > max(60, int(DISKS_STALE_WARN_SEC)) and _dbg_should("disks_ini_stale_warn", 600):
                    age = int(time.time() - float(disks_mtime))
                    log.warning("/unraid/disks.ini appears stale | age_s=%s", age)
            except Exception:
                pass
    except Exception:
        pass
    return payload

@app.get("/health")
def health():
    return jsonify({"status": "ok", "uptime_s": int(time.time() - STARTED)})

# Toggle auto-apply on/off
@app.post("/api/auto_apply")
def api_auto_apply():
    data = request.get_json(force=True, silent=True) or {}
    enable = bool(data.get("enabled"))
    c = load_config()
    c["auto_apply"] = enable
    save_config(c)
    try:
        _audit("auto_apply.toggle", enabled=enable)
    except Exception:
        pass
    # If disabling, do not clear last duty/time so UI can display history
    return jsonify({"ok": True, "auto_apply": enable})

## moved: /api/logs*, /api/log_level handled by api.logs blueprint

@app.after_request
def add_no_cache(resp):
    # Make JSON responses always fresh in browsers / proxies
    if resp.mimetype == "application/json":
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    # Light security headers suitable for single-origin app
    try:
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        # CSP for the app: allow Ko‑fi iframe (frame) and https images. No external scripts.
        csp = (
            "default-src 'self'; "
            "img-src 'self' data: https:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; "
            "frame-src 'self' https://ko-fi.com"
        )
        resp.headers.setdefault("Content-Security-Policy", csp)
    except Exception:
        pass
    return resp

## moved: /api/app/version and /metrics handled by api.appinfo blueprint

# Request timing + logging
@app.before_request
def _req_start_timer():
    try:
        g._start_ts = time.time()
    except Exception:
        pass

@app.after_request
def _req_log(resp):
    try:
        dur_ms = int((time.time() - getattr(g, "_start_ts", time.time())) * 1000)
        path = request.path
        meth = request.method
        code = resp.status_code
        lg = logging.getLogger("fanbridge")
        try:
            _m_inc_http(meth, code)
        except Exception:
            pass
        # Always surface non-2xx responses
        if code >= 500:
            lg.error("%s %s -> %s in %sms", meth, path, code, dur_ms)
        elif code >= 400:
            lg.warning("%s %s -> %s in %sms", meth, path, code, dur_ms)
        else:
            # Success paths: skip logging for chatty endpoints entirely
            QUIET = ("/health", "/api/status", "/api/serial/status", "/api/logs", "/api/log_level", "/api/logs/download")
            if path in QUIET:
                pass
            else:
                lg.info("%s %s -> %s in %sms", meth, path, code, dur_ms)
    except Exception:
        pass
    return resp

@app.errorhandler(404)
def _not_found(e):
    logging.getLogger("fanbridge").warning("404 %s %s", request.method, request.path)
    return jsonify({"ok": False, "error": "not found", "path": request.path}), 404

@app.errorhandler(Exception)
def _unhandled(e):
    logging.getLogger("fanbridge").exception("Unhandled error for %s %s: %s", request.method, request.path, e)
    return jsonify({"ok": False, "error": str(e)}), 500

@app.before_request
def _auth_and_rate():
    p = request.path
    # allow public endpoints
    if p.startswith("/static/") or p in ("/login", "/health", "/api/app/version"):
        return
    # require login
    if "user" not in session:
        return redirect(url_for("login", next=request.path))

    # --- Rate limiting ---
    # Skip rate limiting for safe GETs to keep UI snappy
    if request.method == "GET":
        return

    # Per-endpoint buckets with relaxed limits for serial actions
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    key = "mutate"
    limit, window = 60, 60  # default for POST-ish

    if p == "/api/serial/send":
        key = "serial_send"
        limit, window = 120, 60   # allow ~2/sec
    elif p == "/api/serial/pwm":
        key = "serial_pwm"
        limit, window = 120, 60
    elif p in ("/api/settings", "/api/curves", "/api/reset_defaults", "/api/exclude", "/api/change_password"):
        key = p  # separate buckets for config endpoints
        limit, window = 30, 60

    if not _allow(ip, key, limit=limit, window=window):
        resp = make_response(("Too Many Requests", 429))
        # Provide a minimal Retry-After hint (seconds)
        resp.headers["Retry-After"] = "10"
        return resp

    # CSRF on modifying requests
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if not _require_csrf():
            return make_response(("Invalid CSRF token", 403))

@app.route("/login", methods=["GET", "POST"])
def login():
    users = _load_users()
    first_run = not users or not users.get("users")

    if request.method == "POST":
        if first_run:
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            confirm  = request.form.get("confirm") or ""
            if not username or not password or password != confirm:
                return render_template("login.html", first_run=True, error="Please fill all fields; passwords must match.")
            users = {"users": {username: generate_password_hash(password)}}
            _save_users(users)
            session["user"] = username
            _ensure_csrf_token()
            return redirect(url_for("index"))
        else:
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            stored = (users.get("users") or {}).get(username)
            if stored and check_password_hash(stored, password):
                session["user"] = username
                _ensure_csrf_token()
                nxt = request.args.get("next") or url_for("index")
                return redirect(nxt)
            return render_template("login.html", first_run=False, error="Invalid username or password.")

    # GET
    return render_template("login.html", first_run=first_run, error=None)

@app.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    if request.method == "POST":
        # JS caller expects a quick success without navigation
        return ("", 204)
    # For direct browser hits, send them to the login form
    return redirect(url_for("login"))

@app.get("/")
def index():
    try:
        pi = int((cfg or {}).get("poll_interval_seconds", 7))
    except Exception:
        pi = 7
    if pi < 3: pi = 3
    if pi > 60: pi = 60

    return render_template(
        "index.html",
        version=APP_VERSION,
        poll_s=pi,
        csrf_token=_ensure_csrf_token(),
        username=session.get("user", ""),
    )

@app.get("/api/status")
def status():
    data = compute_status()
    try:
        ss = serial_svc.get_serial_status(full=False)
        data["serial"] = {
            "preferred": ss.get("preferred"),
            "available": ss.get("available"),
            "connected": ss.get("connected"),
            "baud": ss.get("baud"),
            "message": ss.get("message"),
        }
    except Exception as e:
        logging.getLogger("fanbridge").exception("serial status embed failed: %s", e)
        data["serial"] = {"available": False, "connected": False, "message": "error"}
    return jsonify(data)


# --------- API: Serial status ---------
@app.get("/api/serial/status")
def serial_status():
    return jsonify(serial_svc.get_serial_status(full=True))

# --- new quick tools endpoint; add below /api/serial/status ---
@app.get("/api/serial/tools")
def api_serial_tools():
    # Always return fast: include status + an optional quick PING probe.
    status = serial_svc.get_serial_status(full=True)
    checks = {"ping": {"ok": False, "ms": None, "reply": None, "error": None}}
    if status.get("connected"):
        t0 = time.time()
        # Short timeout so UI never hangs long
        res = serial_svc.serial_send_line("PING", expect_reply=True, timeout=0.5)
        dt = int((time.time() - t0) * 1000)
        if res.get("ok"):
            checks["ping"] = {
                "ok": (res.get("reply") == "PONG"),
                "ms": dt,
                "reply": res.get("reply"),
                "error": None
            }
        else:
            checks["ping"] = {"ok": False, "ms": dt, "reply": res.get("reply"), "error": res.get("error")}
    else:
        checks["ping"] = {"ok": False, "ms": None, "reply": None, "error": "not connected"}
    # Flatten a few fields for convenience of older UI code
    payload = {
        "ok": bool(status.get("connected")),
        "connected": bool(status.get("connected")),
        "preferred": status.get("preferred"),
        "port": status.get("preferred"),
        "baud": status.get("baud"),
        "message": status.get("message"),
        "status": status,
        "checks": checks,
    }
    return jsonify(payload)


# --------- API: Serial send a raw line ---------
@app.post("/api/serial/send")
def api_serial_send():
    data = request.get_json(force=True, silent=True) or {}
    line = (data.get("line") or "").strip()
    if not line:
        return jsonify({"ok": False, "error": "missing line"}), 400

    res = serial_svc.serial_send_line(line, expect_reply=True)
    # log succinctly
    if res.get("ok"):
        log.info("serial send | port=%s echo=%r reply=%r", res.get("port"), line, res.get("reply"))
        try: _m_inc_serial_cmd("send", "ok")
        except Exception: pass
    else:
        log.warning("serial send failed | echo=%r err=%s", line, res.get("error") or "unknown")
        try: _m_inc_serial_cmd("send", "error")
        except Exception: pass
    code = 200 if res.get("ok") else 503
    return jsonify(res), code


# --------- API: Serial set PWM (0..255 raw duty) ---------
@app.post("/api/serial/pwm")
def api_serial_pwm():
    data = request.get_json(force=True, silent=True) or {}
    if "value" not in data:
        return jsonify({"ok": False, "error": "missing value"}), 400
    # Accept 0..100% from client
    res = serial_svc.serial_set_pwm_percent(data.get("value"))
    if res.get("ok"):
        log.info("serial pwm | port=%s value=%s reply=%r", res.get("port"), res.get("value"), res.get("reply"))
        try: _m_inc_serial_cmd("pwm", "ok")
        except Exception: pass
    else:
        log.warning("serial pwm failed | value=%s err=%s", data.get("value"), res.get("error") or "unknown")
        try: _m_inc_serial_cmd("pwm", "error")
        except Exception: pass
    code = 200 if res.get("ok") else 503
    return jsonify(res), code



# --------- API: RP status ---------
@app.get("/api/rp/status")
def api_rp_status():
    c = load_config()
    rp_cfg = c.get("rp", {}) if isinstance(c, dict) else {}
    repo_url = rp_cfg.get("repo_url") or DEFAULT_CONFIG["rp"]["repo_url"]
    board = rp_cfg.get("board") or "rp2040"

    # Serial + controller version
    sstat = serial_svc.get_serial_status(full=True)
    ver = None
    if sstat.get("connected"):
        try:
            vres = serial_svc.serial_send_line("version", expect_reply=True, timeout=0.5)
            if vres.get("ok"):
                ver = (vres.get("reply") or "").strip() or None
        except Exception:
            pass
    usb = serial_svc.usb_info_for_port(sstat.get("preferred"))

    # Try to fetch repo manifest (optional)
    manifest_url = repo_url.rstrip("/") + "/manifest.json"
    manifest = _http_get_json(manifest_url)
    latest: dict | None = None
    update_available = False
    if isinstance(manifest, dict) and isinstance(manifest.get("items"), list):
        items: list[dict] = list(manifest.get("items") or [])
        latest = _select_latest_for_board(items, board)
        if latest and ver:
            try:
                update_available = str(latest.get("version")) != str(ver)
            except Exception:
                update_available = False

    payload = {
        "ok": True,
        "privileged": _has_cap_sys_admin(),
        "serial": sstat,
        "usb": usb,
        "controller_version": ver,
        "repo_url": repo_url,
        "board": board,
        "rp2_device": str(rp_cfg.get("rp2_device") or ""),
        "manifest_url": manifest_url,
        "latest": latest,
        "update_available": bool(update_available),
    }
    return jsonify(payload)


# --------- API: Set RP repo URL ---------
@app.post("/api/rp/repo")
def api_rp_repo():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("repo_url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "missing repo_url"}), 400
    c = load_config()
    if "rp" not in c or not isinstance(c["rp"], dict):
        c["rp"] = {}
    c["rp"]["repo_url"] = url
    try:
        save_config(c)
    except Exception:
        pass
    try:
        _audit("rp.repo.update", repo_url=url)
    except Exception:
        pass
    return jsonify({"ok": True, "repo_url": url})

# --------- API: Set preferred RP2 device path ---------
@app.post("/api/rp/rp2_device")
def api_rp_set_device():
    data = request.get_json(force=True, silent=True) or {}
    path = (data.get("rp2_device") or "").strip()
    c = load_config()
    if "rp" not in c or not isinstance(c["rp"], dict):
        c["rp"] = {}
    c["rp"]["rp2_device"] = path
    try:
        save_config(c)
    except Exception:
        pass
    try:
        _audit("rp.rp2_device.update", rp2_device=path)
    except Exception:
        pass
    return jsonify({"ok": True, "rp2_device": path})


# --------- API: Flash latest firmware ---------
@app.post("/api/rp/flash")
def api_rp_flash():
    data = request.get_json(force=True, silent=True) or {}
    steps: list[dict] = []
    def logstep(msg: str, ok: bool | None = None, **kv):
        entry = {"ts": int(time.time()), "msg": msg}
        if ok is not None:
            entry["ok"] = bool(ok)
        if kv:
            entry.update(kv)
        steps.append(entry)
    try:
        board = (data.get("board") or "rp2040").strip()
        version = (data.get("version") or "").strip() or None
        # Ensure we have serial connection to trigger BOOTSEL
        sstat = serial_svc.get_serial_status(full=True)
        if not sstat.get("connected"):
            logstep("controller not connected", ok=False)
            return jsonify({"ok": False, "error": "controller not connected", "progress": steps}), 400

        c = load_config()
        rp_cfg = c.get("rp", {}) if isinstance(c, dict) else {}
        repo_url = rp_cfg.get("repo_url") or DEFAULT_CONFIG["rp"]["repo_url"]

        # Fetch manifest to determine URL
        manifest_url = repo_url.rstrip("/") + "/manifest.json"
        logstep("fetching manifest", url=manifest_url)
        manifest = _http_get_json(manifest_url)
        if not (isinstance(manifest, dict) and isinstance(manifest.get("items"), list)):
            logstep("manifest not available", ok=False)
            return jsonify({"ok": False, "error": "manifest not available", "progress": steps}), 400
        items: list[dict] = list(manifest.get("items") or [])
        item: dict | None = None
        if version:
            for it in items:
                if str(it.get("board")).lower() == board.lower() and str(it.get("version")) == version:
                    item = it; break
        else:
            item = _select_latest_for_board(items, board)
        if not item or not item.get("url"):
            logstep("firmware not found for board/version", ok=False)
            return jsonify({"ok": False, "error": "firmware not found for board/version", "progress": steps}), 404

        fw_url = str(item.get("url"))

        # 1) Reboot controller into BOOTSEL
        logstep("sending BOOTSEL")
        serial_svc.serial_send_line("BOOTSEL", expect_reply=False)
        # Small grace period for USB disconnect
        time.sleep(0.6)
        # 2) Poll for RPI-RP2 block device (preferred path, by-label or probed)
        dev = None
        deadline = time.time() + 40.0
        logstep("waiting for RPI-RP2 device")
        while time.time() < deadline:
            # Preferred path from config, if present
            try:
                c2 = load_config()
                pref_dev = (c2.get("rp", {}) or {}).get("rp2_device") if isinstance(c2, dict) else ""
                if pref_dev and os.path.exists(pref_dev) and _is_block_device(pref_dev):
                    dev = str(pref_dev)
            except Exception:
                pass
            if not dev:
                dev = _find_rp2_dev_symlink() or _find_rp2_block_device()
            if dev:
                logstep("found RPI-RP2 device", ok=True, device=dev)
                break
            time.sleep(0.6)
        if not dev:
            logstep("RPI-RP2 device not detected (is container privileged?)", ok=False)
            return jsonify({"ok": False, "error": "RPI-RP2 device not detected (is container privileged?)", "progress": steps}), 503

        # 3) Mount, download, copy UF2, unmount
        mnt = tempfile.mkdtemp(prefix="rp2-")
        logstep("mounting RP2", mount=mnt)
        ok, err = _mount_device(dev, mnt)
        if not ok:
            try: shutil.rmtree(mnt, ignore_errors=True)
            except Exception: pass
            logstep("mount failed", ok=False, error=str(err or "unknown"))
            return jsonify({"ok": False, "error": f"mount failed: {err}", "progress": steps}), 503
        tmp_uf2 = None
        try:
            # Download to temp file
            tmp_fd, tmp_path = tempfile.mkstemp(prefix="fw-", suffix=".uf2")
            os.close(tmp_fd)
            tmp_uf2 = tmp_path
            try:
                logstep("downloading UF2", url=fw_url)
                req = urllib.request.Request(fw_url, headers={"User-Agent": "fanbridge/1.0"})
                with urllib.request.urlopen(req, timeout=20) as resp, open(tmp_path, "wb") as wf:
                    shutil.copyfileobj(resp, wf)
            except Exception as e:
                logstep("download failed", ok=False, error=str(e))
                return jsonify({"ok": False, "error": f"download failed: {e}", "progress": steps}), 502
            # Copy to mounted volume (any filename is fine)
            dst = os.path.join(mnt, os.path.basename(tmp_path) or "update.uf2")
            try:
                size = os.path.getsize(tmp_path)
                logstep("copying UF2 to RP2", bytes=size, dst=dst)
                shutil.copy2(tmp_path, dst)
                # give ROM time to program before unmount
                time.sleep(1.0)
            except Exception as e:
                logstep("copy failed", ok=False, error=str(e))
                return jsonify({"ok": False, "error": f"copy failed: {e}", "progress": steps}), 503
            # After flashing, wait for CDC device to re-enumerate and try to read version
            re_ver = None
            logstep("waiting for device to re-enumerate")
            t_end = time.time() + 30.0
            while time.time() < t_end:
                try:
                    res = serial_svc.serial_send_line("version", expect_reply=True, timeout=0.5)
                    if res.get("ok") and res.get("reply"):
                        re_ver = (res.get("reply") or "").strip() or None
                        logstep("controller version read", ok=True, version=re_ver)
                        break
                except Exception:
                    pass
                time.sleep(0.5)
            # Try a quick PING
            ping_ok = None
            try:
                pres = serial_svc.serial_send_line("PING", expect_reply=True, timeout=0.6)
                ping_ok = (pres.get("reply") == "PONG")
                logstep("ping test", ok=bool(ping_ok), reply=pres.get("reply"))
            except Exception as e:
                logstep("ping test error", ok=False, error=str(e))
            logstep("finished", ok=True)
            return jsonify({"ok": True, "device": dev, "mount": mnt, "url": fw_url, "version": item.get("version"), "controller_version": re_ver, "progress": steps})
        finally:
            try:
                _umount(mnt)
            except Exception:
                pass
            try:
                shutil.rmtree(mnt, ignore_errors=True)
            except Exception:
                pass
            if tmp_uf2:
                try: os.remove(tmp_uf2)
                except Exception: pass
    except Exception as e:
        # Surface unexpected exceptions to the progress log and client
        try:
            logstep("unexpected error", ok=False, error=str(e))
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(e), "progress": steps}), 500

# --------- API: Exclude device ---------
@app.post("/api/exclude")
def api_exclude():
    data = request.get_json(force=True, silent=True) or {}
    dev = (data.get("dev") or "").strip()
    if not dev:
        return jsonify({"ok": False, "error": "missing dev"}), 400
    excluded = bool(data.get("excluded"))
    c = load_config()
    current = set(c.get("exclude_devices") or [])
    if excluded:
        current.add(dev)
    else:
        current.discard(dev)
    c["exclude_devices"] = sorted(current)
    save_config(c)
    try:
        _audit("exclude.update", device=dev, excluded=excluded)
    except Exception:
        pass
    return jsonify({"ok": True, "exclude_devices": c["exclude_devices"]})


# --------- API: Change password (authenticated) ---------
@app.post("/api/change_password")
def api_change_password():
    # must be logged in due to @app.before_request
    user = session.get("user")
    if not user:
        return jsonify({"ok": False, "error": "not authenticated"}), 401

    data = request.get_json(force=True, silent=True) or {}
    current = (data.get("current") or "").strip()
    new = (data.get("new") or "").strip()
    confirm = (data.get("confirm") or "").strip()

    if not current or not new or not confirm:
        return jsonify({"ok": False, "error": "all fields required"}), 400
    if new != confirm:
        return jsonify({"ok": False, "error": "passwords do not match"}), 400

    users = _load_users()
    stored = (users.get("users") or {}).get(user)
    if not stored or not check_password_hash(stored, current):
        return jsonify({"ok": False, "error": "current password is incorrect"}), 400

    # update hash
    users.setdefault("users", {})[user] = generate_password_hash(new)
    _save_users(users)
    try:
        _audit("auth.password_changed", user=user)
    except Exception:
        pass
    return jsonify({"ok": True})


# --------- API: Settings overrides ---------
@app.post("/api/settings")
def api_settings():
    data = request.get_json(force=True, silent=True) or {}
    c = load_config()
    changed = {}

    def set_int(key, default, clamp: tuple[int,int] | None = None):
        v = data.get(key, None)
        if v is None:
            return
        try:
            iv = int(str(v).strip())
            if clamp:
                lo, hi = clamp
                if iv < lo: iv = lo
                if iv > hi: iv = hi
            c[key] = iv
            changed[key] = iv
        except Exception:
            pass

    set_int("single_override_hdd_c", c.get("single_override_hdd_c", 45))
    set_int("single_override_ssd_c", c.get("single_override_ssd_c", 60))
    # New: allow changing the UI refresh interval (3–60s)
    set_int("poll_interval_seconds", c.get("poll_interval_seconds", 7), clamp=(3, 60))
    # Auto-apply tuning (optional)
    set_int("auto_apply_min_interval_s", c.get("auto_apply_min_interval_s", 3), clamp=(1, 60))
    set_int("auto_apply_hysteresis_duty", c.get("auto_apply_hysteresis_duty", 5), clamp=(0, 64))

    save_config(c)
    try:
        if changed:
            _audit("settings.update", changed=changed)
    except Exception:
        pass
    return jsonify({"ok": True, "changed": changed})


# --------- API: Curves and idle cutoffs ---------
@app.post("/api/curves")
def api_curves():
    data = request.get_json(force=True, silent=True) or {}
    c = load_config()
    changed = {}
    def set_list_int(key):
        v = data.get(key)
        if isinstance(v, list):
            try:
                arr = [int(x) for x in v][:32]
                if not arr:
                    return
                c[key] = arr
                changed[key] = arr
            except Exception:
                pass
    def set_int_key(key):
        v = data.get(key)
        if v is None:
            return
        try:
            c[key] = int(v)
            changed[key] = int(v)
        except Exception:
            pass
    set_list_int("hdd_thresholds")
    set_list_int("hdd_pwm")
    set_list_int("ssd_thresholds")
    set_list_int("ssd_pwm")
    save_config(c)
    try:
        if changed:
            # Log sizes to keep log lines readable; include first few values
            summary = {}
            for k, arr in changed.items():
                if isinstance(arr, list):
                    summary[k] = {"len": len(arr), "head": arr[:8]}
                else:
                    summary[k] = arr
            _audit("curves.update", changed=summary)
    except Exception:
        pass
    return jsonify({"ok": True, "changed": changed})


# --------- API: Reset to defaults (overrides + fan curves) ---------
@app.post("/api/reset_defaults")
def api_reset_defaults():
    # Reset configurable fields to DEFAULT_CONFIG:
    # - single_override_hdd_c / single_override_ssd_c
    # - hdd_thresholds / hdd_pwm
    # - ssd_thresholds / ssd_pwm
    # - poll_interval_seconds (UI refresh default)
    try:
        c = load_config()
        defaults = DEFAULT_CONFIG if 'DEFAULT_CONFIG' in globals() else {}

        keys = [
            "single_override_hdd_c",
            "single_override_ssd_c",
            "hdd_thresholds",
            "hdd_pwm",
            "ssd_thresholds",
            "ssd_pwm",
            "poll_interval_seconds",
            "auto_apply",
            "auto_apply_min_interval_s",
            "auto_apply_hysteresis_duty",
        ]
        for k in keys:
            if k in defaults:
                c[k] = defaults[k]

        save_config(c)
        try:
            _audit("settings.reset_defaults", keys=keys)
        except Exception:
            pass
        return jsonify({
            "ok": True,
            "single_override_hdd_c": c.get("single_override_hdd_c"),
            "single_override_ssd_c": c.get("single_override_ssd_c"),
            "hdd_thresholds": c.get("hdd_thresholds"),
            "hdd_pwm": c.get("hdd_pwm"),
            "ssd_thresholds": c.get("ssd_thresholds"),
            "ssd_pwm": c.get("ssd_pwm"),
            "poll_interval_seconds": c.get("poll_interval_seconds"),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    APP_VERSION = "local"
    app.secret_key = _load_or_create_secret()
    try:
        app.config["TEMPLATES_AUTO_RELOAD"] = True
        app.jinja_env.auto_reload = True
    except Exception:
        pass
    # Local dev conveniences: show URL and optionally open browser
    host = "0.0.0.0"
    port = 8080
    url = f"http://127.0.0.1:{port}"
    try:
        log.info("Serving locally | url=%s host=%s port=%s", url, host, port)
    except Exception:
        pass
    try:
        if os.environ.get("FANBRIDGE_OPEN_BROWSER", "1") == "1":
            import threading, webbrowser
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    except Exception:
        pass
    app.run(host=host, port=port, debug=True)
