from flask import Flask, jsonify, request, render_template, session, redirect, url_for, make_response, g
import atexit, os, time, yaml, glob, pathlib, logging, sys, datetime, secrets
import copy, re, tempfile, threading, hashlib, shutil, struct, subprocess
from typing import Protocol, runtime_checkable
from services import serial as serial_svc
from api.serial import bp as serial_bp
from api.appinfo import bp as appinfo_bp
from api.logs import bp as logs_bp
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import HTTPException

try:
    from dotenv import load_dotenv  
except Exception:
    load_dotenv = None  

try:
    import serial.tools.list_ports as list_ports
except Exception:
    list_ports = None

_BASE = pathlib.Path(__file__).resolve().parent
_PROJECT_ROOT = _BASE.parent 
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 256
_SETUP_TOKEN_BANNER_WRITTEN = False


@runtime_checkable
class SerialProto(Protocol):
    pass  # maintained for legacy type references; implementation moved to services.serial

def _secret_path() -> pathlib.Path:
    # persist a stable session secret across restarts
    configured = os.environ.get("FANBRIDGE_SECRET_PATH", "").strip()
    if configured:
        return pathlib.Path(configured)
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
                os.chmod(p, 0o600)
                return key

        # create if missing/empty
        p.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_urlsafe(48)
        try:
            fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(key)
                f.flush()
                os.fsync(f.fileno())
        except FileExistsError:
            import time
            for _ in range(10):
                k2 = p.read_text(encoding="utf-8").strip()
                if k2:
                    return k2
                time.sleep(0.05)
        os.chmod(p, 0o600)
        return key
    except Exception as exc:
        # An ephemeral key logs every user out after a restart and used to
        # differ between Gunicorn workers.  A container must fail clearly if
        # its persistent secret cannot be created.
        if _in_docker():
            raise RuntimeError(f"cannot persist session secret at {p}: {exc}") from exc
        return secrets.token_urlsafe(48)

def _in_docker() -> bool:
    try:
        return os.path.exists("/.dockerenv")
    except Exception:
        return False


def _setup_token_path() -> pathlib.Path:
    configured = os.environ.get("FANBRIDGE_SETUP_TOKEN_PATH", "").strip()
    if configured:
        return pathlib.Path(configured)
    return (_BASE / "setup.token") if not _in_docker() else pathlib.Path("/config/setup.token")


def _write_setup_token_banner(token: str) -> None:
    global _SETUP_TOKEN_BANNER_WRITTEN
    if _SETUP_TOKEN_BANNER_WRITTEN:
        return
    _SETUP_TOKEN_BANNER_WRITTEN = True

    width = max(72, len(token) + 4)
    inner_width = width - 4

    def banner_row(text: str = "") -> str:
        return f"| {text:<{inner_width}} |\n"

    sys.stderr.write("\n+" + ("=" * (width - 2)) + "+\n")
    sys.stderr.write(banner_row("FANBRIDGE FIRST RUN SETUP TOKEN"))
    sys.stderr.write("+" + ("-" * (width - 2)) + "+\n")
    sys.stderr.write(banner_row(token))
    sys.stderr.write("+" + ("-" * (width - 2)) + "+\n")
    sys.stderr.write(banner_row("Enter this token on the first run setup screen."))
    sys.stderr.write(banner_row("A copy is stored at /config/setup.token until setup is complete."))
    sys.stderr.write("+" + ("=" * (width - 2)) + "+\n\n")
    sys.stderr.flush()


def _load_or_create_setup_token() -> str:
    """Return the one-time first-run token without exposing it over HTTP."""
    configured = os.environ.get("FANBRIDGE_SETUP_TOKEN", "").strip()
    if configured:
        return configured
    path = _setup_token_path()
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            os.chmod(path, 0o600)
            _write_setup_token_banner(token)
            return token
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        token = path.read_text(encoding="utf-8").strip()
    os.chmod(path, 0o600)
    # Write only to the container console. The application ring buffer is
    # downloadable as a support bundle and must never retain this credential.
    _write_setup_token_banner(token)
    return token

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
)
from core.http import http_get_firmware_asset, http_get_json

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
        # Do not trust X-Forwarded-For unless ProxyFix has been explicitly
        # configured for a known reverse proxy.
        ip = request.remote_addr or ""
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
    MAX_CONTENT_LENGTH=4 * 1024 * 1024,
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
DISKS_INI = os.environ.get("FANBRIDGE_DISKS_INI", "/unraid/disks.ini")

USERS_PATH = os.environ.get("FANBRIDGE_USERS") or (
    "/config/users.yml" if _in_docker() else str((_BASE / "users.local.yml"))
)

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
    # Registration is idempotent for test imports, but startup failures are
    # never swallowed: a half-built hardware service must not look healthy.
    if serial_bp.name not in app.blueprints:
        app.register_blueprint(serial_bp, url_prefix="/api/serial")
    # Provide app-wide context for blueprints (read-only values/functions)
    app.config['FB_APP_INFO'] = {
        'CONFIG_PATH': CONFIG_PATH,
        'USERS_PATH': USERS_PATH,
        'DISKS_INI': DISKS_INI,
        'STARTED': STARTED,
        'APP_VERSION': APP_VERSION,
        'IN_DOCKER_FUNC': _in_docker,
    }
    if appinfo_bp.name not in app.blueprints:
        app.register_blueprint(appinfo_bp, url_prefix="/api")
    if logs_bp.name not in app.blueprints:
        app.register_blueprint(logs_bp, url_prefix="/api")
    # Final safeguard: ensure our ring handler is present once the app is built
    _ensure_log_handlers()
    return app

# Ensure blueprints are registered when imported under WSGI servers (gunicorn)
# which typically load `app:app`. `create_app()` wires up API blueprints onto
# the global `app` object; call it here so routes like /api/logs are present.
app = create_app()

# Initialize serial service context
serial_svc.init(
    logger=log,
    dbg_should=_dbg_should,
    inc_open_fail=_m_inc_serial_open_fail,
    inc_serial_cmd=_m_inc_serial_cmd,
)

DEFAULT_CONFIG = {
    "schema_version": 3,
    "controllers": [],
    "poll_interval_seconds": 7,     # UI refresh; clamped 3–60s
    "control_interval_seconds": 10,
    "hdd_thresholds": [30,32,35,38,40,42,44,45],
    "hdd_pwm":        [0,20,30,40,50,60,80,100],
    "ssd_thresholds": [35,40,45,48,50,52,54,55],
    "ssd_pwm":        [0,20,30,40,55,70,85,100],
    "single_override_hdd_c": 45,
    "single_override_ssd_c": 60,
    "override_pwm": 100,
    "fallback_pwm": 10,
    "failsafe_pwm": 100,
    "pwm_hysteresis": 3,
    "exclude_devices": [],
    "drive_assignments": {},
    "idle_cutoff_hdd_c": 30,  
    "idle_cutoff_ssd_c": 35,  
    # Auto-apply PWM to microcontroller (local/dev or container) — disabled by default
    "auto_apply": False,
    "auto_apply_min_interval_seconds": 3,
    "auto_apply_refresh_interval_seconds": 20,
    "auto_apply_hysteresis_percent": 2,
}

# A fan controller should not trust a temperature snapshot for Unraid's
# historically long SMART polling interval. Operators should configure a
# roughly five-minute poll; ten minutes is the fail-safe ceiling here.
try:
    DISKS_STALE_WARN_SEC = int(os.environ.get("FANBRIDGE_DISKS_STALE_WARN_SEC", "600") or "600")
except Exception:
    DISKS_STALE_WARN_SEC = 600

_CONFIG_LOCK = threading.RLock()
_USERS_LOCK = threading.RLock()
_RATE_LOCK = threading.Lock()
_MUTATION_LOCK = threading.RLock()
_LAST_GOOD_CONFIG: dict | None = None


def _atomic_yaml_write(path: str, value: dict) -> None:
    """Durably replace a private YAML file without exposing a partial write."""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, mode=0o700, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(value, handle, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
        try:
            dir_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


def _migrate_config(value: dict) -> dict:
    migrated = copy.deepcopy(value)
    try:
        source_schema = int(migrated.get("schema_version", 0) or 0)
    except (TypeError, ValueError):
        source_schema = 0
    aliases = {
        "auto_apply_min_interval_s": "auto_apply_min_interval_seconds",
    }
    for old, new in aliases.items():
        if new not in migrated and old in migrated:
            migrated[new] = migrated[old]
    if "auto_apply_hysteresis_percent" not in migrated and "auto_apply_hysteresis_duty" in migrated:
        try:
            migrated["auto_apply_hysteresis_percent"] = round(
                int(migrated["auto_apply_hysteresis_duty"]) * 100 / 255
            )
        except (TypeError, ValueError):
            pass
    if "exclude_devices" not in migrated and isinstance(migrated.get("excluded_devices"), list):
        migrated["exclude_devices"] = migrated["excluded_devices"]
    # Only migrate legacy single-port installs that predate the controllers
    # key. An explicitly empty list means the user removed all controllers.
    if "controllers" not in migrated and SERIAL_PREF:
        migrated["controllers"] = [{
            "id": "primary",
            "name": "Primary controller",
            # The only controller supported by the pre-v2 schema was the
            # one-channel Pico/RP2040 design.
            "type": "diy",
            "port": SERIAL_PREF,
            "baud": SERIAL_BAUD,
        }]
    elif source_schema < 2 and isinstance(migrated.get("controllers"), list):
        # Older UI versions called the existing Pico board "official" or
        # "fanbridge". Schema 2 reserves `official` for the separate future
        # six-channel product, so migrate only genuinely old persisted data.
        for controller in migrated["controllers"]:
            if not isinstance(controller, dict):
                continue
            legacy_type = str(controller.get("type") or "").strip().lower()
            if legacy_type in {"official", "fanbridge", "pico", "rp2040"}:
                controller["type"] = "diy"
    migrated["schema_version"] = int(DEFAULT_CONFIG.get("schema_version", 3))
    return migrated

def _load_users() -> dict:
    with _USERS_LOCK:
        if not os.path.exists(USERS_PATH):
            return {}
        try:
            with open(USERS_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                raise ValueError("users file must contain a mapping")
            os.chmod(USERS_PATH, 0o600)
            return data
        except Exception as exc:
            log.error("Unable to read users file %s: %s", USERS_PATH, exc)
            raise

def _save_users(users: dict) -> None:
    with _USERS_LOCK:
        _atomic_yaml_write(USERS_PATH, users)

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
        now = time.monotonic()
        k = ((ip or "?")[:64], (key or "*")[:64])
        with _RATE_LOCK:
            # Bound memory even when many spoofed/ephemeral clients connect.
            if len(_RATE) > 4096:
                stale = [rk for rk, vals in _RATE.items() if not vals or now - vals[-1] >= window]
                for rk in stale[:2048]:
                    _RATE.pop(rk, None)
            arr = [t for t in _RATE.get(k, []) if now - t < window]
            if len(arr) >= limit:
                _RATE[k] = arr
                return False
            arr.append(now)
            _RATE[k] = arr
            return True
    except Exception:
        # Authentication and hardware throttles must not disappear if the
        # limiter's in-memory state is unexpectedly damaged.
        return False

def _ensure_csrf_token() -> str:
    tok = session.get("csrf_token")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["csrf_token"] = tok
    return tok

def _require_csrf() -> bool:
    sent = request.headers.get("X-CSRF-Token", "") or request.form.get("csrf_token", "")
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


_CONFIG_CONTROLLER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_CONTROLLER_NAME_MAX = 24
_CONFIG_DEVICE_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_CONFIG_ALLOWED_BAUDS = {9600, 19200, 38400, 57600, 115200, 230400}
_CONFIG_PORT_PREFIXES = (
    "/host-dev/serial/by-id/", "/host-dev/ttyACM", "/host-dev/ttyUSB",
    "/dev/serial/by-id/", "/dev/ttyACM", "/dev/ttyUSB",
    "/dev/cu.usbmodem", "/dev/tty.usbmodem", "/tmp/ttyFAN",  # nosec B108 - explicit dev mode
)


def _normalise_config(value: dict) -> dict:
    """Return a complete, type-safe configuration suitable for actuation.

    YAML accepts values such as ``"false"`` and ``controllers: null`` that
    are syntactically valid but unsafe for Python truthiness/iteration.  This
    boundary deliberately uses defaults (and disables automatic output) when
    persisted types are wrong. Unknown keys are dropped so stale development
    settings cannot silently become an application API.
    """
    source = value if isinstance(value, dict) else {}

    def integer(key: str, low: int, high: int) -> int:
        default = int(DEFAULT_CONFIG[key])
        raw = source.get(key, default)
        if isinstance(raw, bool):
            return default
        try:
            result = int(raw)
        except (TypeError, ValueError):
            return default
        return result if low <= result <= high else default

    def curve(kind: str) -> tuple[list[int], list[int]]:
        t_key = f"{kind}_thresholds"
        p_key = f"{kind}_pwm"
        raw_t = source.get(t_key)
        raw_p = source.get(p_key)
        if not isinstance(raw_t, list) or not isinstance(raw_p, list):
            return list(DEFAULT_CONFIG[t_key]), list(DEFAULT_CONFIG[p_key])
        try:
            thresholds = [int(item) for item in raw_t if not isinstance(item, bool)]
            outputs = [int(item) for item in raw_p if not isinstance(item, bool)]
        except (TypeError, ValueError):
            return list(DEFAULT_CONFIG[t_key]), list(DEFAULT_CONFIG[p_key])
        valid = (
            2 <= len(thresholds) <= 32
            and len(thresholds) == len(raw_t) == len(outputs) == len(raw_p)
            and all(0 <= item <= 120 for item in thresholds)
            and all(right > left for left, right in zip(thresholds, thresholds[1:]))
            and all(0 <= item <= 100 for item in outputs)
            and all(right >= left for left, right in zip(outputs, outputs[1:]))
        )
        if not valid:
            return list(DEFAULT_CONFIG[t_key]), list(DEFAULT_CONFIG[p_key])
        return thresholds, outputs

    controllers: list[dict] = []
    seen_ids: set[str] = set()
    seen_ports: set[str] = set()
    seen_hardware_uids: set[str] = set()
    raw_controllers = source.get("controllers")
    if isinstance(raw_controllers, list):
        for raw in raw_controllers[:32]:
            if not isinstance(raw, dict):
                continue
            cid = str(raw.get("id") or "").strip().lower()
            name = str(raw.get("name") or cid).strip()
            if len(name) > _CONTROLLER_NAME_MAX:
                name = name[:_CONTROLLER_NAME_MAX].rstrip()
            port = str(raw.get("port") or "").strip()
            try:
                baud = int(raw.get("baud", 115200))
            except (TypeError, ValueError):
                baud = 0
            if (
                not _CONFIG_CONTROLLER_ID_RE.fullmatch(cid)
                or not name or len(name) > _CONTROLLER_NAME_MAX
                or any(ord(ch) < 32 for ch in name)
                or not port or len(port) > 256 or "\x00" in port
                or not port.startswith(_CONFIG_PORT_PREFIXES)
                or baud not in _CONFIG_ALLOWED_BAUDS
            ):
                continue
            physical = serial_svc.canonical_port(port)
            if cid in seen_ids or (physical and physical in seen_ports):
                continue
            kind = str(raw.get("type") or "unknown").strip().lower()
            if kind not in {"diy", "official", "unknown"}:
                kind = "unknown"
            hardware_uid = serial_svc.normalise_hardware_uid(raw.get("hardware_uid"))
            if hardware_uid and hardware_uid in seen_hardware_uids:
                continue
            controller = {
                "id": cid,
                "name": name,
                "type": kind,
                "port": port,
                "baud": baud,
            }
            control_mode = str(raw.get("control_mode") or "").strip().lower()
            if control_mode in {"auto", "manual"}:
                controller["control_mode"] = control_mode
            raw_manual_pwm = raw.get("manual_pwm")
            if not isinstance(raw_manual_pwm, bool):
                try:
                    manual_pwm = int(raw_manual_pwm)
                except (TypeError, ValueError):
                    manual_pwm = None
                if manual_pwm is not None and 0 <= manual_pwm <= 100:
                    controller["manual_pwm"] = manual_pwm
            if hardware_uid:
                controller["hardware_uid"] = hardware_uid
                seen_hardware_uids.add(hardware_uid)
            controllers.append(controller)
            seen_ids.add(cid)
            if physical:
                seen_ports.add(physical)

    excludes: list[str] = []
    raw_excludes = source.get("exclude_devices")
    if isinstance(raw_excludes, list):
        excludes = sorted({
            str(item).strip() for item in raw_excludes
            if _CONFIG_DEVICE_KEY_RE.fullmatch(str(item).strip())
        })[:256]

    controller_ids = {item["id"] for item in controllers}
    assignments: dict[str, str] = {}
    raw_assignments = source.get("drive_assignments")
    if isinstance(raw_assignments, dict):
        for raw_key, raw_target in list(raw_assignments.items())[:256]:
            key = str(raw_key).strip()
            if not _CONFIG_DEVICE_KEY_RE.fullmatch(key):
                continue
            target = str(raw_target).strip()
            # Each drive is routed to one existing controller or not at all.
            assignments[key] = target if target in {"none", *controller_ids} else "none"

    hdd_thresholds, hdd_pwm = curve("hdd")
    ssd_thresholds, ssd_pwm = curve("ssd")
    normalised = {
        "schema_version": int(DEFAULT_CONFIG["schema_version"]),
        "controllers": controllers,
        "poll_interval_seconds": integer("poll_interval_seconds", 3, 60),
        "control_interval_seconds": integer("control_interval_seconds", 2, 30),
        "hdd_thresholds": hdd_thresholds,
        "hdd_pwm": hdd_pwm,
        "ssd_thresholds": ssd_thresholds,
        "ssd_pwm": ssd_pwm,
        "single_override_hdd_c": integer("single_override_hdd_c", 20, 90),
        "single_override_ssd_c": integer("single_override_ssd_c", 20, 110),
        "override_pwm": 100,
        "fallback_pwm": integer("fallback_pwm", 0, 100),
        # Safety faults and single-drive overrides are never user-derated.
        "failsafe_pwm": 100,
        "pwm_hysteresis": integer("pwm_hysteresis", 0, 25),
        "exclude_devices": excludes,
        "drive_assignments": assignments,
        "idle_cutoff_hdd_c": integer("idle_cutoff_hdd_c", 0, 120),
        "idle_cutoff_ssd_c": integer("idle_cutoff_ssd_c", 0, 120),
        # Only a literal YAML/JSON boolean can authorise hardware output.
        "auto_apply": source.get("auto_apply") is True,
        "auto_apply_min_interval_seconds": integer("auto_apply_min_interval_seconds", 1, 60),
        "auto_apply_refresh_interval_seconds": integer("auto_apply_refresh_interval_seconds", 5, 30),
        "auto_apply_hysteresis_percent": integer("auto_apply_hysteresis_percent", 0, 25),
    }

    # Simulation is an explicit, environment-gated developer facility. Keep a
    # bounded fixture only when the operator deliberately supplied one.
    sim = source.get("sim")
    if isinstance(sim, dict) and isinstance(sim.get("drives"), list):
        normalised["sim"] = {"drives": [
            copy.deepcopy(item) for item in sim["drives"][:256]
            if isinstance(item, dict)
        ]}
    return normalised


def _sync_serial_controllers(cfg: dict) -> None:
    desired = {
        str(item.get("id")): item
        for item in (cfg.get("controllers") or [])
        if isinstance(item, dict) and item.get("id")
    }
    registered = {item["id"] for item in serial_svc.list_registered_controllers()}
    for cid in registered - set(desired):
        stopped = serial_svc.safe_stop_controller(cid)
        if not stopped.get("ok"):
            log.warning(
                "controller removed before 100%% safe-stop was verified | cid=%s error=%s",
                cid,
                stopped.get("error") or "unknown",
            )
        serial_svc.unregister_controller(cid)
    for cid, item in desired.items():
        if not serial_svc.register_controller(
            cid,
            str(item.get("port") or ""),
            int(item.get("baud", 115200)),
            expected_type=str(item.get("type") or "unknown"),
            expected_uid=item.get("hardware_uid"),
        ):
            log.error("Unable to register configured controller | cid=%s", cid)

def ensure_config_exists():
    with _CONFIG_LOCK:
        if not os.path.exists(CONFIG_PATH):
            initial = _normalise_config(_migrate_config(DEFAULT_CONFIG))
            _atomic_yaml_write(CONFIG_PATH, initial)
            log.info("Created default config at %s", CONFIG_PATH)

def load_config():
    global _LAST_GOOD_CONFIG
    ensure_config_exists()
    with _CONFIG_LOCK:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user_cfg = yaml.safe_load(f) or {}
            if not isinstance(user_cfg, dict):
                raise ValueError("configuration root must be a mapping")
            migrated = _migrate_config(user_cfg)
            merged = _normalise_config(_merge_defaults(migrated, DEFAULT_CONFIG))
            if merged != user_cfg:
                _atomic_yaml_write(CONFIG_PATH, merged)
                log.warning("Normalised configuration to safe schema %s", merged.get("schema_version"))
            else:
                os.chmod(CONFIG_PATH, 0o600)
            _LAST_GOOD_CONFIG = copy.deepcopy(merged)
        except Exception as exc:
            log.error("Configuration unreadable; retaining the last known good state: %s", exc)
            if _LAST_GOOD_CONFIG is None:
                raise RuntimeError(f"cannot load configuration {CONFIG_PATH}: {exc}") from exc
            merged = copy.deepcopy(_LAST_GOOD_CONFIG)
    _sync_serial_controllers(merged)
    return merged

def save_config(cfg: dict):
    global _LAST_GOOD_CONFIG
    if not isinstance(cfg, dict):
        raise ValueError("configuration must be a mapping")
    with _CONFIG_LOCK:
        merged = _normalise_config(_merge_defaults(_migrate_config(cfg), DEFAULT_CONFIG))
        _atomic_yaml_write(CONFIG_PATH, merged)
        _LAST_GOOD_CONFIG = copy.deepcopy(merged)
    _sync_serial_controllers(merged)
    wake = globals().get("_CONTROL_WAKE")
    if wake is not None:
        wake.set()


def _configured_controller_mode(controller: dict, legacy_auto: bool) -> str:
    mode = str(controller.get("control_mode") or "").strip().lower()
    if mode in {"auto", "manual"}:
        return mode
    return "auto" if legacy_auto else "manual"


def _manual_safety_for_snapshot(cid: str, requested_percent: int) -> tuple[bool, str | None]:
    """Return whether the latest trusted temperature state requires 100%."""
    if requested_percent >= 100:
        return False, None
    try:
        control = _control_summary(include_snapshot=True)
        snapshot = control.pop("snapshot", None)
        if not _control_is_healthy(control) or not isinstance(snapshot, dict):
            return True, "control_state_unavailable"
        controller = next((
            item for item in snapshot.get("controllers", [])
            if isinstance(item, dict) and item.get("id") == cid
        ), None)
        if controller is None:
            return True, "controller_temperature_state_unavailable"
        if controller.get("safety_state") == "failsafe":
            return True, str(controller.get("control_reason") or "temperature_failsafe")
        if controller.get("override") is True or controller.get("manual_safety_override_active") is True:
            return True, str(controller.get("manual_safety_reason") or controller.get("control_reason") or "critical_temperature")
        return False, None
    except Exception:
        return True, "control_state_unavailable"


def _set_manual_pwm(cid: str, value) -> dict:
    """Persist manual ownership before issuing its immediate setpoint."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return {"ok": False, "error": "invalid value"}
    try:
        if isinstance(value, float) and not value.is_integer():
            raise ValueError
        percent = int(value)
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid value"}
    if percent < 0 or percent > 100:
        return {"ok": False, "error": "PWM percent must be between 0 and 100"}

    # Serialize the mode transition with the complete control cycle. Otherwise
    # a cycle that already read an Auto snapshot could write its curve target
    # immediately after this request writes the manual target.
    with _CONTROL_CYCLE_LOCK:
        with _MUTATION_LOCK:
            config = load_config()
            controller = next((
                item for item in config.get("controllers", [])
                if isinstance(item, dict) and item.get("id") == cid
            ), None)
            if controller is None:
                return {"ok": False, "error": "unknown controller id"}
            controller["control_mode"] = "manual"
            controller["manual_pwm"] = percent
            legacy_auto = config.get("auto_apply") is True
            config["auto_apply"] = any(
                _configured_controller_mode(item, legacy_auto) == "auto"
                for item in config.get("controllers", [])
                if isinstance(item, dict)
            )
            save_config(config)

        safety_override, safety_reason = _manual_safety_for_snapshot(cid, percent)
        applied_percent = 100 if safety_override else percent
        result = dict(serial_svc.serial_set_pwm_percent(cid, applied_percent))
    result["requested_value"] = percent
    result["safety_override"] = safety_override
    result["safety_reason"] = safety_reason
    serial_svc.record_operator_transaction(cid, str(applied_percent), result)
    if safety_override:
        log.warning(
            "manual thermal safety blocked lower output | cid=%s | requested=%s%% | applied=100%% | reason=%s",
            cid,
            percent,
            safety_reason,
        )
    if result.get("ok"):
        _audit(
            "manual_pwm.set",
            controller=cid,
            requested=percent,
            applied=applied_percent,
            safety_override=safety_override,
        )
    return result

load_config()

    

    # Serial helpers moved to services.serial

# ---------- Auto-apply PWM state ----------
# Moved to services.pwm_calculator

#
# ---------- Serial send helpers used by API ----------
#
# get currently preferred port (same logic as get_serial_status)
import json as _json

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

# ---------- PWM logic ----------
from services.pwm_calculator import compute_status as _compute_status_svc

def compute_status():
    app_context = {
        'cfg': load_config(),
        'disks_ini': DISKS_INI,
        'in_docker': _in_docker,
        'app_version': APP_VERSION,
        'disks_stale_warn_sec': DISKS_STALE_WARN_SEC,
        'allow_simulation': os.environ.get("FANBRIDGE_ALLOW_SIMULATION", "0") == "1",
        'dbg_should': _dbg_should,
        'warn_once': _warn_once
    }
    return _compute_status_svc(app_context)


_CONTROL_STATE_LOCK = threading.RLock()
_CONTROL_CYCLE_LOCK = threading.Lock()
_CONTROL_WAKE = threading.Event()
_CONTROL_THREAD: threading.Thread | None = None
_CONTROL_STATE: dict = {
    "started_at": None,
    "last_attempt_at": None,
    "last_success_at": None,
    "last_error": None,
    "snapshot": None,
}
app.config["FB_SET_MANUAL_PWM"] = _set_manual_pwm


def _adopt_persistent_controller_identity(cid: str, identity: dict | None) -> str | None:
    """Persist a protocol-2 UID for a previously port-bound controller."""
    if not isinstance(identity, dict):
        return None
    try:
        protocol = int(identity.get("protocol") or 0)
    except (TypeError, ValueError):
        return None
    if protocol < 2:
        return None
    hardware_uid = serial_svc.normalise_hardware_uid(identity.get("hardware_uid"))
    if not hardware_uid:
        return None
    with _MUTATION_LOCK:
        config = load_config()
        controller = next((
            item for item in config.get("controllers", [])
            if isinstance(item, dict) and item.get("id") == cid
        ), None)
        if controller is None:
            return None
        existing_uid = serial_svc.normalise_hardware_uid(controller.get("hardware_uid"))
        if existing_uid:
            return existing_uid if existing_uid == hardware_uid else None
        duplicate = next((
            item for item in config.get("controllers", [])
            if isinstance(item, dict)
            and item.get("id") != cid
            and serial_svc.normalise_hardware_uid(item.get("hardware_uid")) == hardware_uid
        ), None)
        if duplicate is not None:
            log.error(
                "refusing controller UID enrollment because it is already assigned | cid=%s existing_cid=%s uid=%s",
                cid, duplicate.get("id"), hardware_uid,
            )
            return None
        controller["hardware_uid"] = hardware_uid
        save_config(config)
        log.info(
            "enrolled persistent controller hardware UID | cid=%s uid=%s",
            cid, hardware_uid,
        )
        return hardware_uid


def _controller_telemetry(data: dict) -> None:
    """Attach bounded serial status/telemetry during the single control cycle."""
    for controller in data.get("controllers", []):
        cid = str(controller.get("id") or "")
        if not cid:
            continue
        try:
            serial_status = serial_svc.get_serial_status(cid, full=False)
            controller["serial"] = {
                key: serial_status.get(key)
                for key in ("preferred", "available", "connected", "baud", "message")
            }
            controller["telemetry"] = {}
            if serial_status.get("connected"):
                adopted_uid = _adopt_persistent_controller_identity(
                    cid, serial_status.get("identity")
                )
                if adopted_uid:
                    controller["hardware_uid"] = adopted_uid
                    controller["persistent_identity"] = True
                result = serial_svc.serial_send_line(cid, "STATUS", expect_reply=True, timeout=0.4)
                reply = result.get("reply") if result.get("ok") else None
                if reply:
                    try:
                        telemetry = _json.loads(reply)
                        if isinstance(telemetry, dict):
                            controller["telemetry"] = telemetry
                    except (TypeError, ValueError):
                        controller["telemetry_error"] = "invalid STATUS response"
                elif not result.get("ok"):
                    controller["telemetry_error"] = str(result.get("error") or "STATUS failed")
        except Exception as exc:
            controller["serial"] = {"connected": False, "message": str(exc)}
            controller["telemetry"] = {}


def _run_control_cycle() -> dict | None:
    if not _CONTROL_CYCLE_LOCK.acquire(blocking=False):
        return None
    try:
        now = int(time.time())
        with _CONTROL_STATE_LOCK:
            _CONTROL_STATE["last_attempt_at"] = now
        try:
            snapshot = compute_status()
            _controller_telemetry(snapshot)
            with _CONTROL_STATE_LOCK:
                _CONTROL_STATE["snapshot"] = copy.deepcopy(snapshot)
                _CONTROL_STATE["last_success_at"] = int(time.time())
                _CONTROL_STATE["last_error"] = None
            return snapshot
        except Exception as exc:
            log.exception("control cycle failed: %s", exc)
            with _CONTROL_STATE_LOCK:
                _CONTROL_STATE["last_error"] = "control cycle failed"
            return None
    finally:
        _CONTROL_CYCLE_LOCK.release()


def _control_worker() -> None:
    with _CONTROL_STATE_LOCK:
        _CONTROL_STATE["started_at"] = int(time.time())
    while True:
        _run_control_cycle()
        try:
            interval = int(load_config().get("control_interval_seconds", 10))
        except Exception:
            interval = 10
        interval = max(2, min(30, interval))
        _CONTROL_WAKE.wait(interval)
        _CONTROL_WAKE.clear()


def _start_control_loop() -> None:
    global _CONTROL_THREAD
    if os.environ.get("FANBRIDGE_CONTROL_LOOP", "1") != "1":
        return
    with _CONTROL_STATE_LOCK:
        if _CONTROL_THREAD and _CONTROL_THREAD.is_alive():
            return
        _CONTROL_THREAD = threading.Thread(target=_control_worker, name="fanbridge-control", daemon=True)
        _CONTROL_THREAD.start()


def _control_summary(include_snapshot: bool = False) -> dict:
    with _CONTROL_STATE_LOCK:
        state = copy.deepcopy(_CONTROL_STATE)
    now = int(time.time())
    last_success = state.get("last_success_at")
    summary = {
        "running": bool(_CONTROL_THREAD and _CONTROL_THREAD.is_alive()),
        "last_attempt_at": state.get("last_attempt_at"),
        "last_success_at": last_success,
        "last_success_age_s": (now - int(last_success)) if last_success else None,
        "error": state.get("last_error"),
    }
    if include_snapshot:
        summary["snapshot"] = state.get("snapshot")
    return summary


def _control_is_healthy(control: dict) -> bool:
    return bool(
        control.get("running")
        and control.get("last_success_age_s") is not None
        and int(control["last_success_age_s"]) <= 60
        and not control.get("error")
    )


@app.get("/health")
def health():
    # Liveness is deliberately read-only. Hardware actuation belongs only to
    # the dedicated control thread.
    control = _control_summary()
    healthy = _control_is_healthy(control)
    state = "ok" if healthy else "degraded"
    return jsonify({"status": state, "uptime_s": int(time.time() - STARTED), "control": control}), (200 if healthy else 503)


@app.get("/api/control/health")
def control_health():
    control = _control_summary()
    healthy = _control_is_healthy(control)
    return jsonify({"ok": healthy, "control": control}), (200 if healthy else 503)

# Toggle auto-apply on/off
@app.post("/api/auto_apply")
def api_auto_apply():
    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "JSON object required"}), 400
    enable = data.get("enabled")
    if not isinstance(enable, bool):
        return jsonify({"ok": False, "error": "enabled must be a boolean"}), 400
    cid = data.get("cid")
    if cid is not None and (not isinstance(cid, str) or not _CONTROLLER_ID_RE.fullmatch(cid)):
        return jsonify({"ok": False, "error": "valid cid is required"}), 400
    with _CONTROL_CYCLE_LOCK:
        c = load_config()
        controllers = [item for item in c.get("controllers", []) if isinstance(item, dict)]
        legacy_auto = c.get("auto_apply") is True
        if cid is None:
            # Compatibility for older clients: a global toggle changes every
            # controller. New clients always provide the selected controller ID.
            for controller in controllers:
                controller["control_mode"] = "auto" if enable else "manual"
                if not enable:
                    controller.setdefault("manual_pwm", 100)
        else:
            controller = next((item for item in controllers if item.get("id") == cid), None)
            if controller is None:
                return jsonify({"ok": False, "error": "controller not found"}), 404
            controller["control_mode"] = "auto" if enable else "manual"
            if not enable:
                controller.setdefault("manual_pwm", 100)
        c["auto_apply"] = any(
            _configured_controller_mode(controller, legacy_auto) == "auto"
            for controller in controllers
        )
        save_config(c)
    _CONTROL_WAKE.set()
    try:
        _audit("auto_apply.toggle", controller=cid or "all", enabled=enable)
    except Exception:
        pass
    return jsonify({
        "ok": True,
        "cid": cid,
        "control_mode": "auto" if enable else "manual",
        "auto_apply": c["auto_apply"],
    })

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
        # Chart.js is bundled by Vite; no third-party script execution is
        # required. Inline styles remain temporarily necessary for the UI.
        csp = (
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "frame-src 'none'"
        )
        resp.headers.setdefault("Content-Security-Policy", csp)
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
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
            QUIET = (
                "/health",
                "/api/status",
                "/api/serial/status",
                "/api/logs",
                "/api/logs/clear",
                "/api/log_level",
                "/api/logs/download",
            )
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
    if isinstance(e, HTTPException):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": e.name.lower()}), e.code
        return e
    logging.getLogger("fanbridge").exception("Unhandled error for %s %s: %s", request.method, request.path, e)
    return jsonify({"ok": False, "error": "internal server error"}), 500


def _safe_next_url(value: str | None) -> str:
    from urllib.parse import urlsplit
    target = (value or "").strip()
    parts = urlsplit(target)
    if not target.startswith("/") or target.startswith("//") or parts.scheme or parts.netloc:
        return url_for("index")
    return target


def _user_hash(users: dict, username: str) -> str | None:
    entry = (users.get("users") or {}).get(username)
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict) and isinstance(entry.get("password_hash"), str):
        return entry["password_hash"]
    return None


def _session_version(users: dict, username: str) -> int:
    try:
        return int((users.get("session_versions") or {}).get(username, 1))
    except (TypeError, ValueError):
        return 1

@app.before_request
def _auth_and_rate():
    p = request.path
    ip = request.remote_addr or ""

    # Login is public, but it is not exempt from CSRF or brute-force limits.
    if p == "/login":
        if request.method == "POST":
            if not _allow(ip, "login", limit=8, window=300):
                return jsonify({"ok": False, "error": "too many login attempts"}), 429
            if not _require_csrf():
                return jsonify({"ok": False, "error": "invalid CSRF token"}), 403
        return

    # allow public endpoints
    if p.startswith("/static/") or p in ("/health", "/api/app/version"):
        return
    # require login
    if "user" not in session:
        if p.startswith("/api/"):
            return jsonify({"ok": False, "error": "authentication required"}), 401
        return redirect(url_for("login", next=request.path))

    # Password changes invalidate other signed cookies even though Flask's
    # session itself is client-side.
    try:
        users = _load_users()
        username = str(session.get("user") or "")
        if not _user_hash(users, username) or int(session.get("auth_version", 0)) != _session_version(users, username):
            session.clear()
            if p.startswith("/api/"):
                return jsonify({"ok": False, "error": "session expired"}), 401
            return redirect(url_for("login", next=request.path))
    except Exception:
        return jsonify({"ok": False, "error": "authentication store unavailable"}), 503

    # --- Rate limiting ---
    # Most safe GETs are cache reads. Hardware diagnostics are deliberately
    # bounded because they acquire the same physical serial lock as the
    # cooling lease refresh.
    if request.method == "GET":
        hardware_limits = {
            "/api/ports": ("ports_probe", 10, 60),
            "/api/serial/status": ("serial_status", 30, 60),
            "/api/serial/tools": ("serial_tools", 20, 60),
            "/api/rp/status": ("firmware_status", 10, 60),
        }
        limit_spec = hardware_limits.get(p)
        if p == "/api/logs/download" and request.args.get("cid"):
            limit_spec = ("serial_diagnostics", 10, 60)
        if limit_spec and not _allow(ip, limit_spec[0], limit=limit_spec[1], window=limit_spec[2]):
            return jsonify({"ok": False, "error": "too many hardware diagnostic requests"}), 429
        return

    # Per-endpoint buckets with relaxed limits for serial actions
    key = "mutate"
    limit, window = 60, 60  # default for POST-ish

    if p == "/api/serial/send":
        key = "serial_send"
        limit, window = 120, 60   # allow ~2/sec
    elif p == "/api/serial/pwm":
        key = "serial_pwm"
        limit, window = 120, 60
    elif p == "/api/ports/identify":
        key = "controller_identify"
        limit, window = 10, 60
    elif p in ("/api/rp/flash", "/api/rp/flash_upload"):
        key = "firmware_update"
        limit, window = 3, 600
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
            return jsonify({"ok": False, "error": "invalid CSRF token"}), 403


@app.before_request
def _serialize_state_mutations():
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    state_paths = {
        "/api/auto_apply", "/api/config", "/api/settings", "/api/curves",
        "/api/reset_defaults", "/api/exclude", "/api/change_password",
    }
    if request.path in state_paths or request.path == "/api/controllers" or request.path.startswith("/api/controllers/"):
        _MUTATION_LOCK.acquire()
        g._fanbridge_mutation_lock = True


@app.teardown_request
def _release_state_mutation(_error=None):
    if getattr(g, "_fanbridge_mutation_lock", False):
        _MUTATION_LOCK.release()

@app.route("/login", methods=["GET", "POST"])
def login():
    users = _load_users()
    first_run = not users or not users.get("users")

    if request.method == "POST":
        if first_run:
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            confirm  = request.form.get("confirm") or ""
            supplied_setup_token = request.form.get("setup_token") or ""
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username):
                return render_template("login.html", first_run=True, error="Username must use letters, numbers, '.', '_' or '-'.", csrf_token=_ensure_csrf_token())
            if len(password) < PASSWORD_MIN_LENGTH or len(password) > PASSWORD_MAX_LENGTH or password != confirm:
                return render_template("login.html", first_run=True, error="Use a password of at least 8 characters and enter it twice.", csrf_token=_ensure_csrf_token())
            expected_setup_token = _load_or_create_setup_token()
            if not secrets.compare_digest(supplied_setup_token, expected_setup_token):
                _audit("auth.setup_rejected", username=username)
                return render_template("login.html", first_run=True, error="The one time setup token is incorrect. Check the container log.", csrf_token=_ensure_csrf_token()), 403
            # Re-read under the write lock so two first-run requests cannot
            # both claim the installation.
            with _USERS_LOCK:
                current = _load_users()
                if current.get("users"):
                    return render_template("login.html", first_run=False, error="Setup has already been completed.", csrf_token=_ensure_csrf_token()), 409
                users = {
                    "users": {username: generate_password_hash(password)},
                    "session_versions": {username: 1},
                }
                _save_users(users)
            if not os.environ.get("FANBRIDGE_SETUP_TOKEN"):
                try:
                    _setup_token_path().unlink(missing_ok=True)
                except OSError:
                    pass
            session.clear()
            session["user"] = username
            session["auth_version"] = 1
            _ensure_csrf_token()
            _audit("auth.setup_completed", username=username)
            return redirect(url_for("index"))
        else:
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            stored = _user_hash(users, username)
            if stored and check_password_hash(stored, password):
                session.clear()
                session["user"] = username
                session["auth_version"] = _session_version(users, username)
                _ensure_csrf_token()
                nxt = _safe_next_url(request.args.get("next"))
                _audit("auth.login", username=username)
                return redirect(nxt)
            _audit("auth.login_failed", username=username[:64])
            return render_template("login.html", first_run=False, error="Invalid username or password.", csrf_token=_ensure_csrf_token())

    # GET
    if first_run:
        _load_or_create_setup_token()
    return render_template("login.html", first_run=first_run, error=None, csrf_token=_ensure_csrf_token())

@app.post("/logout")
def logout():
    session.clear()
    return ("", 204)

@app.get("/")
def index():
    try:
        pi = int(load_config().get("poll_interval_seconds", 7))
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
    state = _control_summary(include_snapshot=True)
    data = state.pop("snapshot", None)
    if not isinstance(data, dict) or not _control_is_healthy(state):
        return jsonify({
            "ok": False,
            "error": "control state is unavailable or stale",
            "control": state,
        }), 503
    config = load_config()
    data = copy.deepcopy(data)
    source = data.get("temperature_source")
    if isinstance(source, dict) and source.get("mtime") is not None:
        try:
            source["age_seconds"] = max(0, int(time.time() - int(source["mtime"])))
            stale_after = max(60, int(source.get("stale_after_seconds", DISKS_STALE_WARN_SEC)))
            if source["age_seconds"] > stale_after:
                source["stale"] = True
                source["fault"] = "temperature_source_stale"
        except (TypeError, ValueError):
            source["age_seconds"] = None
    data["ok"] = True
    data["control"] = state
    data["source"] = copy.deepcopy(data.get("temperature_source") or {})
    data["settings"] = {
        "poll_interval_seconds": int(config.get("poll_interval_seconds", 7)),
        "control_interval_seconds": int(config.get("control_interval_seconds", 10)),
        "single_override_hdd_c": int(config.get("single_override_hdd_c", 45)),
        "single_override_ssd_c": int(config.get("single_override_ssd_c", 60)),
        "auto_apply": bool(config.get("auto_apply")),
        "auto_apply_min_interval_seconds": int(config.get("auto_apply_min_interval_seconds", 3)),
        "auto_apply_refresh_interval_seconds": int(config.get("auto_apply_refresh_interval_seconds", 20)),
        "auto_apply_hysteresis_percent": int(config.get("auto_apply_hysteresis_percent", 2)),
        "fallback_pwm": int(config.get("fallback_pwm", 10)),
        "failsafe_pwm": 100,
        "excluded_devices": sorted(set(config.get("exclude_devices") or [])),
        "drive_assignments": copy.deepcopy(config.get("drive_assignments") or {}),
    }
    data["curves"] = {
        "hdd_thresholds": list(config.get("hdd_thresholds") or []),
        "hdd_pwm": list(config.get("hdd_pwm") or []),
        "ssd_thresholds": list(config.get("ssd_thresholds") or []),
        "ssd_pwm": list(config.get("ssd_pwm") or []),
    }
    # `config` is a compatibility alias containing only UI-safe fields.
    data["config"] = {**data["settings"], **data["curves"]}
    return jsonify(data)

@app.get("/api/history")
def history():
    from services.history import get_history
    try:
        hours = int(request.args.get("hours", "1"))
    except ValueError:
        hours = 1
    hours = max(1, min(720, hours))
    cid = (request.args.get("cid") or "").strip()
    if cid and not _CONTROLLER_ID_RE.fullmatch(cid):
        return jsonify({"ok": False, "error": "invalid controller id"}), 400
    if cid:
        config = load_config()
        if not any(item.get("id") == cid for item in config.get("controllers", [])):
            return jsonify({"ok": False, "error": "controller not found"}), 404
    return jsonify({"ok": True, "cid": cid or None, "history": get_history(hours, cid)})




# --------- API: Controllers and Ports ---------

_CONTROLLER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_ALLOWED_BAUDS = {9600, 19200, 38400, 57600, 115200, 230400}


def _valid_controller_port(port: str) -> bool:
    if not port or len(port) > 256 or "\x00" in port:
        return False
    allowed_prefixes = (
        "/host-dev/serial/by-id/", "/host-dev/ttyACM", "/host-dev/ttyUSB",
        "/dev/serial/by-id/", "/dev/ttyACM", "/dev/ttyUSB",
        "/dev/cu.usbmodem", "/dev/tty.usbmodem",
    )
    if port.startswith(allowed_prefixes):
        return True
    return (
        os.environ.get("FANBRIDGE_DEV_SERIAL", "0") == "1"
        and port.startswith("/tmp/ttyFAN")  # nosec B108 - explicit dev mode only
    )


def _suggested_controller_name(controller_type: str, hardware_uid: str | None) -> str | None:
    if controller_type == "diy" and hardware_uid:
        return f"DIY-RP2040-{hardware_uid[-4:].upper()}"
    return None

@app.get("/api/ports")
def get_ports():
    ports = serial_svc.list_serial_ports()
    config = load_config()
    controllers = config.get("controllers") or []
    results = []
    for p in ports:
        details = serial_svc.identify_port_details(p)
        hardware_uid = serial_svc.normalise_hardware_uid(
            details.get("hardware_uid") if isinstance(details, dict) else None
        )
        configured = next((
            item for item in controllers
            if isinstance(item, dict) and (
                (hardware_uid and item.get("hardware_uid") == hardware_uid)
                or serial_svc.canonical_port(item.get("port")) == serial_svc.canonical_port(p)
            )
        ), None)
        results.append({
            "port": p,
            "type": details.get("type", "unknown") if isinstance(details, dict) else "unknown",
            "board": details.get("board") if isinstance(details, dict) else None,
            "protocol": details.get("protocol") if isinstance(details, dict) else None,
            "channels": details.get("channels") if isinstance(details, dict) else None,
            "hardware_uid": hardware_uid,
            "persistent_identity": bool(hardware_uid),
            "suggested_name": _suggested_controller_name(
                details.get("type") if isinstance(details, dict) else "unknown",
                hardware_uid,
            ),
            "identify_supported": bool(
                isinstance(details, dict)
                and details.get("type") == "diy"
                and details.get("board") == "rp2040-zero"
                and hardware_uid
            ),
            "configured_controller_id": configured.get("id") if configured else None,
        })
    return jsonify({"ok": True, "ports": results})


@app.post("/api/ports/identify")
def identify_controller_port():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "JSON object required"}), 400
    port = str(data.get("port") or "").strip()
    if not _valid_controller_port(port):
        return jsonify({"ok": False, "error": "port is not an allowed mapped USB serial device"}), 400

    config = load_config()
    requested_physical = serial_svc.canonical_port(port)
    configured = next((
        item for item in config.get("controllers", [])
        if isinstance(item, dict)
        and serial_svc.canonical_port(item.get("port")) == requested_physical
    ), None)
    if configured is not None:
        return jsonify({
            "ok": False,
            "error": f"serial port is already assigned to controller {configured.get('id')}",
        }), 409

    configured_uids = {
        uid for item in config.get("controllers", [])
        if isinstance(item, dict)
        and (uid := serial_svc.normalise_hardware_uid(item.get("hardware_uid")))
    }
    result = serial_svc.identify_unregistered_controller(
        port,
        excluded_hardware_uids=configured_uids,
    )
    if result.get("ok"):
        return jsonify(result)
    code = str(result.get("code") or "")
    status = 409 if code in {"already_assigned", "upgrade_required"} else 502
    if code in {"invalid_port", "identity_failed"}:
        status = 400
    return jsonify(result), status

@app.post("/api/controllers")
def add_controller():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "JSON object required"}), 400
    cid = str(data.get("id") or "").strip().lower()
    cname = str(data.get("name") or "").strip()
    cport = str(data.get("port") or "").strip()
    try:
        cbaud = int(data.get("baud", 115200))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid baud rate"}), 400

    if not _CONTROLLER_ID_RE.fullmatch(cid):
        return jsonify({"ok": False, "error": "controller id must match [a-z][a-z0-9_-]{0,31}"}), 400
    if not cname or len(cname) > _CONTROLLER_NAME_MAX or any(ord(ch) < 32 for ch in cname):
        return jsonify({"ok": False, "error": f"controller name must be 1-{_CONTROLLER_NAME_MAX} printable characters"}), 400
    if not _valid_controller_port(cport):
        return jsonify({"ok": False, "error": "port is not an allowed mapped USB serial device"}), 400
    if cbaud not in _ALLOWED_BAUDS:
        return jsonify({"ok": False, "error": "unsupported baud rate"}), 400

    identity = serial_svc.identify_port_details(cport)
    detected_type = identity.get("type") if isinstance(identity, dict) else "unknown"
    hardware_uid = serial_svc.normalise_hardware_uid(
        identity.get("hardware_uid") if isinstance(identity, dict) else None
    )
    if detected_type not in {"official", "diy"} and os.environ.get("FANBRIDGE_ALLOW_UNVERIFIED_CONTROLLER", "0") != "1":
        return jsonify({"ok": False, "error": "device did not identify as a FanBridge controller"}), 400
    if detected_type == "official":
        return jsonify({
            "ok": False,
            "error": "six-channel custom-controller support is reserved but not implemented in this host release",
        }), 409
    ctype = detected_type if detected_type in {"official", "diy"} else "unknown"

    if not cid or not ctype or not cport:
        return jsonify({"ok": False, "error": "Missing required fields"}), 400

    cfg = load_config()
    controllers = cfg.setdefault("controllers", [])
    if len(controllers) >= 32:
        return jsonify({"ok": False, "error": "maximum controller count reached"}), 409
    
    # Check if exists
    for c in controllers:
        if c.get("id") == cid:
            return jsonify({"ok": False, "error": "Controller ID already exists"}), 400
        existing = str(c.get("port") or "")
        if serial_svc.canonical_port(existing) == serial_svc.canonical_port(cport):
            return jsonify({"ok": False, "error": "serial port is already assigned"}), 400
        if hardware_uid and c.get("hardware_uid") == hardware_uid:
            return jsonify({"ok": False, "error": "controller hardware UID is already assigned"}), 409

    new_c = {
        "id": cid,
        "name": cname or cid,
        "type": ctype,
        "port": cport,
        "baud": cbaud,
        "control_mode": "manual",
        "manual_pwm": 100,
    }
    if hardware_uid:
        new_c["hardware_uid"] = hardware_uid
    if not serial_svc.register_controller(
        cid,
        cport,
        cbaud,
        expected_type=ctype,
        expected_uid=hardware_uid,
    ):
        return jsonify({"ok": False, "error": "serial port or controller hardware UID is already registered"}), 409
    controllers.append(new_c)
    try:
        save_config(cfg)
    except Exception:
        serial_svc.unregister_controller(cid)
        raise
    _CONTROL_WAKE.set()
    
    return jsonify({
        "ok": True,
        "controller": new_c,
        "persistent_identity": bool(hardware_uid),
    })

@app.patch("/api/controllers/<cid>")
def update_controller(cid):
    if not _CONTROLLER_ID_RE.fullmatch(cid):
        return jsonify({"ok": False, "error": "invalid controller id"}), 400

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "JSON object required"}), 400

    cname = str(data.get("name") or "").strip()
    if not cname or len(cname) > _CONTROLLER_NAME_MAX or any(ord(ch) < 32 for ch in cname):
        return jsonify({"ok": False, "error": f"controller name must be 1-{_CONTROLLER_NAME_MAX} printable characters"}), 400

    cfg = load_config()
    controller = next(
        (item for item in cfg.get("controllers", []) if item.get("id") == cid),
        None,
    )
    if controller is None:
        return jsonify({"ok": False, "error": "Controller not found"}), 404

    controller["name"] = cname
    save_config(cfg)
    _CONTROL_WAKE.set()
    return jsonify({
        "ok": True,
        "controller": {"id": cid, "name": cname},
    })

@app.delete("/api/controllers/<cid>")
def delete_controller(cid):
    if not _CONTROLLER_ID_RE.fullmatch(cid):
        return jsonify({"ok": False, "error": "invalid controller id"}), 400
    cfg = load_config()
    controllers = cfg.get("controllers", [])
    
    # Filter out the controller
    new_controllers = [c for c in controllers if c.get("id") != cid]
    if len(new_controllers) == len(controllers):
        return jsonify({"ok": False, "error": "Controller not found"}), 404
        
    cfg["controllers"] = new_controllers
    assignments = cfg.get("drive_assignments")
    if isinstance(assignments, dict):
        cfg["drive_assignments"] = {
            # A deleted hardware destination leaves its drives explicitly
            # unassigned until the operator selects a replacement controller.
            dev: ("none" if assigned == cid else assigned)
            for dev, assigned in assignments.items()
        }
    save_config(cfg)
    serial_svc.unregister_controller(cid)
    _CONTROL_WAKE.set()
    
    return jsonify({"ok": True})

# --------- API: Serial endpoints moved to api/serial blueprint ---------



# --------- API: Controller firmware status ---------
_FIRMWARE_FLASH_LOCK = threading.Lock()
_FIRMWARE_RELEASE_LOCK = threading.Lock()
_FIRMWARE_RELEASE_CACHE: dict = {
    "expires_at": 0.0,
    "release": None,
    "error": None,
}
_FIRMWARE_RELEASE_CACHE_SECONDS = 300
_FIRMWARE_MIN_REMOTE_VERSION = (2, 5, 0)
_UF2_MAGIC_START_0 = 0x0A324655
_UF2_MAGIC_START_1 = 0x9E5D5157
_UF2_MAGIC_END = 0x0AB16F30
_UF2_FLAG_FAMILY_ID = 0x00002000
_RP2040_FAMILY_ID = 0xE48BFF56


def _firmware_version_tuple(value: object) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?([0-9]+)\.([0-9]+)\.([0-9]+)", str(value or "").strip())
    if not match:
        return (0, 0, 0)
    return tuple(int(part) for part in match.groups())


def _latest_approved_diy_firmware(*, refresh: bool = False) -> tuple[dict | None, str | None]:
    """Return the newest HIL-gated DIY release with a checksum companion."""
    now = time.monotonic()
    with _FIRMWARE_RELEASE_LOCK:
        if not refresh and now < float(_FIRMWARE_RELEASE_CACHE.get("expires_at") or 0):
            return _FIRMWARE_RELEASE_CACHE.get("release"), _FIRMWARE_RELEASE_CACHE.get("error")

        releases = http_get_json(
            "https://api.github.com/repos/RoBroLabs/fanbridge/releases?per_page=30",
            timeout=6.0,
        )
        approved: list[dict] = []
        error = None
        if not isinstance(releases, list):
            error = "Firmware release service is unavailable."
        else:
            for release in releases:
                if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
                    continue
                tag = str(release.get("tag_name") or "")
                tag_match = re.fullmatch(r"fw-v([0-9]+\.[0-9]+\.[0-9]+)", tag)
                if not tag_match:
                    continue
                version = tag_match.group(1)
                version_tuple = _firmware_version_tuple(version)
                if version_tuple < _FIRMWARE_MIN_REMOTE_VERSION:
                    continue
                expected_asset = f"fanbridge-rp2040-{version}.uf2"
                expected_checksum = f"{expected_asset}.sha256"
                asset_names = {
                    str(asset.get("name") or "")
                    for asset in (release.get("assets") or [])
                    if isinstance(asset, dict)
                }
                # The protected release workflow publishes both files only
                # after the matching hardware-in-the-loop approval is set.
                if expected_asset not in asset_names or expected_checksum not in asset_names:
                    continue
                base = f"https://github.com/RoBroLabs/fanbridge/releases/download/{tag}"
                approved.append({
                    "version": version,
                    "version_tuple": version_tuple,
                    "tag": tag,
                    "asset": expected_asset,
                    "asset_url": f"{base}/{expected_asset}",
                    "checksum_url": f"{base}/{expected_checksum}",
                })

        selected = max(approved, key=lambda item: item["version_tuple"], default=None)
        _FIRMWARE_RELEASE_CACHE.update({
            "expires_at": now + _FIRMWARE_RELEASE_CACHE_SECONDS,
            "release": selected,
            "error": error,
        })
        return selected, error


def _validate_rp2040_uf2(path: str) -> tuple[bool, str, str | None]:
    try:
        size = os.path.getsize(path)
        if size < 512 or size > 4 * 1024 * 1024 or size % 512:
            return False, "UF2 must contain complete 512-byte blocks and be no larger than 4 MiB", None
        digest = hashlib.sha256()
        expected_blocks = size // 512
        seen: set[int] = set()
        with open(path, "rb") as stream:
            for _ in range(expected_blocks):
                block = stream.read(512)
                digest.update(block)
                magic0, magic1, flags, _target, payload_size, block_no, num_blocks, family = struct.unpack_from(
                    "<IIIIIIII", block, 0
                )
                end_magic = struct.unpack_from("<I", block, 508)[0]
                if magic0 != _UF2_MAGIC_START_0 or magic1 != _UF2_MAGIC_START_1 or end_magic != _UF2_MAGIC_END:
                    return False, "file is not a valid UF2 image", None
                if payload_size <= 0 or payload_size > 476:
                    return False, "UF2 contains an invalid payload block", None
                if num_blocks != expected_blocks or block_no >= expected_blocks or block_no in seen:
                    return False, "UF2 block numbering is incomplete or inconsistent", None
                if not flags & _UF2_FLAG_FAMILY_ID or family != _RP2040_FAMILY_ID:
                    return False, "UF2 is not marked for the RP2040 device family", None
                seen.add(block_no)
        if len(seen) != expected_blocks:
            return False, "UF2 image is incomplete", None
        return True, "ok", digest.hexdigest()
    except (OSError, struct.error):
        return False, "UF2 image could not be validated", None


def _bootsel_usb_selector(location: str | None, timeout: float = 20.0) -> tuple[int, int] | None:
    value = str(location or "").strip()
    valid_location = bool(re.fullmatch(r"[0-9]+-[0-9]+(?:\.[0-9]+)*(?::[0-9]+\.[0-9]+)?", value))
    device_name = value.split(":", 1)[0] if valid_location else ""
    usb_root = pathlib.Path("/sys/bus/usb/devices")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        candidates = [usb_root / device_name] if device_name else []
        if not candidates:
            candidates = [path.parent for path in usb_root.glob("*/idVendor")]
        matches = []
        for base in candidates:
            try:
                vendor = (base / "idVendor").read_text(encoding="ascii").strip().lower()
                product = (base / "idProduct").read_text(encoding="ascii").strip().lower()
                if vendor == "2e8a" and product == "0003":
                    bus = int((base / "busnum").read_text(encoding="ascii").strip())
                    address = int((base / "devnum").read_text(encoding="ascii").strip())
                    matches.append((bus, address))
            except (OSError, ValueError):
                continue
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None
        time.sleep(0.25)
    return None


def _firmware_flash_availability(controller: dict) -> tuple[bool, str | None]:
    if controller.get("type") != "diy":
        return False, "Firmware upload is currently available only for DIY RP2040 controllers."
    if not shutil.which("picotool"):
        return False, "The container image does not include picotool."
    if not os.path.isdir("/dev/bus/usb"):
        return False, "Map /dev/bus/usb into the container and allow USB character devices."
    return True, None


@app.get("/api/rp/status")
def api_rp_status():
    cid = (request.args.get("cid") or "").strip()
    if not _CONTROLLER_ID_RE.fullmatch(cid):
        return jsonify({"ok": False, "error": "valid cid parameter required"}), 400
    config = load_config()
    controller = next(
        (item for item in config.get("controllers", []) if item.get("id") == cid),
        None,
    )
    if not controller:
        return jsonify({"ok": False, "error": "controller not found"}), 404

    serial_status = serial_svc.get_serial_status(cid, full=True)
    version = None
    if serial_status.get("connected"):
        try:
            result = serial_svc.serial_send_line(cid, "VERSION", expect_reply=True, timeout=0.5)
            if result.get("ok"):
                version = (result.get("reply") or "").strip() or None
        except Exception:
            pass
    identity = serial_status.get("identity")
    if not isinstance(identity, dict):
        identity = {}
    version = version or (str(identity.get("version") or "").strip() or None)
    flash_enabled, flash_reason = _firmware_flash_availability(controller)
    serial_code = (
        "connected" if serial_status.get("connected") else
        "firmware_update_required" if identity.get("legacy") else
        "device_unavailable" if not serial_status.get("available") else
        "identity_unverified"
    )
    serial_status = {**serial_status, "code": serial_code}
    release, release_error = _latest_approved_diy_firmware(
        refresh=request.args.get("refresh") == "1",
    )
    latest_version = str(release.get("version")) if release else None
    current_version = _firmware_version_tuple(version)
    latest_tuple = release.get("version_tuple") if release else None
    update_available = bool(
        controller.get("type") == "diy"
        and release
        and (current_version == (0, 0, 0) or latest_tuple > current_version)
    )
    if controller.get("type") != "diy":
        remote_message = "Remote firmware releases are not available for this controller type."
    elif release_error:
        remote_message = release_error
    elif not release:
        remote_message = "No hardware-approved remote firmware release is published yet."
    elif not flash_enabled:
        remote_message = flash_reason or "Docker USB firmware access is not configured."
    elif update_available:
        remote_message = f"Firmware {latest_version} is verified and ready to install."
    elif current_version > latest_tuple:
        remote_message = "This controller is newer than the latest approved remote release."
    else:
        remote_message = "This controller is running the latest approved firmware."

    return jsonify({
        "ok": True,
        "cid": cid,
        "product": controller.get("type"),
        "controller_version": version,
        "board": identity.get("board"),
        "protocol_version": identity.get("protocol"),
        "channel_count": identity.get("channels"),
        "serial": serial_status,
        "usb": _usb_info_for_port(serial_status.get("preferred")),
        "firmware_flash_enabled": flash_enabled,
        "flash_unavailable_reason": flash_reason,
        "latest_version": latest_version,
        "remote_update_available": update_available,
        "remote_install_enabled": bool(update_available and flash_enabled),
        "remote_update_message": remote_message,
    })


# Retain small compatibility stubs so older clients receive an explicit answer.
@app.post("/api/rp/repo")
def api_rp_repo():
    return jsonify({
        "ok": False,
        "error": "custom firmware repositories are not supported",
    }), 403


@app.post("/api/rp/rp2_device")
def api_rp_set_device():
    return jsonify({
        "ok": False,
        "error": "firmware targets are selected from the registered controller identity",
    }), 403


def _flash_validated_rp2040(
    cid: str,
    temp_path: str,
    digest: str,
    *,
    source: str,
    release_version: str | None = None,
) -> tuple[dict, int]:
    prepared = serial_svc.enter_diy_bootsel(cid)
    if not prepared.get("ok"):
        log.warning("firmware update preparation failed | cid=%s error=%s", cid, prepared.get("error"))
        return {"ok": False, "error": "controller could not safely enter firmware update mode"}, 409

    selector = _bootsel_usb_selector(prepared.get("usb_location"))
    if not selector:
        return {
            "ok": False,
            "error": "RP2040 BOOTSEL device was not uniquely visible through the Docker USB mapping",
        }, 503
    bus, address = selector
    picotool = shutil.which("picotool")
    if not picotool:
        return {"ok": False, "error": "picotool is unavailable"}, 503
    command = [
        picotool, "load", "-v", "-x", temp_path,
        "--bus", str(bus), "--address", str(address),
    ]
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=45,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.error("picotool timed out | cid=%s digest=%s", cid, digest[:12])
        return {"ok": False, "error": "firmware writer timed out; controller remains in BOOTSEL mode"}, 504
    if completed.returncode != 0:
        log.error(
            "picotool failed | cid=%s code=%s output=%s",
            cid,
            completed.returncode,
            (completed.stdout or "")[-1000:],
        )
        return {"ok": False, "error": "firmware writer rejected the UF2 image"}, 502

    verified_identity = None
    installed_version = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status = serial_svc.get_serial_status(cid, full=False)
        if status.get("connected"):
            identity = status.get("identity")
            if isinstance(identity, dict) and int(identity.get("protocol") or 0) >= 2:
                verified_identity = identity
                version_result = serial_svc.serial_send_line(cid, "VERSION", expect_reply=True, timeout=0.6)
                if version_result.get("ok"):
                    installed_version = str(version_result.get("reply") or "").strip() or None
                break
        time.sleep(0.5)

    if verified_identity:
        _adopt_persistent_controller_identity(cid, verified_identity)
    _CONTROL_WAKE.set()
    _audit(
        f"firmware.{source}",
        controller=cid,
        sha256=digest[:16],
        verified=bool(verified_identity),
        version=installed_version,
        release_version=release_version,
    )
    return {
        "ok": True,
        "cid": cid,
        "sha256": digest,
        "source": source,
        "release_version": release_version,
        "verified": bool(verified_identity),
        "controller_version": installed_version,
        "message": (
            "firmware flashed and controller identity verified"
            if verified_identity else
            "firmware flashed, but the serial controller did not reconnect before the verification timeout"
        ),
    }, 200


@app.post("/api/rp/flash")
def api_rp_flash():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "JSON object required"}), 400
    cid = str(data.get("cid") or "").strip()
    if not _CONTROLLER_ID_RE.fullmatch(cid):
        return jsonify({"ok": False, "error": "valid cid parameter required"}), 400
    config = load_config()
    controller = next(
        (item for item in config.get("controllers", []) if item.get("id") == cid),
        None,
    )
    if not controller:
        return jsonify({"ok": False, "error": "controller not found"}), 404
    release, release_error = _latest_approved_diy_firmware(refresh=True)
    if release_error:
        return jsonify({"ok": False, "error": release_error}), 503
    if not release:
        return jsonify({"ok": False, "error": "no hardware-approved remote firmware release is published"}), 409
    requested_version = str(data.get("version") or "").strip()
    if requested_version and requested_version != release["version"]:
        return jsonify({"ok": False, "error": "the selected firmware release is no longer current"}), 409
    flash_enabled, flash_reason = _firmware_flash_availability(controller)
    if not flash_enabled:
        return jsonify({"ok": False, "error": flash_reason or "firmware update is unavailable"}), 503
    if not _FIRMWARE_FLASH_LOCK.acquire(blocking=False):
        return jsonify({"ok": False, "error": "another firmware update is already running"}), 409

    temp_path = None
    try:
        checksum_data = http_get_firmware_asset(
            release["checksum_url"],
            max_bytes=1024,
            timeout=10.0,
        )
        firmware_data = http_get_firmware_asset(
            release["asset_url"],
            max_bytes=4 * 1024 * 1024,
            timeout=30.0,
        )
        if checksum_data is None or firmware_data is None:
            return jsonify({"ok": False, "error": "approved firmware assets could not be downloaded"}), 503
        try:
            checksum_text = checksum_data.decode("ascii").strip()
        except UnicodeDecodeError:
            return jsonify({"ok": False, "error": "firmware checksum file is invalid"}), 502
        checksum_match = re.fullmatch(
            rf"([A-Fa-f0-9]{{64}})\s+\*?{re.escape(release['asset'])}",
            checksum_text,
        )
        if not checksum_match:
            return jsonify({"ok": False, "error": "firmware checksum file is invalid"}), 502

        descriptor, temp_path = tempfile.mkstemp(prefix="fanbridge-rp2040-remote-", suffix=".uf2")
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(firmware_data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_path, 0o600)
        valid, validation_error, digest = _validate_rp2040_uf2(temp_path)
        if not valid or not digest:
            return jsonify({"ok": False, "error": validation_error}), 502
        if not secrets.compare_digest(digest.lower(), checksum_match.group(1).lower()):
            return jsonify({"ok": False, "error": "firmware checksum verification failed"}), 502
        payload, status = _flash_validated_rp2040(
            cid,
            temp_path,
            digest,
            source="remote",
            release_version=release["version"],
        )
        return jsonify(payload), status
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
        _FIRMWARE_FLASH_LOCK.release()


@app.post("/api/rp/flash_upload")
def api_rp_flash_upload():
    cid = (request.form.get("cid") or "").strip()
    if not _CONTROLLER_ID_RE.fullmatch(cid):
        return jsonify({"ok": False, "error": "valid cid parameter required"}), 400
    config = load_config()
    controller = next(
        (item for item in config.get("controllers", []) if item.get("id") == cid),
        None,
    )
    if not controller:
        return jsonify({"ok": False, "error": "controller not found"}), 404
    flash_enabled, flash_reason = _firmware_flash_availability(controller)
    if not flash_enabled:
        return jsonify({"ok": False, "error": flash_reason or "firmware upload is unavailable"}), 503

    upload = request.files.get("firmware")
    filename = str(getattr(upload, "filename", "") or "")
    if upload is None or not filename.lower().endswith(".uf2"):
        return jsonify({"ok": False, "error": "select an RP2040 .uf2 firmware file"}), 400
    if not _FIRMWARE_FLASH_LOCK.acquire(blocking=False):
        return jsonify({"ok": False, "error": "another firmware update is already running"}), 409

    temp_path = None
    try:
        descriptor, temp_path = tempfile.mkstemp(prefix="fanbridge-rp2040-", suffix=".uf2")
        os.close(descriptor)
        os.chmod(temp_path, 0o600)
        upload.save(temp_path)
        valid, validation_error, digest = _validate_rp2040_uf2(temp_path)
        if not valid or not digest:
            return jsonify({"ok": False, "error": validation_error}), 400
        payload, status = _flash_validated_rp2040(
            cid,
            temp_path,
            digest,
            source="upload",
        )
        return jsonify(payload), status
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
        _FIRMWARE_FLASH_LOCK.release()


# --------- API: Exclude device ---------
@app.post("/api/exclude")
def api_exclude():
    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "JSON object required"}), 400
    raw_dev = data.get("dev")
    if not isinstance(raw_dev, str):
        return jsonify({"ok": False, "error": "device name must be a string"}), 400
    dev = raw_dev.strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", dev):
        return jsonify({"ok": False, "error": "invalid device name"}), 400
    excluded = data.get("excluded")
    if not isinstance(excluded, bool):
        return jsonify({"ok": False, "error": "excluded must be a boolean"}), 400
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
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "JSON object required"}), 400
    current = data.get("current") or ""
    new = data.get("new") or ""
    confirm = data.get("confirm") or ""

    if not all(isinstance(value, str) for value in (current, new, confirm)):
        return jsonify({"ok": False, "error": "password fields must be strings"}), 400

    if not current or not new or not confirm:
        return jsonify({"ok": False, "error": "all fields required"}), 400
    if new != confirm:
        return jsonify({"ok": False, "error": "passwords do not match"}), 400
    if len(new) < PASSWORD_MIN_LENGTH or len(new) > PASSWORD_MAX_LENGTH:
        return jsonify({"ok": False, "error": "new password must be 8 to 256 characters"}), 400

    users = _load_users()
    stored = _user_hash(users, str(user))
    if not stored or not check_password_hash(stored, current):
        return jsonify({"ok": False, "error": "current password is incorrect"}), 400

    # update hash
    users.setdefault("users", {})[user] = generate_password_hash(new)
    versions = users.setdefault("session_versions", {})
    versions[user] = _session_version(users, str(user)) + 1
    _save_users(users)
    session["auth_version"] = int(versions[user])
    try:
        _audit("auth.password_changed", user=user)
    except Exception:
        pass
    return jsonify({"ok": True})


# --------- API: Settings overrides ---------
@app.post("/api/config")
def api_config_transaction():
    """Validate settings and curves together, then perform one durable write."""
    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "JSON object required"}), 400
    settings = data.get("settings")
    curves = data.get("curves")
    if not isinstance(settings, dict) or not isinstance(curves, dict):
        return jsonify({"ok": False, "error": "settings and curves objects are required"}), 400

    setting_keys = {
        "single_override_hdd_c", "single_override_ssd_c",
        "poll_interval_seconds", "control_interval_seconds",
        "auto_apply_min_interval_seconds", "auto_apply_refresh_interval_seconds",
        "auto_apply_hysteresis_percent", "excluded_devices", "exclude_devices",
        "drive_assignments", "auto_apply", "fallback_pwm",
    }
    curve_keys = {"hdd_thresholds", "hdd_pwm", "ssd_thresholds", "ssd_pwm"}
    unknown = sorted((set(settings) - setting_keys) | (set(curves) - curve_keys))
    if unknown:
        return jsonify({"ok": False, "error": "unknown configuration fields", "fields": unknown}), 400
    if set(curves) != curve_keys:
        return jsonify({"ok": False, "error": "all HDD and SSD curve fields are required"}), 400

    current = load_config()
    candidate = copy.deepcopy(current)
    for key, raw in settings.items():
        candidate["exclude_devices" if key == "excluded_devices" else key] = copy.deepcopy(raw)
    candidate.update(copy.deepcopy(curves))
    normalised = _normalise_config(_merge_defaults(_migrate_config(candidate), DEFAULT_CONFIG))

    # Normalisation is a safety boundary, not silent API coercion. Every value
    # supplied by the client must survive it exactly (apart from set ordering).
    for key, raw in settings.items():
        canonical = "exclude_devices" if key == "excluded_devices" else key
        saved = normalised.get(canonical)
        if canonical == "exclude_devices":
            try:
                valid = (
                    isinstance(raw, list)
                    and all(isinstance(item, str) for item in raw)
                    and sorted(set(raw)) == saved
                )
            except TypeError:
                valid = False
        else:
            valid = type(raw) is type(saved) and raw == saved
        if not valid:
            return jsonify({"ok": False, "error": f"invalid value for {key}"}), 400
    for key, raw in curves.items():
        if not isinstance(raw, list) or raw != normalised.get(key):
            return jsonify({"ok": False, "error": f"invalid value for {key}"}), 400

    save_config(normalised)
    _CONTROL_WAKE.set()
    _audit("config.transaction", settings=sorted(settings), curves=sorted(curves))
    return jsonify({"ok": True, "settings": settings, "curves": curves})


@app.post("/api/settings")
def api_settings():
    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict) or not data:
        return jsonify({"ok": False, "error": "settings object is required"}), 400

    aliases = {
        "min_interval_s": "auto_apply_min_interval_seconds",
        "hysteresis_percent": "auto_apply_hysteresis_percent",
        "auto_apply_min_interval_s": "auto_apply_min_interval_seconds",
    }
    normalised = {aliases.get(key, key): value for key, value in data.items()}
    allowed = {
        "single_override_hdd_c", "single_override_ssd_c",
        "poll_interval_seconds", "control_interval_seconds",
        "auto_apply_min_interval_seconds", "auto_apply_refresh_interval_seconds",
        "auto_apply_hysteresis_percent", "excluded_devices", "exclude_devices",
        "drive_assignments", "auto_apply", "fallback_pwm",
    }
    unknown = sorted(set(normalised) - allowed)
    if unknown:
        return jsonify({"ok": False, "error": "unknown settings", "fields": unknown}), 400

    c = load_config()
    changed = {}

    def set_int(key: str, limits: tuple[int, int]):
        v = normalised.get(key, None)
        if v is None:
            return
        try:
            if isinstance(v, bool):
                raise ValueError
            iv = int(v)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be an integer")
        lo, hi = limits
        if not lo <= iv <= hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
        c[key] = iv
        changed[key] = iv

    try:
        set_int("single_override_hdd_c", (20, 90))
        set_int("single_override_ssd_c", (20, 110))
        set_int("poll_interval_seconds", (3, 60))
        set_int("control_interval_seconds", (2, 30))
        set_int("auto_apply_min_interval_seconds", (1, 60))
        set_int("auto_apply_refresh_interval_seconds", (5, 30))
        set_int("auto_apply_hysteresis_percent", (0, 25))
        set_int("fallback_pwm", (0, 100))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    if "auto_apply" in normalised:
        if not isinstance(normalised["auto_apply"], bool):
            return jsonify({"ok": False, "error": "auto_apply must be a boolean"}), 400
        c["auto_apply"] = normalised["auto_apply"]
        changed["auto_apply"] = normalised["auto_apply"]

    excluded_value = normalised.get("excluded_devices", normalised.get("exclude_devices"))
    if excluded_value is not None:
        if not isinstance(excluded_value, list) or len(excluded_value) > 256:
            return jsonify({"ok": False, "error": "excluded_devices must be a list of at most 256 device names"}), 400
        excluded: list[str] = []
        for value in excluded_value:
            dev = str(value).strip()
            if not re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", dev):
                return jsonify({"ok": False, "error": f"invalid device name: {dev[:64]}"}), 400
            excluded.append(dev)
        c["exclude_devices"] = sorted(set(excluded))
        changed["excluded_devices"] = c["exclude_devices"]

    if "drive_assignments" in normalised:
        value = normalised["drive_assignments"]
        if not isinstance(value, dict) or len(value) > 256:
            return jsonify({"ok": False, "error": "drive_assignments must be an object with at most 256 entries"}), 400
        controller_ids = {str(item.get("id")) for item in c.get("controllers", [])}
        assignments: dict[str, str] = {}
        for raw_dev, raw_target in value.items():
            dev = str(raw_dev).strip()
            target = str(raw_target).strip()
            if not re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", dev):
                return jsonify({"ok": False, "error": f"invalid assignment device: {dev[:64]}"}), 400
            if target not in {"none", *controller_ids}:
                return jsonify({"ok": False, "error": f"unknown assignment target for {dev}"}), 400
            assignments[dev] = target
        c["drive_assignments"] = assignments
        changed["drive_assignments"] = assignments

    if not changed:
        return jsonify({"ok": False, "error": "no settings changed"}), 400

    save_config(c)
    _CONTROL_WAKE.set()
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
    if not isinstance(data, dict) or not data:
        return jsonify({"ok": False, "error": "curve object is required"}), 400

    # Accept the paired UI representation during migration, but persist one
    # canonical flat schema.
    normalised = dict(data)
    for drive_type in ("hdd", "ssd"):
        points = data.get(drive_type)
        if points is not None:
            if not isinstance(points, list) or not points:
                return jsonify({"ok": False, "error": f"{drive_type} must contain curve points"}), 400
            try:
                normalised[f"{drive_type}_thresholds"] = [point[0] for point in points]
                normalised[f"{drive_type}_pwm"] = [point[1] for point in points]
            except (TypeError, IndexError):
                return jsonify({"ok": False, "error": f"invalid {drive_type} curve point"}), 400
            normalised.pop(drive_type, None)

    allowed = {"hdd_thresholds", "hdd_pwm", "ssd_thresholds", "ssd_pwm"}
    unknown = sorted(set(normalised) - allowed)
    if unknown:
        return jsonify({"ok": False, "error": "unknown curve fields", "fields": unknown}), 400

    c = load_config()
    changed = {}
    for drive_type in ("hdd", "ssd"):
        t_key = f"{drive_type}_thresholds"
        p_key = f"{drive_type}_pwm"
        if t_key not in normalised and p_key not in normalised:
            continue
        if t_key not in normalised or p_key not in normalised:
            return jsonify({"ok": False, "error": f"{t_key} and {p_key} must be supplied together"}), 400
        try:
            thresholds = [int(value) for value in normalised[t_key]]
            pwms = [int(value) for value in normalised[p_key]]
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": f"{drive_type} curve values must be integer lists"}), 400
        if not 2 <= len(thresholds) <= 32 or len(thresholds) != len(pwms):
            return jsonify({"ok": False, "error": f"{drive_type} curve must contain 2-32 paired points"}), 400
        if any(not 0 <= value <= 120 for value in thresholds) or any(b <= a for a, b in zip(thresholds, thresholds[1:])):
            return jsonify({"ok": False, "error": f"{drive_type} temperatures must be strictly increasing within 0-120"}), 400
        if any(not 0 <= value <= 100 for value in pwms) or any(b < a for a, b in zip(pwms, pwms[1:])):
            return jsonify({"ok": False, "error": f"{drive_type} PWM values must be non-decreasing within 0-100"}), 400
        c[t_key] = thresholds
        c[p_key] = pwms
        changed[t_key] = thresholds
        changed[p_key] = pwms

    if not changed:
        return jsonify({"ok": False, "error": "no curves changed"}), 400
    save_config(c)
    _CONTROL_WAKE.set()
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
            "control_interval_seconds",
            "auto_apply",
            "auto_apply_min_interval_seconds",
            "auto_apply_refresh_interval_seconds",
            "auto_apply_hysteresis_percent",
            "fallback_pwm",
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


def _safe_stop_registered_controllers_on_exit() -> None:
    """Best-effort immediate full cooling during a graceful container exit.

    The firmware's independent 60-second lease remains the crash/SIGKILL
    fallback. This hook shortens the normal Gunicorn/Docker stop path without
    making process shutdown a safety dependency.
    """
    try:
        controllers = list(serial_svc.list_registered_controllers())
    except Exception:
        controllers = []
    for controller in controllers:
        cid = str(controller.get("id") or "")
        if not cid:
            continue
        try:
            result = serial_svc.safe_stop_controller(cid)
            if not result.get("ok"):
                log.warning(
                    "graceful-exit safe-stop was not verified | cid=%s error=%s",
                    cid,
                    result.get("error") or "unknown",
                )
        except Exception as exc:
            try:
                log.warning("graceful-exit safe-stop failed | cid=%s error=%s", cid, exc)
            except Exception:
                pass


if _in_docker():
    atexit.register(_safe_stop_registered_controllers_on_exit)
    try:
        users = _load_users()
        if not users.get("users"):
            _load_or_create_setup_token()
    except Exception as exc:
        log.error("Unable to initialise first run setup token: %s", exc)
        raise


_start_control_loop()


if __name__ == "__main__":
    APP_VERSION = "local"
    app.secret_key = _load_or_create_secret()
    try:
        app.config["TEMPLATES_AUTO_RELOAD"] = True
        app.jinja_env.auto_reload = True
    except Exception:
        pass
    # Local dev conveniences: show URL and optionally open browser
    host = os.environ.get("FANBRIDGE_DEV_HOST", "127.0.0.1").strip()
    if host not in {"127.0.0.1", "::1"}:
        host = "127.0.0.1"
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
    debug_enabled = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug_enabled, use_reloader=False)
