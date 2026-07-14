from flask import Blueprint, jsonify, request, make_response
import datetime, time, os, sys, re
from core.logging_setup import LOG_RING as _LOG_RING, LOG_LOCK as _LOG_LOCK
from services import serial as serial_svc
from flask import current_app

bp = Blueprint("logs", __name__)
_CID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_LOG_SCOPES = {"all", "system", "controller"}
_CONTROLLER_MARKER_RE = re.compile(
    r'(?:\b[a-z_]*cid=[a-z][a-z0-9_-]{0,31}\b|'
    r'\[[a-z][a-z0-9_-]{0,31}\]|'
    r'"controller"\s*:\s*"[a-z][a-z0-9_-]{0,31}")'
)

_LEVELS = {
    "DEBUG": 10,
    "NORMAL": 20,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}


def _controller_message_matches(message: object, cid: str = "") -> bool:
    text = str(message or "")
    if not cid:
        return bool(_CONTROLLER_MARKER_RE.search(text))
    escaped = re.escape(cid)
    return bool(re.search(
        rf'(?:\b[a-z_]*cid={escaped}\b|\[{escaped}\]|'
        rf'"controller"\s*:\s*"{escaped}")',
        text,
    ))


def _parse_log_scope(*, cid: str = "") -> tuple[str, str | None]:
    scope = (request.args.get("scope") or ("controller" if cid else "all")).strip().lower()
    if scope not in _LOG_SCOPES:
        return scope, "invalid log scope"
    if scope == "controller" and not cid:
        return scope, "controller id required for controller log scope"
    if scope == "system" and cid:
        return scope, "controller id is not valid for system log scope"
    return scope, None


def _item_in_scope(item: dict, scope: str, cid: str = "") -> bool:
    message = item.get("msg")
    if scope == "system":
        return not _controller_message_matches(message)
    if scope == "controller":
        return _controller_message_matches(message, cid)
    return True


@bp.get("/logs")
def api_logs():
    """Return recent log items from the in-process ring buffer.

    Query params:
      - since: last seen item id (int). Items with id <= since are omitted.
      - min_level: DEBUG/INFO/WARNING/ERROR (inclusive filter).
      - limit: max number of items to return (1..1000).
    """
    try:
        since = int(request.args.get("since", "0") or "0")
    except Exception:
        since = 0
    min_level = _LEVELS.get((request.args.get("min_level") or "").upper(), 10)
    cid = (request.args.get("cid") or "").strip()
    if cid and not _CID_RE.fullmatch(cid):
        return jsonify({"ok": False, "error": "invalid controller id"}), 400
    scope, scope_error = _parse_log_scope(cid=cid)
    if scope_error:
        return jsonify({"ok": False, "error": scope_error}), 400
    try:
        limit = max(1, min(1000, int(request.args.get("limit", "500") or "500")))
    except Exception:
        limit = 500

    try:
        src = list(_LOG_RING) if isinstance(_LOG_RING, list) else list(_LOG_RING)
    except Exception:
        src = []

    # Determine last id in the ring for clients to advance cursors
    last_id = 0
    try:
        if src:
            last_id = int((src[-1] or {}).get("id", 0))
    except Exception:
        last_id = 0

    # Filter by id and minimum level
    items = []
    try:
        for it in reversed(src):
            if since and int(it.get("id", 0)) <= int(since):
                break
            if not _item_in_scope(it, scope, cid):
                continue
            lvl = it.get("level", "INFO")
            if _LEVELS.get(str(lvl).upper(), 10) < int(min_level):
                continue
            items.append(it)
            if len(items) >= limit:
                break
    except Exception:
        items = []

    # Current effective root level (number)
    import logging as _logging
    try:
        current_level = int(_logging.getLogger().getEffectiveLevel())
    except Exception:
        current_level = 20

    return jsonify({
        "ok": True,
        "items": list(reversed(items)),
        "count": len(items),
        "last_id": last_id,
        "level": current_level,
    })


@bp.post("/log_level")
def api_log_level():
    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "JSON object required"}), 400
    raw_level = data.get("level")
    if not isinstance(raw_level, str):
        return jsonify({"ok": False, "error": "level must be a string"}), 400
    level = raw_level.upper()
    import logging
    if level not in _LEVELS:
        return jsonify({"ok": False, "error": "invalid level"}), 400
    logging.getLogger().setLevel(_LEVELS[level])
    return jsonify({"ok": True, "level": level})


@bp.post("/logs/clear")
def api_logs_clear():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "JSON object required"}), 400
    scope = str(data.get("scope") or "all").strip().lower()
    cid = str(data.get("cid") or "").strip()
    if scope not in _LOG_SCOPES:
        return jsonify({"ok": False, "error": "invalid log scope"}), 400
    if cid and not _CID_RE.fullmatch(cid):
        return jsonify({"ok": False, "error": "invalid controller id"}), 400
    if scope == "controller" and not cid:
        return jsonify({"ok": False, "error": "controller id required for controller log scope"}), 400
    if scope == "system" and cid:
        return jsonify({"ok": False, "error": "controller id is not valid for system log scope"}), 400

    def _clear_scope() -> None:
        if scope == "all":
            _LOG_RING.clear()  # type: ignore[attr-defined]
            return
        retained = [item for item in _LOG_RING if not _item_in_scope(item, scope, cid)]
        _LOG_RING.clear()  # type: ignore[attr-defined]
        _LOG_RING.extend(retained)  # type: ignore[attr-defined]

    try:
        if _LOG_LOCK is not None:
            with _LOG_LOCK:
                _clear_scope()
        else:
            _clear_scope()
        return jsonify({"ok": True})
    except Exception:
        current_app.logger.exception("failed to clear application log ring")
        return jsonify({"ok": False, "error": "could not clear logs"}), 500


@bp.get("/logs/download")
def api_logs_download():
    # Accept both fmt and format for compatibility with older UI code
    fmt = request.args.get("format") or request.args.get("fmt") or "text"
    requested_cid = (request.args.get("cid") or "").strip()
    if requested_cid and not _CID_RE.fullmatch(requested_cid):
        return jsonify({"ok": False, "error": "invalid controller id"}), 400
    scope, scope_error = _parse_log_scope(cid=requested_cid)
    if scope_error:
        return jsonify({"ok": False, "error": scope_error}), 400
    try:
        src = list(_LOG_RING) if isinstance(_LOG_RING, list) else list(_LOG_RING)
    except Exception:
        src = []

    src = [item for item in src if _item_in_scope(item, scope, requested_cid)]
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
        safe_env = {
            "FANBRIDGE_LOG_LEVEL",
            "FANBRIDGE_SECURE_COOKIES",
            "FANBRIDGE_DISKS_STALE_WARN_SEC",
            "FANBRIDGE_CONTROL_LOOP",
        }
        try:
            info["env"] = {key: os.environ[key] for key in sorted(safe_env) if key in os.environ}
        except Exception:
            info["env"] = {}
        cid = requested_cid
        if cid:
            try:
                info["serial_status"] = serial_svc.get_serial_status(cid, full=True)
                # A quarantined 2.1/2.2 DIY board must receive no further
                # diagnostics: those releases incorrectly renewed their unsafe
                # lease for every command. Only query a positively verified
                # controller.
                if info["serial_status"].get("connected"):
                    vres = serial_svc.serial_send_line(cid, "VERSION", expect_reply=True)
                    info["controller_version_reply"] = vres.get("reply")
            except Exception:
                info["serial_status"] = {"connected": False, "message": "diagnostic query failed"}
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
            if requested_cid:
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
