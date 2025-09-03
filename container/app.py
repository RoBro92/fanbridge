from flask import Flask, jsonify
import os, time, yaml, json, configparser, glob

app = Flask(__name__)
STARTED = time.time()

# Paths
CONFIG_PATH = os.environ.get("FANBRIDGE_CONFIG", "/config/config.yml")
DISKS_INI = "/unraid/disks.ini"   # bind-mount to /var/local/emhttp/disks.ini on host

# Defaults (UI can change via future /api/config)
DEFAULT_CONFIG = {
    "poll_interval_seconds": 7,     # UI refresh; clamped 3–60s
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

        # If sysfs clearly indicates activity, prefer it over disks.ini lag
        ss = _spin_state_from_sysfs(dev)
        if ss is False:
            spundown = False

        # If temp is unknown for NVMe but device is active, try sysfs
        dclean = _unquote(dev)
        if temp is None and dclean.startswith("nvme") and not spundown:
            t_nv = _nvme_temp_sysfs(dclean)
            if isinstance(t_nv, int):
                temp = t_nv

        dtype = "SSD" if not _is_hdd(dclean) else "HDD"
        state = "spun down" if spundown else ("on" if temp is not None else "N/A")
        drives.append({
            "dev": dclean,
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
            drives.append({
                "dev": d.get("name"),
                "type": d.get("type", "HDD"),
                "temp": d.get("temp"),
                "state": "on" if d.get("temp") is not None else "spun down",
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
        "recommended_pwm": int(recommended_pwm),
        "override": override,
        "mode": mode,
        "version": os.environ.get("FANBRIDGE_VERSION", "0.0.1"),
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

@app.get("/")
def index():
    # Use configured poll interval, clamp 3–60s
    try:
        pi = int((cfg or {}).get("poll_interval_seconds", 7))
    except Exception:
        pi = 7
    if pi < 3: pi = 3
    if pi > 60: pi = 60

    html = f"""
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <title>fanbridge</title>
      <style>
        body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }}
        h1 {{ margin: 0 0 8px; }}
        .meta {{ color: #666; margin-bottom: 16px; }}
        table {{ border-collapse: collapse; width: 100%; max-width: 880px; }}
        th, td {{ border: 1px solid #ddd; padding: 8px 10px; text-align: left; }}
        th {{ background: #f6f8fa; }}
        .ok {{ color: #0a7; font-weight: 600; }}
        .warn {{ color: #d97706; font-weight: 600; }}
        .crit {{ color: #d32; font-weight: 700; }}
        .muted {{ color: #777; }}
        .flex {{ display:flex; gap:12px; align-items:center; flex-wrap: wrap; }}
        .pill {{ border:1px solid #ddd; border-radius:999px; padding:3px 10px; background:#fafafa; font-size: 12px; }}
        .small {{ font-size: 12px; }}
        .right {{ float:right; }}
        .footer {{ margin-top: 16px; }}
        code {{ background:#f6f8fa; padding:2px 6px; border-radius:4px; }}
      </style>
    </head>
    <body>
      <h1>fanbridge</h1>
      <div class="meta flex">
        <span id="mode" class="pill">mode: …</span>
        <span id="ver" class="pill">version: …</span>
        <span class="pill">refresh: every {pi}s</span>
        <span id="mtime" class="pill">disks.ini: …</span>
        <span class="small muted" id="updated">last update: …</span>
        <a href="/api/status" class="right small">API</a>
      </div>

      <table id="tbl">
        <thead>
          <tr>
            <th>Device</th>
            <th>Type</th>
            <th>State</th>
            <th>Temp (°C)</th>
            <th class="small">Excluded</th>
          </tr>
        </thead>
        <tbody id="rows">
          <tr><td colspan="5" class="muted">Loading…</td></tr>
        </tbody>
        <tfoot>
          <tr>
            <th colspan="5">
              HDD avg/min/max: <span id="hddstats" class="small muted">—</span> &nbsp; | &nbsp;
              SSD avg/min/max: <span id="ssdstats" class="small muted">—</span> &nbsp; | &nbsp;
              Recommended PWM: <strong id="pwm">—</strong>
            </th>
          </tr>
        </tfoot>
      </table>

      <div class="footer small muted">
        Health: <a href="/health">/health</a>
      </div>

      <script>
      const POLL_MS = {pi} * 1000;
      const fmt = (v) => (v === null || v === undefined) ? "—" : v;
      const clsForTemp = (t) => {{
        if (t === null) return "muted";
        if (t >= 55) return "crit";
        if (t >= 45) return "warn";
        return "ok";
      }};
      async function refresh() {{
        try {{
          const r = await fetch('/api/status', {{ cache: 'no-store' }});
          const j = await r.json();
          document.getElementById('mode').textContent = 'mode: ' + (j.mode || '—');
          document.getElementById('ver').textContent = 'version: ' + (j.version || '—');
          document.getElementById('pwm').textContent = (j.recommended_pwm ?? '—') + '%';
          const now = new Date();
          document.getElementById('updated').textContent = 'last update: ' + now.toLocaleString();
          const mt = j.disks_ini_mtime ? new Date(j.disks_ini_mtime * 1000).toLocaleTimeString() : 'n/a';
          document.getElementById('mtime').textContent = 'disks.ini: ' + mt;
          const rows = document.getElementById('rows');
          rows.innerHTML = '';
          (j.drives || []).forEach(d => {{
            const tr = document.createElement('tr');
            const tdDev = document.createElement('td'); tdDev.textContent = d.dev || '—';
            const tdType = document.createElement('td'); tdType.textContent = d.type || '—';
            const tdState = document.createElement('td'); tdState.textContent = d.state || '—';
            const tdTemp = document.createElement('td'); tdTemp.textContent = fmt(d.temp);
            tdTemp.className = clsForTemp(d.temp);
            const tdEx = document.createElement('td'); tdEx.textContent = d.excluded ? 'yes' : 'no'; tdEx.className='small muted';
            tr.append(tdDev, tdType, tdState, tdTemp, tdEx);
            rows.appendChild(tr);
          }});
          const hs = j.hdd || {{}}, ss = j.ssd || {{}};
          document.getElementById('hddstats').textContent = `${{fmt(hs.avg)}} / ${{fmt(hs.min)}} / ${{fmt(hs.max)}}  (n=${{fmt(hs.count)}})`;
          document.getElementById('ssdstats').textContent = `${{fmt(ss.avg)}} / ${{fmt(ss.min)}} / ${{fmt(ss.max)}}  (n=${{fmt(ss.count)}})`;
        }} catch (e) {{
          console.error(e);
        }}
      }}
      refresh();
      setInterval(refresh, POLL_MS);
      </script>
    </body>
    </html>
    """
    return html

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)