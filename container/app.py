from flask import Flask, jsonify
import os, time, yaml, subprocess, glob, shlex, shutil, json

app = Flask(__name__)
STARTED = time.time()

CONFIG_PATH = os.environ.get("FANBRIDGE_CONFIG", "/config/config.yml")

DEFAULT_CONFIG = {
    "mode": "real",              
    "poll_interval_seconds": 15,
    "hdd_thresholds": [20,25,28,30,32,34,36,38,40,42],
    "hdd_pwm":        [10,15,20,30,40,50,60,70,85,100],
    "ssd_thresholds": [25,30,35,38,40,42,45,48,50,55],
    "ssd_pwm":        [10,10,15,25,35,45,55,65,80,95],
    "single_override_hdd_c": 45,
    "single_override_ssd_c": 60,
    "override_pwm": 100,
    "fallback_pwm": 10,
    "pwm_hysteresis": 3,
    "exclude_devices": [],
}

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

def _read_file(path: str) -> str | None:
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except Exception:
        return None

def _is_hdd(dev_name: str) -> bool:
    # SATA/SAS: /sys/block/sdX/queue/rotational = 1 → HDD, 0 → SSD
    rot = _read_file(f"/sys/block/{dev_name}/queue/rotational")
    if rot is not None:
        return rot.strip() == "1"
    # NVMe: treat as SSD
    if dev_name.startswith("nvme"):
        return False
    # default conservative: HDD
    return True

def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def _smart_json_multi(path: str, name: str) -> tuple[int, dict]:
    """
    Try a sequence of device types for maximum compatibility without waking disks.
    SATA/SAS/USB bridges: try sat → scsi → auto with -n standby.
    NVMe: use -d nvme; if caps block, we may fall back to sysfs later.
    """
    # NVMe controller or namespace
    if name.startswith("nvme"):
        cmd = ["smartctl", "-j", "-A", "-d", "nvme", path]
        cp = _run(cmd)
        try:
            data = json.loads(cp.stdout) if cp.stdout else {}
        except Exception:
            data = {}
        return cp.returncode, data

    # SATA/SAS/USB bridges: try multiple transport types
    for dtype in ("sat", "scsi", "auto"):
        cmd = ["smartctl", "-j", "-n", "standby", "-A"]
        if dtype != "auto":
            cmd += ["-d", dtype]
        cmd.append(path)
        cp = _run(cmd)
        try:
            data = json.loads(cp.stdout) if cp.stdout else {}
        except Exception:
            data = {}
        if cp.returncode in (0, 2) and data:
            return cp.returncode, data

    return 1, {}

def _nvme_temp_sysfs(ctrl_or_ns: str) -> int | None:
    """
    Fallback: read NVMe temperature from sysfs hwmon when smartctl lacks caps.
    Returns °C if available else None.
    """
    try:
        # Normalise to controller name e.g. nvme0 from nvme0n1
        ctrl = ctrl_or_ns.split("n")[0] if "n" in ctrl_or_ns else ctrl_or_ns
        for hw in glob.glob("/sys/class/hwmon/hwmon*"):
            devlink = os.path.realpath(os.path.join(hw, "device"))
            if f"/{ctrl}/" in devlink:
                tpath = os.path.join(hw, "temp1_input")
                if os.path.exists(tpath):
                    val = _read_file(tpath)
                    if val:
                        s = val.strip()
                        if s.isdigit():
                            v = int(s)
                            return v // 1000 if v > 1000 else v
    except Exception:
        pass
    return None

def _smart_json(path: str, name: str) -> tuple[int, dict]:
    return _smart_json_multi(path, name)

# --- SMART helpers (JSON-based, robust) --------------------------------------
def _enumerate_block_devices() -> list[tuple[str, str]]:
    """
    Enumerate whole-disk nodes (no partitions). Do not exclude anything here,
    so the UI can show *all* discoverable devices to the user for opt-out.
    """
    devs: list[tuple[str, str]] = []

    # SATA/SAS whole disks: sda, sdb, sdaa… (exclude names containing digits = partitions)
    try:
        for entry in sorted(os.listdir("/sys/block")):
            if entry.startswith("sd"):
                if any(ch.isdigit() for ch in entry):
                    continue  # skip partitions like sda1
                path = f"/dev/{entry}"
                if os.path.exists(path):
                    devs.append((path, entry))
    except Exception:
        pass

    # NVMe: prefer controller nodes nvmeX; fall back to nvme?n1 if controller node isn't present
    try:
        nvme_ctrls = set()
        for entry in sorted(os.listdir("/sys/block")):
            if entry.startswith("nvme"):
                if "p" in entry:
                    continue  # skip partitions
                ctrl = entry.split("n")[0] if "n" in entry else entry
                nvme_ctrls.add(ctrl)
        for ctrl in sorted(nvme_ctrls):
            path_ctrl = f"/dev/{ctrl}"
            if os.path.exists(path_ctrl):
                devs.append((path_ctrl, ctrl))
            # Also offer the default namespace if present
            ns_path = f"/dev/{ctrl}n1"
            if os.path.exists(ns_path):
                devs.append((ns_path, f"{ctrl}n1"))
    except Exception:
        pass

    return devs

def _smart_state_and_temp(rc: int, data: dict, name: str) -> tuple[str, int | None]:
    """
    Decide (state, tempC) from smartctl JSON + return code.
    rc == 2 → in standby (not spun up); rc == 0 → OK/active; others → N/A.
    """
    # Determine standby/low-power from JSON if available
    in_standby = False
    pm = data.get("power_mode") or {}
    if isinstance(pm, dict):
        in_standby = bool(pm.get("is_in_standby") or pm.get("is_in_low_power_mode"))
    if rc == 2 or in_standby:
        return ("spun down", None)
    if rc != 0:
        return ("N/A", None)

    # --- ATA path: prefer raw.value; then raw.string first int; then temperature.current
    ata = data.get("ata_smart_attributes") or {}
    table = ata.get("table") or []
    for row in table:
        try:
            attr_id = int(row.get("id", -1))
        except Exception:
            continue
        if attr_id in (194, 190):
            raw = row.get("raw")
            # Case 1: dict form with an integer 'value'
            if isinstance(raw, dict):
                val = raw.get("value")
                if isinstance(val, int):
                    temp = val
                    if 0 <= temp <= 120:
                        return ("on", temp)
                # maybe a 'string' like "39 (Min/Max 20/60)"
                s = raw.get("string")
                if isinstance(s, str):
                    # take first integer token
                    for part in s.split():
                        if part.isdigit():
                            temp = int(part)
                            if 0 <= temp <= 120:
                                return ("on", temp)
                            break
            # Case 2: raw is already a string
            if isinstance(raw, str):
                for part in raw.split():
                    if part.isdigit():
                        temp = int(part)
                        if 0 <= temp <= 120:
                            return ("on", temp)
                        break

    # Some firmwares also expose temperature.current
    tblock = data.get("temperature") or {}
    if isinstance(tblock, dict):
        t = tblock.get("current")
        if isinstance(t, int) and 0 <= t <= 120:
            return ("on", t)

    # --- NVMe path: typical locations
    nvme_log = data.get("nvme_smart_health_information_log") or {}
    if isinstance(nvme_log, dict):
        t = nvme_log.get("temperature")
        if isinstance(t, int) and 0 <= t <= 120:
            return ("on", t)

    # Final fallback for NVMe when smartctl cannot read temperature
    if name.startswith("nvme"):
        t = _nvme_temp_sysfs(name)
        if isinstance(t, int) and 0 <= t <= 120:
            return ("on", t)

    return ("on", None)

def _smart_read_drive(path: str, name: str) -> dict:
    dtype = "SSD" if name.startswith("nvme") or not _is_hdd(name) else "HDD"
    rc, data = _smart_json(path, name)
    state, temp = _smart_state_and_temp(rc, data, name)
    if state == "N/A" and not data and rc != 0:
        print(f"[fanbridge] smartctl failed for {name} rc={rc}")
    excluded = name in set(cfg.get("exclude_devices") or [])
    return {"dev": name, "type": dtype, "temp": temp, "state": state, "excluded": excluded}
# ---------------------------------------------------------------------------

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

    mode = cfg.get("mode", "real")
    drives: list[dict] = []

    if mode == "real":
        # Ensure smartctl exists
        if not shutil.which("smartctl"):
            # Avoid import at top to keep footprint small
            drives = []
        else:
            for path, name in _enumerate_block_devices():
                if not name:
                    continue  # guard against any bogus blank entries
                drives.append(_smart_read_drive(path, name))
    else:
        # (Optional) keep sim path if you ever flip it back for Mac testing
        for d in (cfg.get("sim", {}).get("drives", []) or []):
            drives.append({
                "dev": d.get("name"),
                "type": d.get("type", "HDD"),
                "temp": d.get("temp"),
                "state": "on" if d.get("temp") is not None else "spun down",
                "excluded": False,
            })

    for d in drives:
        if d["state"] == "N/A":
            print(f"[fanbridge] SMART read failed for {d['dev']} (type={d['type']})")

    hdd_vals = [d["temp"] for d in drives if d.get("type") == "HDD" and not d.get("excluded") and d.get("temp") is not None]
    ssd_vals = [d["temp"] for d in drives if d.get("type") == "SSD" and not d.get("excluded") and d.get("temp") is not None]

    def stats(vals):
        if not vals:
            return {"avg": 0, "min": 0, "max": 0, "count": 0}
        return {"avg": int(sum(vals)/len(vals)), "min": min(vals), "max": max(vals), "count": len(vals)}

    hdd = stats(hdd_vals)
    ssd = stats(ssd_vals)

    override = False
    if hdd_vals and max(hdd_vals) >= int(cfg.get("single_override_hdd_c", 45)):
        override = True
    if ssd_vals and max(ssd_vals) >= int(cfg.get("single_override_ssd_c", 60)):
        override = True

    if override:
        recommended_pwm = int(cfg.get("override_pwm", 100))
    else:
        pwm_hdd = map_temp_to_pwm(hdd["avg"], cfg["hdd_thresholds"], cfg["hdd_pwm"]) if hdd["count"] else 0
        pwm_ssd = map_temp_to_pwm(ssd["avg"], cfg["ssd_thresholds"], cfg["ssd_pwm"]) if ssd["count"] else 0
        recommended_pwm = max(pwm_hdd, pwm_ssd)
        if hdd["count"] == 0 and ssd["count"] == 0:
            recommended_pwm = int(cfg.get("fallback_pwm", 10))

    return {
        "drives": drives,
        "hdd": hdd,
        "ssd": ssd,
        "recommended_pwm": int(recommended_pwm),
        "override": override,
        "mode": mode,
        "version": os.environ.get("FANBRIDGE_VERSION", "dev"),
    }

@app.get("/health")
def health():
    return jsonify({"status": "ok", "uptime_s": int(time.time() - STARTED)})

@app.get("/api/status")
def status():
    return jsonify(compute_status())

@app.get("/")
def index():
    return """
    <h1>fanbridge</h1>
    <p>Running (real mode).</p>
    <ul>
      <li><a href="/health">/health</a></li>
      <li><a href="/api/status">/api/status</a></li>
    </ul>
    """

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)