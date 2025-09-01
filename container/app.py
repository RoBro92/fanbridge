from flask import Flask, jsonify
import os, time, yaml

app = Flask(__name__)
STARTED = time.time()

CONFIG_PATH = os.environ.get("FANBRIDGE_CONFIG", "/config/config.yml")

def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        # fallback to example config shipped in image
        with open("/config/config.yml", "r") as f:
            return yaml.safe_load(f) or {}

cfg = load_config()

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
    # Hot-reload each call (handy while tuning locally)
    cfg = load_config()

    mode = cfg.get("mode", "sim")
    drives = []

    if mode == "sim":
        for d in (cfg.get("sim", {}).get("drives", []) or []):
            drives.append({
                "dev": d.get("name"),
                "type": d.get("type", "HDD"),
                "temp": d.get("temp"),
                "state": "on" if d.get("temp") is not None else "spun down"
            })
    else:
        # We'll add real SMART parsing later; placeholder for now
        drives = []

    # Pool stats
    hdd_vals = [d["temp"] for d in drives if d["type"]=="HDD" and d["temp"] is not None]
    ssd_vals = [d["temp"] for d in drives if d["type"]=="SSD" and d["temp"] is not None]

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

    return {
        "drives": drives,
        "hdd": hdd,
        "ssd": ssd,
        "recommended_pwm": int(recommended_pwm),
        "override": override,
        "mode": mode,
        "version": os.environ.get("FANBRIDGE_VERSION", "dev")
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
    <p>Scaffold running.</p>
    <ul>
      <li><a href="/health">/health</a></li>
      <li><a href="/api/status">/api/status</a></li>
    </ul>
    """

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)