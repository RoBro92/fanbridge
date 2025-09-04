from flask import Flask, jsonify, request, render_template, session, redirect, url_for, make_response, g
import os, time, yaml, json, configparser, glob, pathlib, logging, sys, traceback



try:
    import serial  # type: ignore
    from serial.tools import list_ports  # type: ignore
except Exception:
    serial = None
    list_ports = None
import secrets, hashlib, datetime
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from dotenv import load_dotenv  
except Exception:
    load_dotenv = None  

_BASE = pathlib.Path(__file__).resolve().parent
_PROJECT_ROOT = _BASE.parent 


def _secret_path() -> pathlib.Path:
    # persist a stable session secret across restarts
    return (_BASE / "secret.key") if not _in_docker() else pathlib.Path("/config/secret.key")

def _load_or_create_secret() -> str:
    p = _secret_path()
    try:
        if p.exists():
            return p.read_text().strip()
        key = secrets.token_urlsafe(32)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(key)
        return key
    except Exception:
        return secrets.token_urlsafe(32)

def _in_docker() -> bool:
    try:
        return os.path.exists("/.dockerenv")
    except Exception:
        return False

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
def _setup_logging():
    lvl_name = os.environ.get("FANBRIDGE_LOG_LEVEL") or ("DEBUG" if os.environ.get("FLASK_DEBUG") else "INFO")
    level = getattr(logging, str(lvl_name).upper(), logging.INFO)

    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(stream=sys.stdout)
        fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        handler.setFormatter(logging.Formatter(fmt))
        root.addHandler(handler)
    root.setLevel(level)

    # Reduce noisy libraries
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

_setup_logging()
log = logging.getLogger("fanbridge")

def _read_version_from_release() -> str | None:
    """Read version from project RELEASE.md. Expected first matching line formats:
    - "Version: X.Y.Z"
    - Markdown header like "# vX.Y.Z" or "## 1.2.3" or "## [1.2.3]".
    Returns the version string if found, else None.
    """
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

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = _load_or_create_secret()
if os.environ.get("TEMPLATES_AUTO_RELOAD") == "1" or os.environ.get("FLASK_DEBUG"):
    try:
        app.config["TEMPLATES_AUTO_RELOAD"] = True
        app.jinja_env.auto_reload = True
    except Exception:
        pass
STARTED = time.time()
log.info("FanBridge starting | version=%s in_docker=%s", APP_VERSION or "unknown", str(_in_docker()).lower())


def _default_config_path() -> str:
    # When not in Docker (e.g., running `python3 app.py`), prefer a local file
    # so no special setup is required for development.
    return "/config/config.yml" if _in_docker() else str(_BASE / "config.local.yml")

CONFIG_PATH = os.environ.get("FANBRIDGE_CONFIG") or _default_config_path()
DISKS_INI = "/unraid/disks.ini"   # bind-mount to /var/local/emhttp/disks.ini on host
USERS_PATH = "/config/users.yml" if _in_docker() else str((_BASE / "users.local.yml"))

# Serial preference and baud configurable via environment
SERIAL_PREF = os.environ.get("FANBRIDGE_SERIAL_PORT", "").strip()
SERIAL_BAUD = int(os.environ.get("FANBRIDGE_SERIAL_BAUD", "115200") or "115200")

try:
    log.info(
        "paths | config=%s users=%s disks_ini=%s exists=%s serial_pref=%s baud=%s",
        CONFIG_PATH, USERS_PATH, DISKS_INI, str(os.path.exists(DISKS_INI)).lower(), os.environ.get("FANBRIDGE_SERIAL_PORT", ""), SERIAL_BAUD
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
}

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

_RATE = {}  # ip -> [timestamps]
def _allow(ip: str, limit=20, window=60) -> bool:
    now = time.time()
    arr = _RATE.get(ip, [])
    arr = [t for t in arr if now - t < window]
    if len(arr) >= limit:
        _RATE[ip] = arr
        return False
    arr.append(now)
    _RATE[ip] = arr
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

    

# ---------- Unraid disks.ini parsing ----------
def _read_file(path: str) -> str | None:
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except Exception:
        return None

# Helper to normalise/strip quotes from INI values
def _unquote(s: str | None) -> str:
    if s is None:
        return ""
    s = s.strip()
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        s = s[1:-1]
    return s.strip()

def _sysfs(dev: str, rel: str) -> str | None:
    d = _unquote(dev)
    return _read_file(f"/sys/block/{d}/{rel}")

def _spin_state_from_sysfs(dev: str) -> bool | None:
    """
    Returns False if clearly active, True if clearly spun down, or None if unknown.
    We consult a couple of sysfs hints:
      - device/state: often "running" for active devices
      - power/runtime_status: "active" vs "suspended"
    """
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

def _nvme_temp_sysfs(dev: str) -> int | None:
    """
    Try to grab NVMe temp from sysfs when disks.ini has '*' (unknown).
    We look for hwmon temp1_input attached to this nvme device.
    """
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

def _read_disks_ini() -> list[dict]:
    """
    Parse Unraid's /var/local/emhttp/disks.ini (bind-mounted to /unraid/disks.ini).
    Returns list of drive dicts with dev, type, temp, state, excluded.
    """
    if not os.path.exists(DISKS_INI):
        return []

    cp = configparser.ConfigParser()
    try:
        cp.read(DISKS_INI)
    except Exception as e:
        log.exception("Failed to parse %s: %s", DISKS_INI, e)
        return []

    drives: list[dict] = []
    excludes = set(cfg.get("exclude_devices") or [])

    for section in cp.sections():
        dev = _unquote(cp.get(section, "device", fallback=""))
        slot = _unquote(cp.get(section, "name", fallback=""))
        if not dev:
            continue
        temp_raw = _unquote(cp.get(section, "temp", fallback=""))
        temp: int | None = None
        if temp_raw.isdigit():
            t = int(temp_raw)
            if 0 <= t <= 120:
                temp = t

        spundown = _unquote(cp.get(section, "spundown", fallback="0")) == "1"

        ss = _spin_state_from_sysfs(dev)
        if (spundown is False) and (ss is True):
            spundown = True
        # Otherwise leave 'spundown' as reported by disks.ini.

        # If temp is unknown for NVMe but device is active, try sysfs
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
            "excluded": (dclean in excludes),
        })
    return drives

# ---------- Serial helpers ----------
def _unique_order(seq):
    seen = set()
    out = []
    for x in seq:
        if x and x not in seen:
            out.append(x)
            seen.add(x)
    return out

def list_serial_ports():
    """Return an ordered, de-duplicated list of plausible serial devices inside the container."""
    candidates = []
    # Prefer stable udev by-id links if the host mapped them
    candidates.extend(sorted(glob.glob("/dev/serial/by-id/*")))
    # Common CDC ACM and USB serial nodes
    candidates.extend(sorted(glob.glob("/dev/ttyACM*")))
    candidates.extend(sorted(glob.glob("/dev/ttyUSB*")))
    # pyserial discovery (best-effort)
    if list_ports:
        try:
            for p in list_ports.comports():
                if p.device:
                    candidates.append(p.device)
        except Exception:
            pass
    return _unique_order(candidates)

def probe_serial_open(port: str, baud: int | None = None):
    """Quick open/close to verify device access without committing to a protocol."""
    if not port:
        return False, "no port specified"
    if serial is None:
        return False, "pyserial not available"
    try:
        s = serial.Serial(port=port, baudrate=baud or SERIAL_BAUD, timeout=0.2)
        try:
            ok = True
        finally:
            s.close()
        return ok, "ok"
    except Exception as e:
        msg = str(e)
        # Permission hints
        lower = msg.lower()
        if any(k in lower for k in ("permission", "denied", "operation not permitted")):
            msg = (
                f"{msg} (hint: add --device {port}, "
                "Extra Params: --device-cgroup-rule='c 166:* rmw' --device-cgroup-rule='c 188:* rmw' "
                "--group-add=16, and map /dev/serial/by-id if available)"
            )
        try:
            logging.getLogger("fanbridge").warning(
                "serial open failed | port=%s baud=%s err=%s", port, baud or SERIAL_BAUD, msg
            )
        except Exception:
            pass
        return False, msg

def get_serial_status(full: bool = True):
    """Build a status dict for /api/serial/status and embed in /api/status."""
    ports = list_serial_ports()
    preferred = SERIAL_PREF if SERIAL_PREF else (ports[0] if ports else "")
    available = bool(ports)
    connected = False
    message = "no ports detected"

    if preferred:
        ok, msg = probe_serial_open(preferred, SERIAL_BAUD)
        connected = ok
        message = msg
        if not ok:
            try:
                lvl = logging.WARNING if any(s in str(msg).lower() for s in ("denied", "permission", "not opened", "busy")) else logging.INFO
                logging.getLogger("fanbridge").log(
                    lvl,
                    "serial not connected | port=%s baud=%s reason=%s (map device and grant permissions)",
                    preferred, SERIAL_BAUD, msg,
                )
            except Exception:
                pass
    elif available:
        message = "ports detected but none selected"

    data = {
        "preferred": preferred,
        "ports": ports if full else None,
        "available": available,
        "connected": connected,
        "baud": SERIAL_BAUD,
        "message": message,
    }
    if not full:
        data.pop("ports", None)
    try:
        logging.getLogger("fanbridge").debug(
            "serial | preferred=%s available=%s connected=%s baud=%s msg=%s ports=%s",
            data.get("preferred"), data.get("available"), data.get("connected"), data.get("baud"), data.get("message"), len(ports)
        )
    except Exception:
        pass
    return data

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

    # Prefer Unraid's disks.ini (no privileges required)
    if os.path.exists(DISKS_INI):
        mode = "disks.ini"
        drives = _read_disks_ini()
    else:
        # sim (for non-Unraid local testing)
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
    except Exception:
        pass

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
    }
    try:
        log.debug(
            "status | mode=%s hdd_avg=%s ssd_avg=%s pwm=%s drives=%s",
            mode, hdd.get("avg"), ssd.get("avg"), payload["recommended_pwm"], len(drives)
        )
    except Exception:
        pass
    return payload

@app.get("/health")
def health():
    return jsonify({"status": "ok", "uptime_s": int(time.time() - STARTED)})

@app.after_request
def add_no_cache(resp):
    # Make JSON responses always fresh in browsers / proxies
    if resp.mimetype == "application/json":
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

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
        # Always surface non-2xx responses
        if code >= 500:
            lg.error("%s %s -> %s in %sms", meth, path, code, dur_ms)
        elif code >= 400:
            lg.warning("%s %s -> %s in %sms", meth, path, code, dur_ms)
        else:
            # Success paths: keep health noisy at debug
            if path in ("/health", "/api/status", "/api/serial/status"):
                lg.debug("%s %s -> %s in %sms", meth, path, code, dur_ms)
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
    if p.startswith("/static/") or p in ("/login", "/health"):
        return
    # require login
    if "user" not in session:
        return redirect(url_for("login", next=request.path))
    # rate limit
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    if not _allow(ip):
        return make_response(("Too Many Requests", 429))
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
        ss = get_serial_status(full=False)
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
    return jsonify(get_serial_status(full=True))



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

    save_config(c)
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
    return jsonify({"ok": True, "changed": changed})


# --------- API: Reset to defaults (overrides + fan curves) ---------
@app.post("/api/reset_defaults")
def api_reset_defaults():
    """
    Reset editable configuration fields to DEFAULT_CONFIG:
      - single_override_hdd_c
      - single_override_ssd_c
      - hdd_thresholds / hdd_pwm
      - ssd_thresholds / ssd_pwm
      - poll_interval_seconds (so UI pill matches defaults)
    """
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
        ]
        for k in keys:
            if k in defaults:
                c[k] = defaults[k]

        save_config(c)
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
    app.run(host="0.0.0.0", port=8080, debug=True)
