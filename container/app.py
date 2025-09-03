from flask import Flask, jsonify, request
import os, time, yaml, json, configparser, glob

app = Flask(__name__)
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
        "": os.environ.get("FANBRIDGE_VERSION", "0.1.0"),
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
    # Use configured poll interval, clamp 3–60s
    try:
        pi = int((cfg or {}).get("poll_interval_seconds", 7))
    except Exception:
        pi = 7
    if pi < 3: pi = 3
    if pi > 60: pi = 60

    html = """
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <title>fanbridge</title>
      <style>
        body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }
        :root { --bg:#ffffff; --fg:#111827; --muted:#6b7280; --border:#e5e7eb; --thbg:#f6f8fa; --pillbg:#fafafa; --cardbg:#ffffff; }
        body.dark { --bg:#0f172a; --fg:#e5e7eb; --muted:#94a3b8; --border:#334155; --thbg:#111827; --pillbg:#1f2937; --cardbg:#0b1220; }
        body { background: var(--bg); color: var(--fg); }
        .muted { color: var(--muted); }
        table, th, td { border-color: var(--border); }
        th { background: var(--thbg); }
        .pill { border:1px solid var(--border); border-radius:999px; padding:3px 10px; background:var(--pillbg); font-size: 12px; color: var(--fg); }
        button { padding:6px 10px; border:1px solid var(--border); background:var(--pillbg); border-radius:6px; cursor:pointer; color: var(--fg); }
        button:hover { filter: brightness(1.05); }
        a { color: inherit; text-decoration: underline; }
        h1 { margin: 0 0 8px; }
        .meta { color: #666; margin-bottom: 16px; }
        table { border-collapse: collapse; width: 100%; max-width: 880px; }
        th, td { border: 1px solid; padding: 8px 10px; text-align: left; }
        .ok { color: #0a7; font-weight: 600; }
        .warn { color: #d97706; font-weight: 600; }
        .crit { color: #d32; font-weight: 700; }
        .flex { display:flex; gap:12px; align-items:center; flex-wrap: wrap; }
        .pill.warn { border-color:#f59e0b; background:#fff7ed; color:#92400e; }
        .small { font-size: 12px; }
        .right { float:right; }
        .footer { margin-top: 16px; }
        code { background:#f6f8fa; padding:2px 6px; border-radius:4px; }
        .panel { border:1px solid var(--border); border-radius:8px; padding:12px; background: var(--cardbg); max-width: 880px; margin-top:16px; }
        .panel h2 { margin:0 0 8px; font-size:16px; }
        .chk { display:inline-flex; align-items:center; gap:6px; margin:6px 12px 6px 0; }
        .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap:6px 12px; }
        .inputs { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
        input[type="number"] { width: 72px; padding:4px 6px; }
        /* Centre + tighten specific columns */
        th.type, td.type { text-align: center; width: 90px; }
        th.state, td.state { text-align: center; width: 90px; }
        th.temp, td.temp { text-align: center; width: 110px; }
        th.inc, td.inc { text-align: center; width: 90px; }
        td.inc { text-align:center; }
        input.incl { transform: scale(1.3); accent-color: #16a34a; }
        .grid#curveH_th, .grid#curveH_pw, .grid#curveS_th, .grid#curveS_pw { grid-template-columns: repeat(auto-fit, minmax(70px, 1fr)); }
        .grid#curveH_th input, .grid#curveH_pw input, .grid#curveS_th input, .grid#curveS_pw input { width: 64px; }
        .rowlabel { min-width: 110px; }
        /* Form controls follow theme */
        input, select {
          background: var(--cardbg);
          color: var(--fg);
          border: 1px solid var(--border);
          color-scheme: light;
        }
        body.dark input, body.dark select {
          color-scheme: dark;
        }
        /* Improve visibility of number input spin buttons in dark mode (WebKit/Blink) */
        body.dark input[type="number"]::-webkit-outer-spin-button,
        body.dark input[type="number"]::-webkit-inner-spin-button {
            opacity: 1 !important;
            /* Make the arrows pop on dark backgrounds (Chromium/WebKit) */
            filter: invert(1) brightness(1.8) contrast(2)
                    drop-shadow(0 0 0.5px #000);
        }
        /* Firefox honours color-scheme; the above ensures good contrast on Chromium/WebKit */
        input[disabled], select[disabled] { opacity: .75; }

        /* Ensure temp cells don't inherit odd backgrounds in dark mode */
        td.temp.ok, td.temp.warn, td.temp.crit {
          background: transparent !important;
          text-shadow: none;
        }
      </style>
    </head>
    <body>
      <h1>fanbridge</h1>
      <div class="meta flex">
        <span id="ver" class="pill">version: …</span>
        <span class="pill">refresh: every __PI__s</span>
        <span id="mtime" class="pill">disks.ini: …</span>
        <span class="small muted" id="updated">last update: …</span>
        <a href="/api/status" class="right small" style="margin-left:12px;">API</a><a href="/health" class="right small" style="margin-left:12px;">Health</a>
        <button id="themeToggle" class="pill" type="button">Theme: …</button>
        <span id="dirtyFlag" class="pill warn" style="display:none;">Unsaved changes</span>
      </div>

      <table id="tbl">
        <thead>
          <tr>
            <th>Device</th>
            <th class="type">Type</th>
            <th class="state">State</th>
            <th class="temp">Temp (°C)</th>
            <th class="inc">Included</th>
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

      <div class="panel">
        <h3>Configuration</h3>
        <h2>Single Drive Temp Overrides</h2>
        <div class="inputs">
          <label>HDD override (°C): <input type="number" id="hddovr" min="30" max="70" step="1"></label>
          <label>SSD override (°C): <input type="number" id="ssdovr" min="40" max="90" step="1"></label>
          <button id="savebtn">Save overrides</button>
          <button id="discardbtn" style="display:none;">Discard</button>
        </div>
        <h3 style="margin-bottom:8px;">Fan curves</h3>
        <div class="small muted" style="margin-bottom:10px;">Set the mapping from average pool temperature to PWM. Left to right = increasing temperature.</div>

        <h2 style="margin:6px 0 6px;">HDD fan curve</h2>
        <div class="inputs" style="align-items:flex-start;">
          <div class="rowlabel small muted" style="width:130px;">Thresholds (°C)</div>
          <div class="grid" id="curveH_th"></div>
        </div>
        <div class="inputs" style="align-items:flex-start; margin-top:6px;">
          <div class="rowlabel small muted" style="width:130px;">PWM (%)</div>
          <div class="grid" id="curveH_pw"></div>
        </div>

        <h2 style="margin:12px 0 6px;">SSD fan curve</h2>
        <div class="inputs" style="align-items:flex-start;">
          <div class="rowlabel small muted" style="width:130px;">Thresholds (°C)</div>
          <div class="grid" id="curveS_th"></div>
        </div>
        <div class="inputs" style="align-items:flex-start; margin-top:6px;">
          <div class="rowlabel small muted" style="width:130px;">PWM (%)</div>
          <div class="grid" id="curveS_pw"></div>
        </div>

        <div class="inputs" style="margin-top:10px;">
          <button id="savecurves">Save fan curves</button>
          <button id="discardcurves" style="display:none;">Discard</button>
        </div>
      </div>

      <!-- Footer health section removed -->

      <script>
      const POLL_MS = __PI__ * 1000;
      const fmt = (v) => (v === null || v === undefined) ? "—" : v;
      // dynamic colour class based on per-type override thresholds
      let OVERRIDE_HDD = 45;
      let OVERRIDE_SSD = 60;
      const WARN_DELTA = 5; // orange when within 5°C of override
      let HTH = [], HPW = [], STH = [], SPW = [];
      let DIRTY_OVR = false;   // overrides edited but not saved
      let DIRTY_CURVES = false; // fan curves edited but not saved

      function setDirty(which, v=true){
        if(which === 'ovr') DIRTY_OVR = v; else if(which === 'curves') DIRTY_CURVES = v;
        const any = DIRTY_OVR || DIRTY_CURVES;
        document.getElementById('dirtyFlag').style.display = any ? '' : 'none';
        document.getElementById('discardbtn').style.display = DIRTY_OVR ? '' : 'none';
        document.getElementById('discardcurves').style.display = DIRTY_CURVES ? '' : 'none';
      }

      const clsForTemp = (t, type) => {
        if (t === null || t === undefined) return "muted";
        const thr = (String(type).toUpperCase() === 'SSD') ? OVERRIDE_SSD : OVERRIDE_HDD;
        if (t >= thr) return "crit";            // red at/above override
        if (t >= (thr - WARN_DELTA)) return "warn"; // orange within 5°C
        return "ok";
      };
      async function refresh() {
        try {
          const r = await fetch('/api/status', { cache: 'no-store' });
          const j = await r.json();
          document.getElementById('ver').textContent = 'version: ' + (j.version || '—');
          document.getElementById('pwm').textContent = (j.recommended_pwm ?? '—') + '%';
          const now = new Date();
          document.getElementById('updated').textContent = 'last update: ' + now.toLocaleString();
          const mt = j.disks_ini_mtime ? new Date(j.disks_ini_mtime * 1000).toLocaleTimeString() : 'n/a';
          document.getElementById('mtime').textContent = 'disks.ini: ' + mt;
          // sync dynamic thresholds from server so colours reflect current settings
          if (typeof j.override_hdd_c === 'number') OVERRIDE_HDD = j.override_hdd_c;
          if (typeof j.override_ssd_c === 'number') OVERRIDE_SSD = j.override_ssd_c;
          if (Array.isArray(j.hdd_thresholds)) HTH = j.hdd_thresholds.slice();
          if (Array.isArray(j.hdd_pwm)) HPW = j.hdd_pwm.slice();
          if (Array.isArray(j.ssd_thresholds)) STH = j.ssd_thresholds.slice();
          if (Array.isArray(j.ssd_pwm)) SPW = j.ssd_pwm.slice();
          const rows = document.getElementById('rows');
          rows.innerHTML = '';
          (j.drives || []).forEach(d => {
            const tr = document.createElement('tr');
            const tdDev = document.createElement('td'); tdDev.textContent = d.dev ? (d.slot ? (d.dev + ' (' + d.slot + ')') : d.dev) : '—';
            const tdType = document.createElement('td'); tdType.textContent = d.type || '—'; tdType.className = 'type';
            const tdState = document.createElement('td'); tdState.textContent = d.state || '—'; tdState.className = 'state';
            const tdTemp = document.createElement('td'); tdTemp.textContent = fmt(d.temp); tdTemp.className = 'temp ' + clsForTemp(d.temp, d.type);
            const tdInc = document.createElement('td'); tdInc.className = 'inc';
            const cbInc = document.createElement('input');
            cbInc.type = 'checkbox';
            cbInc.className = 'incl';
            cbInc.checked = !d.excluded; // checked means INCLUDED in calculations
            cbInc.onchange = async (e) => {
              try {
                await fetch('/api/exclude', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ dev: d.dev, excluded: !e.target.checked })
                });
              } catch (err) { console.error(err); }
            };
            tdInc.appendChild(cbInc);
            tr.append(tdDev, tdType, tdState, tdTemp, tdInc);
            rows.appendChild(tr);
          });
          const hs = j.hdd || {}, ss = j.ssd || {};
          document.getElementById('hddstats').textContent = (fmt(hs.avg) + ' / ' + fmt(hs.min) + ' / ' + fmt(hs.max) + '  (n=' + fmt(hs.count) + ')');
          document.getElementById('ssdstats').textContent = (fmt(ss.avg) + ' / ' + fmt(ss.min) + ' / ' + fmt(ss.max) + '  (n=' + fmt(ss.count) + ')');
          // populate override inputs only if not being edited (dirty)
          if (!DIRTY_OVR) {
            if (typeof j.override_hdd_c !== 'undefined') document.getElementById('hddovr').value = j.override_hdd_c;
            if (typeof j.override_ssd_c !== 'undefined') document.getElementById('ssdovr').value = j.override_ssd_c;
          }
          // populate fan curve editors (skip if user is editing to prevent overwrites)
          if (!DIRTY_CURVES) {
            const ch_th = document.getElementById('curveH_th'); ch_th.innerHTML='';
            const ch_pw = document.getElementById('curveH_pw'); ch_pw.innerHTML='';
            const cs_th = document.getElementById('curveS_th'); cs_th.innerHTML='';
            const cs_pw = document.getElementById('curveS_pw'); cs_pw.innerHTML='';

            const makeNum = (val)=>{ const i=document.createElement('input'); i.type='number'; i.min='0'; i.max='100'; i.step='1'; i.value = val; i.addEventListener('input', ()=> setDirty('curves', true)); return i; };

            const nH = Math.max(HTH.length, HPW.length) || 1;
            const nS = Math.max(STH.length, SPW.length) || 1;
            ch_th.style.gridTemplateColumns = `repeat(${nH}, minmax(70px, 1fr))`;
            ch_pw.style.gridTemplateColumns = `repeat(${nH}, minmax(70px, 1fr))`;
            cs_th.style.gridTemplateColumns = `repeat(${nS}, minmax(70px, 1fr))`;
            cs_pw.style.gridTemplateColumns = `repeat(${nS}, minmax(70px, 1fr))`;

            const stepsH = nH;
            const stepsS = nS;

            for (let i=0;i<stepsH;i++) { ch_th.appendChild(makeNum(HTH[i]!==undefined?HTH[i]:0)); ch_pw.appendChild(makeNum(HPW[i]!==undefined?HPW[i]:0)); }
            for (let i=0;i<stepsS;i++) { cs_th.appendChild(makeNum(STH[i]!==undefined?STH[i]:0)); cs_pw.appendChild(makeNum(SPW[i]!==undefined?SPW[i]:0)); }
          }
        } catch (e) {
          console.error(e);
        }
      }
      refresh();
      setInterval(refresh, POLL_MS);
      document.getElementById('hddovr').addEventListener('input', ()=> setDirty('ovr', true));
      document.getElementById('ssdovr').addEventListener('input', ()=> setDirty('ovr', true));
      document.getElementById('savebtn').addEventListener('click', async () => {
        const h = parseInt(document.getElementById('hddovr').value, 10);
        const s = parseInt(document.getElementById('ssdovr').value, 10);
        try {
          await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              single_override_hdd_c: isNaN(h) ? undefined : h,
              single_override_ssd_c: isNaN(s) ? undefined : s
            })
          });
          setDirty('ovr', false);
          refresh();
        } catch (err) { console.error(err); }
      });
      document.getElementById('savecurves').addEventListener('click', async () => {
        const ch_th = document.getElementById('curveH_th').querySelectorAll('input');
        const ch_pw = document.getElementById('curveH_pw').querySelectorAll('input');
        const cs_th = document.getElementById('curveS_th').querySelectorAll('input');
        const cs_pw = document.getElementById('curveS_pw').querySelectorAll('input');
        const HTH2 = Array.from(ch_th).map(i => parseInt(i.value||'0',10));
        const HPW2 = Array.from(ch_pw).map(i => parseInt(i.value||'0',10));
        const STH2 = Array.from(cs_th).map(i => parseInt(i.value||'0',10));
        const SPW2 = Array.from(cs_pw).map(i => parseInt(i.value||'0',10));
        try {
          await fetch('/api/curves', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ hdd_thresholds: HTH2, hdd_pwm: HPW2, ssd_thresholds: STH2, ssd_pwm: SPW2 })
          });
          setDirty('curves', false);
          refresh();
        } catch (err) { console.error(err); }
      });
      document.getElementById('discardbtn').addEventListener('click', ()=> { setDirty('ovr', false); refresh(); });
      document.getElementById('discardcurves').addEventListener('click', ()=> { setDirty('curves', false); refresh(); });
      // ---- Theme toggle (light/dark) ----
      const THEME_KEY = 'fanbridgeTheme';
      function applyTheme(t){
        const dark = (t === 'dark');
        document.body.classList.toggle('dark', dark);
        const btn = document.getElementById('themeToggle');
        if (btn) btn.textContent = 'Theme: ' + (dark ? 'Dark' : 'Light');
        try { localStorage.setItem(THEME_KEY, dark ? 'dark' : 'light'); } catch(e) {}
      }
      (function(){
        let pref = 'light';
        try {
          pref = localStorage.getItem(THEME_KEY) || (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
        } catch(e) {}
        applyTheme(pref);
        const tbtn = document.getElementById('themeToggle');
        if (tbtn) tbtn.addEventListener('click', ()=> {
          const next = document.body.classList.contains('dark') ? 'light' : 'dark';
          applyTheme(next);
        });
      })();
      </script>
    </body>
    </html>
    """
    html = html.replace("__PI__", str(pi))
    return html

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)