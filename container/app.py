from flask import Flask, jsonify, request, render_template
import os, time, yaml, json, configparser, glob, pathlib

# Load environment variables from possible .env locations
try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None  # fallback if not installed

# Resolve paths relative to this file
_BASE = pathlib.Path(__file__).resolve().parent
_PROJECT_ROOT = _BASE.parent  # repo root if container copies to /app

if load_dotenv:
    # Load order: project root .env, /app/.env, and /config/.env (mounted on Unraid)
    for _p in (
        _PROJECT_ROOT / ".env",
        _BASE / ".env",
        pathlib.Path("/config/.env"),
    ):
        try:
            load_dotenv(str(_p), override=False)
        except Exception:
            pass

def _read_version_from_release() -> str | None:
    """Read version from project RELEASE.md. Expected first matching line formats:
    - "Version: X.Y.Z"
    - Markdown header like "# vX.Y.Z" or "## 1.2.3" or "## [1.2.3]".
    Returns the version string if found, else None.
    """
    import re
    candidates = [
        _PROJECT_ROOT / "RELEASE.md",
        _PROJECT_ROOT / "CHANGELOG.md",
    ]
    rx_list = [
        re.compile(r"^\s*Version\s*:\s*([0-9]+(?:\.[0-9]+){1,3})\b", re.I),
        re.compile(r"^\s*#+\s*v?([0-9]+\.[0-9]+(?:\.[0-9]+)?)\b"),
        re.compile(r"^\s*\[v?([0-9]+\.[0-9]+(?:\.[0-9]+)?)\]"),
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

# Canonical version source: RELEASE.md. Fall back to env, then "dev".
APP_VERSION = _read_version_from_release() or os.environ.get("FANBRIDGE_VERSION") or os.environ.get("VERSION") or "dev"

app = Flask(__name__, template_folder="templates", static_folder="static")
STARTED = time.time()

# Paths
CONFIG_PATH = os.environ.get("FANBRIDGE_CONFIG", "/config/config.yml")
DISKS_INI = "/unraid/disks.ini"   # bind-mount to /var/local/emhttp/disks.ini on host

# Defaults (UI can change via future /api/config)
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
    "idle_cutoff_hdd_c": 30,  # below this, HDD fan is 0%
    "idle_cutoff_ssd_c": 35,  # below this, SSD fan is 0%
    "sim": { "drives": [] },        # optional: for non‑Unraid local testing
}

# ---------- config helpers ----------
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
        print(f"[fanbridge] Created default config at {CONFIG_PATH}")

def load_config():
    ensure_config_exists()
    try:
        with open(CONFIG_PATH, "r") as f:
            user_cfg = yaml.safe_load(f) or {}
    except Exception:
        with open(CONFIG_PATH, "w") as wf:
            yaml.safe_dump(DEFAULT_CONFIG, wf, sort_keys=False)
        print(f"[fanbridge] Rewrote unreadable config with defaults at {CONFIG_PATH}")
        return DEFAULT_CONFIG
    merged = _merge_defaults(user_cfg, DEFAULT_CONFIG)
    if merged != user_cfg:
        try:
            with open(CONFIG_PATH, "w") as f:
                yaml.safe_dump(merged, f, sort_keys=False)
            print("[fanbridge] Normalised config with defaults (saved).")
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
    # nvme0n1 -> nvme0
    ctrl = dev.split("n", 1)[0]
    # Typical paths: /sys/class/nvme/nvme0/device/hwmon/hwmonX/temp1_input
    candidates = glob.glob(f"/sys/class/nvme/{ctrl}/device/hwmon/hwmon*/temp*_input")
    for p in candidates:
        val = _read_file(p)
        if val and val.strip().isdigit():
            # value is in millidegrees or degrees depending on platform; handle both
            n = int(val.strip())
            if n > 1000:
                n = n // 1000
            if 0 <= n <= 120:
                return n
    return None

def _is_hdd(dev_name: str) -> bool:
    # NVMe → SSD, else use rotational when available, default HDD
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
        print(f"[fanbridge] Failed to parse {DISKS_INI}: {e}")
        return []

    drives: list[dict] = []
    excludes = set(cfg.get("exclude_devices") or [])

    for section in cp.sections():
        # Accept any section that provides a device field (diskX, parity, cache, etc.)
        dev = _unquote(cp.get(section, "device", fallback=""))
        slot = _unquote(cp.get(section, "name", fallback=""))
        if not dev:
            continue

        # temperature: blank or "NA" → None; clamp to 0–120C
        temp_raw = _unquote(cp.get(section, "temp", fallback=""))
        temp: int | None = None
        if temp_raw.isdigit():
            t = int(temp_raw)
            if 0 <= t <= 120:
                temp = t

        # spundown = "1" means disk is sleeping
        spundown = _unquote(cp.get(section, "spundown", fallback="0")) == "1"

        # Reconcile with sysfs hints conservatively:
        # - If disks.ini says spun down (spundown==True), KEEP it (do not override to up).
        # - If disks.ini says active (spundown==False) but sysfs clearly says suspended, flip to spun down.
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
            print(f"[fanbridge] disks.ini has no temp for {d['dev']} (type={d['type']})")

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

    return {
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

@app.get("/health")
def health():
    return jsonify({"status": "ok", "uptime_s": int(time.time() - STARTED)})

@app.after_request
def add_no_cache(resp):
    # Make JSON responses always fresh in browsers / proxies
    if resp.mimetype == "application/json":
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

@app.get("/api/status")
def status():
    return jsonify(compute_status())


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


# --------- API: Settings overrides ---------
@app.post("/api/settings")
def api_settings():
    data = request.get_json(force=True, silent=True) or {}
    c = load_config()
    changed = {}
    def set_int(key, default):
        v = data.get(key, None)
        if v is None:
            return
        try:
            iv = int(str(v).strip())
            c[key] = iv
            changed[key] = iv
        except Exception:
            pass
    set_int("single_override_hdd_c", c.get("single_override_hdd_c", 45))
    set_int("single_override_ssd_c", c.get("single_override_ssd_c", 60))
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
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
