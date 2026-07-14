from flask import Blueprint, current_app, jsonify, request
import os, re, time
from services import serial as serial_svc

bp = Blueprint("serial", __name__)
_CID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_UNAVAILABLE_ERROR = "controller unavailable or identity not verified"


def _public_status(status: dict) -> dict:
    public = dict(status)
    identity = public.get("identity") if isinstance(public.get("identity"), dict) else {}
    if public.get("connected"):
        public["message"] = "connected"
        public["code"] = "connected"
    elif identity.get("legacy"):
        public["message"] = "DIY firmware update required (2.4.0 or newer)"
        public["code"] = "firmware_update_required"
    elif not public.get("available"):
        public["message"] = "no mapped serial device is available"
        public["code"] = "device_unavailable"
    else:
        public["message"] = _UNAVAILABLE_ERROR
        public["code"] = "identity_unverified"
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
        serial_svc.record_operator_transaction(
            cid,
            line,
            {"ok": False, "error": "controller unavailable; command not sent"},
        )
        return jsonify({
            "ok": False,
            "error": _UNAVAILABLE_ERROR,
        }), 409
    res = serial_svc.serial_send_line(cid, line, expect_reply=True)
    serial_svc.record_operator_transaction(cid, line, res)
    if not res.get("ok"):
        return jsonify({
            "ok": False,
            "error": "serial diagnostic command failed",
        }), 502
    return jsonify(res)


@bp.post("/test")
def api_serial_test():
    if os.environ.get("FANBRIDGE_MAINTENANCE_MODE", "0") != "1":
        return jsonify({"ok": False, "error": "fan test requires maintenance mode"}), 403
    data = request.get_json(force=True, silent=True) or {}
    cid = data.get("cid") if isinstance(data, dict) else None
    if not isinstance(cid, str) or not _CID_RE.fullmatch(cid):
        return jsonify({"ok": False, "error": "valid cid is required"}), 400
    status = serial_svc.get_serial_status(cid, full=False)
    if not status.get("connected"):
        serial_svc.record_operator_transaction(
            cid,
            "TEST",
            {"ok": False, "error": "controller unavailable; command not sent"},
        )
        return jsonify({"ok": False, "error": _UNAVAILABLE_ERROR}), 409
    res = serial_svc.serial_send_line(cid, "TEST", expect_reply=True, timeout=1.0)
    serial_svc.record_operator_transaction(cid, "TEST", res)
    if not res.get("ok"):
        return jsonify({"ok": False, "error": "controller fan test failed"}), 502
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
    # A manual command is also a control-mode transition. Keeping this atomic
    # prevents the automatic worker from overwriting the new setpoint on its
    # next temperature/curve cycle.
    set_manual_pwm = current_app.config.get("FB_SET_MANUAL_PWM")
    if not callable(set_manual_pwm):
        return jsonify({"ok": False, "error": "manual PWM service is unavailable"}), 503
    res = set_manual_pwm(cid, data.get("value"))
    code = 200 if res.get("ok") else 400
    if not res.get("ok"):
        return jsonify({
            "ok": False,
            "error": "manual PWM command failed",
        }), code
    return jsonify(res), code
