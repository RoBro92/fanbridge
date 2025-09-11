from flask import Blueprint, jsonify, make_response, current_app
import os, time
import core.metrics as _metrics
from core.appver import latest_github_release, parse_semver_tuple

bp = Blueprint("appinfo", __name__)
_CACHE = { 'ts': 0.0, 'latest': None }


@bp.get("/app/version")
def api_app_version():
    repo = os.environ.get("FANBRIDGE_REPO", "RoBro92/fanbridge")
    now = time.time()
    latest = None
    try:
        if _CACHE and (now - float(_CACHE.get('ts', 0))) < 300:
            latest = _CACHE.get('latest')
        else:
            latest = latest_github_release(repo)
            _CACHE['ts'] = now
            _CACHE['latest'] = latest
    except Exception:
        latest = None
    current = (current_app.config.get('FB_APP_INFO') or {}).get('APP_VERSION')
    update = False
    try:
        if current and latest:
            update = parse_semver_tuple(str(latest)) > parse_semver_tuple(str(current))
    except Exception:
        update = False
    return jsonify({
        'ok': True,
        'current': current,
        'latest': latest,
        'repo': repo,
        'update_available': bool(update),
    })


@bp.get("/metrics")
def metrics():
    # Prometheus text exposition format
    lines: list[str] = []
    lines.append("# HELP fanbridge_http_requests_total HTTP requests by method and code")
    lines.append("# TYPE fanbridge_http_requests_total counter")
    try:
        items = list(_metrics.HTTP.items())
    except Exception:
        items = []
    for (method, code), val in items:
        lines.append(f"fanbridge_http_requests_total{{method=\"{method}\",code=\"{code}\"}} {int(val)}")

    lines.append("# HELP fanbridge_serial_commands_total Serial commands by kind and status")
    lines.append("# TYPE fanbridge_serial_commands_total counter")
    try:
        sc = list(_metrics.SERIAL_CMD.items())
    except Exception:
        sc = []
    for (kind, status), val in sc:
        lines.append(f"fanbridge_serial_commands_total{{kind=\"{kind}\",status=\"{status}\"}} {int(val)}")

    lines.append("# HELP fanbridge_serial_open_failures_total Serial open failures")
    lines.append("# TYPE fanbridge_serial_open_failures_total counter")
    try:
        lines.append(f"fanbridge_serial_open_failures_total {int(_metrics.SERIAL_OPEN_FAIL)}")
    except Exception:
        lines.append("fanbridge_serial_open_failures_total 0")

    resp = make_response("\n".join(lines) + "\n")
    resp.headers["Content-Type"] = "text/plain; version=0.0.4; charset=utf-8"
    return resp

