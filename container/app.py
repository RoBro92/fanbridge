from flask import Flask, jsonify
import os, time, yaml, subprocess, glob, shlex, shutil, json, configparser

app = Flask(__name__)
STARTED = time.time()

CONFIG_PATH = os.environ.get("FANBRIDGE_CONFIG", "/config/config.yml")
DISKS_INI = "/unraid/disks.ini"

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

def _read_disks_ini() -> list[dict]:
    """
    Parse Unraid's /var/local/emhttp/disks.ini (bind-mounted to /unraid/disks.ini) if present.
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
        if not section.startswith("disk"):
            continue
        dev = cp.get(section, "device", fallback="").strip()
        if not dev:
            continue
        # temperature: blank or "NA" → None
        temp_val = cp.get(section, "temp", fallback="").strip()
        temp: int | None = None
        if temp_val.isdigit():
            t = int(temp_val)
            if 0 <= t <= 120:
                temp = t
        spundown = cp.get(section, "spundown", fallback="0").strip() == "1"

        # Type: NVMe → SSD; else consult rotational when available
        if dev.startswith("nvme"):
            dtype = "SSD"
        else:
            rot_path = f"/sys/block/{dev}/queue/rotational"
            rot = _read_file(rot_path)
            if rot == "0":
                dtype = "SSD"
            else:
                dtype = "HDD"

        state = "spun down" if spundown else ("on" if temp is not None else "N/A")
        drives.append({
            "dev": dev,
            "type": dtype,
            "temp": temp,
            "state": state,
            "excluded": (dev in excludes),
        })
    return drives

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

    drives: list[dict] = []

    # Prefer Unraid's disks.ini if bind-mounted (no special privileges required)
    if os.path.exists(DISKS_INI):
        drives = _read_disks_ini()
        mode = "disks.ini"
    else:
        mode = cfg.get("mode", "real")
        if mode == "real":
            if not shutil.which("smartctl"):
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

@app.after_request
def add_no_cache(resp):
    # Make /api/status always fresh in browsers / proxies
    if resp.mimetype == "application/json":
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

@app.get("/api/status")
def status():
    return jsonify(compute_status())

@app.get("/")
def index():
    # Use configured poll interval, clamp 3–60s, default 7s
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