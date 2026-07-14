from flask import Blueprint, jsonify, request
import os, re, time
from services import serial as serial_svc

bp = Blueprint("serial", __name__)
_CID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_UNAVAILABLE_ERROR = "controller unavailable or identity not verified"


def _public_status(status: dict) -> dict:
    public = dict(status)
    public["message"] = "connected" if public.get("connected") else _UNAVAILABLE_ERROR
    return public


@bp.get("/status")
def serial_status():
    cid = request.args.get("cid")
    if not isinstance(cid, str) or not _CID_RE.fullmatch(cid):
        return jsonify({"ok": False, "error": "valid cid parameter required"}), 400
    return jsonify(_public_status(serial_svc.get_serial_status(cid, full=True)))


@bp.get("/tools")
def api_serial_tools():
    cid = request.args.get("cid")
    if not isinstance(cid, str) or not _CID_RE.fullmatch(cid):
        return jsonify({"ok": False, "error": "valid cid parameter required"}), 400
    status = serial_svc.get_serial_status(cid, full=True)
    checks = {"ping": {"ok": False, "ms": None, "reply": None, "error": None}}
    if status.get("connected"):
        t0 = time.time()
        res = serial_svc.serial_send_line(cid, "PING", expect_reply=True, timeout=0.5)
        dt = int((time.time() - t0) * 1000)
        if res.get("ok"):
            checks["ping"] = {
                "ok": (res.get("reply") == "PONG"),
                "ms": dt,
                "reply": res.get("reply"),
                "error": None,
            }
        else:
            checks["ping"] = {
                "ok": False,
                "ms": dt,
                "reply": res.get("reply"),
                "error": "serial diagnostic command failed",
            }
    else:
        checks["ping"] = {"ok": False, "ms": None, "reply": None, "error": "not connected"}
    return jsonify({"status": _public_status(status), "checks": checks})


@bp.post("/send")
def api_serial_send():
    if os.environ.get("FANBRIDGE_MAINTENANCE_MODE", "0") != "1":
        return jsonify({"ok": False, "error": "raw serial commands require maintenance mode"}), 403
    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "JSON object required"}), 400
    cid = data.get("cid")
    if not isinstance(cid, str) or not _CID_RE.fullmatch(cid):
        return jsonify({"ok": False, "error": "valid cid is required"}), 400
    raw_line = data.get("line")
    if not isinstance(raw_line, str):
        return jsonify({"ok": False, "error": "serial command must be a string"}), 400
    line = raw_line.strip()
    if not line:
        return jsonify({"ok": False, "error": "empty line"}), 400
    if len(line) > 64 or any(ord(ch) < 32 or ord(ch) > 126 for ch in line):
        return jsonify({"ok": False, "error": "serial command must be at most 64 printable ASCII characters"}), 400
    command = line.upper()
    read_only_commands = {
        "ID", "ID?", "VERSION", "VERSION?", "PING",
        "RPM", "RPM?", "UPTIME", "UPTIME?", "STATUS", "STATUS?",
    }
    if command not in read_only_commands:
        return jsonify({
            "ok": False,
            "error": "the raw console permits read-only diagnostics only",
        }), 403
    status = serial_svc.get_serial_status(str(cid), full=False)
    if not status.get("connected"):
        return jsonify({
            "ok": False,
            "error": _UNAVAILABLE_ERROR,
        }), 409
    res = serial_svc.serial_send_line(cid, line, expect_reply=True)
    if not res.get("ok"):
        return jsonify({
            "ok": False,
            "error": "serial diagnostic command failed",
        }), 502
    return jsonify(res)


@bp.post("/pwm")
def api_serial_pwm():
    if os.environ.get("FANBRIDGE_MAINTENANCE_MODE", "0") != "1":
        return jsonify({"ok": False, "error": "manual PWM requires maintenance mode"}), 403
    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "JSON object required"}), 400
    cid = data.get("cid")
    if not isinstance(cid, str) or not _CID_RE.fullmatch(cid):
        return jsonify({"ok": False, "error": "valid cid is required"}), 400
    res = serial_svc.serial_set_pwm_percent(cid, data.get("value"))
    code = 200 if res.get("ok") else 400
    if not res.get("ok"):
        return jsonify({
            "ok": False,
            "error": "manual PWM command failed",
        }), code
    return jsonify(res), code
