from flask import Blueprint, jsonify, request, make_response
import datetime, time, os, sys
from core.logging_setup import LOG_RING as _LOG_RING, LOG_LOCK as _LOG_LOCK
from services import serial as serial_svc
from flask import current_app

bp = Blueprint("logs", __name__)

_LEVELS = {
    "DEBUG": 10,
    "NORMAL": 20,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}


@bp.get("/logs")
def api_logs():
    try:
        since = int(request.args.get("since", "0") or "0")
    except Exception:
        since = 0
    min_level = _LEVELS.get((request.args.get("min_level") or "").upper(), 10)
    try:
        limit = max(1, min(1000, int(request.args.get("limit", "500") or "500")))
    except Exception:
        limit = 500

    try:
        src = list(_LOG_RING) if isinstance(_LOG_RING, list) else list(_LOG_RING)
    except Exception:
        src = []
    # filter
    items = []
    try:
        for it in reversed(src):
            if since and int(it.get("id", 0)) <= int(since):
                break
            lvl = it.get("level", "INFO")
            if _LEVELS.get(str(lvl).upper(), 10) < int(min_level):
                continue
            items.append(it)
            if len(items) >= limit:
                break
    except Exception:
        items = []

    return jsonify({"ok": True, "items": list(reversed(items)), "count": len(items)})


@bp.post("/log_level")
def api_log_level():
    data = request.get_json(force=True, silent=True) or {}
    level = (data.get("level") or "").upper()
    import logging
    if level not in _LEVELS:
        return jsonify({"ok": False, "error": "invalid level"}), 400
    logging.getLogger().setLevel(_LEVELS[level])
    return jsonify({"ok": True, "level": level})


@bp.post("/logs/clear")
def api_logs_clear():
    try:
        if _LOG_LOCK is not None:
            with _LOG_LOCK:
                _LOG_RING.clear()  # type: ignore[attr-defined]
        else:
            _LOG_RING.clear()  # type: ignore[attr-defined]
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/logs/download")
def api_logs_download():
    # Accept both fmt and format for compatibility with older UI code
    fmt = request.args.get("format") or request.args.get("fmt") or "text"
    try:
        src = list(_LOG_RING) if isinstance(_LOG_RING, list) else list(_LOG_RING)
    except Exception:
        src = []

    items = src[-500:] if fmt != "json" else src

    # Build diagnostics bundle
    def _collect_diagnostics() -> dict:
        import platform as _platform
        info: dict = {}
        cfg = current_app.config.get('FB_APP_INFO') or {}
        info["timestamp_utc"] = datetime.datetime.utcnow().isoformat(timespec='seconds')
        info["uptime_s"] = int(time.time() - int(cfg.get('STARTED') or 0))
        info["version"] = cfg.get('APP_VERSION')
        info["in_docker"] = bool(cfg.get('IN_DOCKER_FUNC') and cfg['IN_DOCKER_FUNC']())
        try:
            info["python"] = sys.version.split()[0]
            info["platform"] = f"{sys.platform} | {_platform.platform()}"
        except Exception:
            pass
        try:
            info["paths"] = {
                "config": {"path": cfg.get('CONFIG_PATH'), "exists": os.path.exists(str(cfg.get('CONFIG_PATH') or ''))},
                "users":  {"path": cfg.get('USERS_PATH'),  "exists": os.path.exists(str(cfg.get('USERS_PATH') or ''))},
                "disks_ini": {
                    "path": cfg.get('DISKS_INI'),
                    "exists": os.path.exists(str(cfg.get('DISKS_INI') or '')),
                    "mtime": int(os.path.getmtime(str(cfg.get('DISKS_INI') or ''))) if os.path.exists(str(cfg.get('DISKS_INI') or '')) else None,
                },
            }
        except Exception:
            pass
        try:
            info["env"] = {k: v for (k, v) in os.environ.items() if k.upper().startswith("FANBRIDGE_")}
        except Exception:
            pass
        try:
            info["serial_status"] = serial_svc.get_serial_status(full=True)
            vres = serial_svc.serial_send_line("version", expect_reply=True)
            info["controller_version_reply"] = vres.get("reply")
        except Exception:
            pass
        try:
            mounts: list[str] = []
            with open("/proc/mounts", "r", encoding="utf-8", errors="ignore") as f:
                for ln in f.readlines()[:500]:
                    if any(tag in ln for tag in ("/config", "/unraid", "/proc", "/sys", "/")):
                        mounts.append(ln.strip())
            info["mounts"] = mounts
        except Exception:
            info["mounts"] = []
        return info

    diagnostics = _collect_diagnostics()

    ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    filename = f"fanbridge-logs-{ts}.{ 'json' if fmt == 'json' else 'txt' }"
    if fmt == "json":
        import json
        payload = {"ok": True, "diagnostics": diagnostics, "items": items}
        resp = make_response(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=False))
        resp.headers["Content-Type"] = "application/json; charset=utf-8"
    else:
        lines = []
        lines.append("==== FanBridge Diagnostics ====")
        lines.append(f"Timestamp (UTC): {diagnostics.get('timestamp_utc')}")
        lines.append(f"Uptime (s): {diagnostics.get('uptime_s')}")
        lines.append(f"Version: {diagnostics.get('version')}")
        lines.append(f"In Docker: {diagnostics.get('in_docker')}")
        py = diagnostics.get('python'); plat = diagnostics.get('platform')
        lines.append(f"Python: {py} | Platform: {plat}")
        try:
            p = diagnostics.get('paths', {})
            lines.append("Paths:")
            for key in ("config", "users", "disks_ini"):
                item = p.get(key, {}) if isinstance(p, dict) else {}
                lines.append(f"  - {key}: {item.get('path')} exists={item.get('exists')} mtime={item.get('mtime')}")
        except Exception:
            pass
        try:
            env = diagnostics.get('env', {}) or {}
            if env:
                lines.append("Env (FANBRIDGE_*):")
                for k, v in env.items():
                    lines.append(f"  {k}={v}")
        except Exception:
            pass
        try:
            ss = diagnostics.get('serial_status', {}) or {}
            lines.append("Serial:")
            lines.append(f"  connected={ss.get('connected')} preferred={ss.get('preferred')} baud={ss.get('baud')} message={ss.get('message')}")
            ports = ss.get('ports') or []
            if ports:
                lines.append(f"  ports: {', '.join(ports[:12])}{' ...' if len(ports)>12 else ''}")
            cv = diagnostics.get('controller_version_reply')
            if cv:
                lines.append(f"  controller version: {cv}")
        except Exception:
            pass
        try:
            m = diagnostics.get('mounts') or []
            if m:
                lines.append("Mounts:")
                for ln in m[:50]:
                    lines.append(f"  {ln}")
        except Exception:
            pass
        lines.append("==== End Diagnostics ====")
        lines.append("")
        for it in items:
            try:
                t = datetime.datetime.fromtimestamp(int(it.get("ts", 0))).isoformat(timespec='seconds')
            except Exception:
                t = str(it.get("ts", ""))
            lines.append(f"{t} | {it.get('level','')} | {it.get('name','')} | {it.get('msg','')}")
        resp = make_response("\n".join(lines) + ("\n" if lines else ""))
        resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    resp.headers["Content-Disposition"] = f"attachment; filename=\"{filename}\""
    return resp
