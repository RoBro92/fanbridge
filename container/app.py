from flask import Flask, jsonify
import os, time, yaml, subprocess, glob, shlex, shutil

app = Flask(__name__)
STARTED = time.time()

CONFIG_PATH = os.environ.get("FANBRIDGE_CONFIG", "/config/config.yml")

DEFAULT_CONFIG = {
    "mode": "real",               # default to real SMART on Unraid
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

def _smart_temp_from_attrs(text: str) -> int | None:
    # Try common patterns without spinning up (we already use -n standby where applicable)
    for line in text.splitlines():
        # smartctl -A for ATA/SATA (ID 194 or 190)
        if line.startswith("194 ") or line.startswith("190 "):
            parts = line.split()
            if len(parts) >= 10:
                try:
                    return int(parts[9])
                except Exception:
                    pass
        # smartctl for NVMe sometimes prints "Temperature: 41 C" or "Current Drive Temperature: 41 C"
        if "Current Drive Temperature:" in line:
            parts = line.split(":")
            if len(parts) >= 2:
                try:
                    return int(parts[1].strip().split()[0])
                except Exception:
                    pass
        if line.strip().startswith("Temperature:"):
            try:
                return int(line.strip().split()[1])
            except Exception:
                pass
    return None

def _enumerate_block_devices() -> list[tuple[str, str]]:
    """
    Return list of (device_path, dev_name) for /dev/sd? and /dev/nvme?n1 that exist.
    """
    devs = []
    for path in sorted(glob.glob("/dev/sd?")):
        devs.append((path, os.path.basename(path)))
    for path in sorted(glob.glob("/dev/nvme?n1")):
        devs.append((path, os.path.basename(path)))
    return devs

def _smart_read_drive(path: str, name: str) -> dict:
    # Choose command flags
    if name.startswith("nvme"):
        cmd = ["smartctl", "-A", path]
        dtype = "SSD"
    else:
        cmd = ["smartctl", "-n", "standby", "-A", path]  # won't spin up
        dtype = "HDD" if _is_hdd(name) else "SSD"

    cp = _run(cmd)
    if cp.returncode == 2:
        # 2 means device is in low-power mode; treat as spun down
        return {"dev": name, "type": dtype, "temp": None, "state": "spun down"}
    if cp.returncode != 0 or not cp.stdout:
        return {"dev": name, "type": dtype, "temp": None, "state": "N/A"}

    temp = _smart_temp_from_attrs(cp.stdout)
    if temp is None:
        return {"dev": name, "type": dtype, "temp": None, "state": "N/A"}
    return {"dev": name, "type": dtype, "temp": temp, "state": "on"}

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
                drives.append(_smart_read_drive(path, name))
    else:
        # (Optional) keep sim path if you ever flip it back for Mac testing
        for d in (cfg.get("sim", {}).get("drives", []) or []):
            drives.append({
                "dev": d.get("name"),
                "type": d.get("type", "HDD"),
                "temp": d.get("temp"),
                "state": "on" if d.get("temp") is not None else "spun down"
            })

    hdd_vals = [d["temp"] for d in drives if d.get("type") == "HDD" and d.get("temp") is not None]
    ssd_vals = [d["temp"] for d in drives if d.get("type") == "SSD" and d.get("temp") is not None]

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