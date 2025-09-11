from flask import Blueprint, jsonify, request
import time
from services import serial as serial_svc

bp = Blueprint("serial", __name__)


@bp.get("/status")
def serial_status():
    return jsonify(serial_svc.get_serial_status(full=True))


@bp.get("/tools")
def api_serial_tools():
    status = serial_svc.get_serial_status(full=True)
    checks = {"ping": {"ok": False, "ms": None, "reply": None, "error": None}}
    if status.get("connected"):
        t0 = time.time()
        res = serial_svc.serial_send_line("PING", expect_reply=True, timeout=0.5)
        dt = int((time.time() - t0) * 1000)
        if res.get("ok"):
            checks["ping"] = {
                "ok": (res.get("reply") == "PONG"),
                "ms": dt,
                "reply": res.get("reply"),
                "error": None,
            }
        else:
            checks["ping"] = {"ok": False, "ms": dt, "reply": res.get("reply"), "error": res.get("error")}
    else:
        checks["ping"] = {"ok": False, "ms": None, "reply": None, "error": "not connected"}
    return jsonify({"status": status, "checks": checks})


@bp.post("/send")
def api_serial_send():
    data = request.get_json(force=True, silent=True) or {}
    line = (data.get("line") or "").strip()
    if not line:
        return jsonify({"ok": False, "error": "empty line"}), 400
    res = serial_svc.serial_send_line(line, expect_reply=True)
    if not res.get("ok"):
        return jsonify(res), 502
    return jsonify(res)


@bp.post("/pwm")
def api_serial_pwm():
    data = request.get_json(force=True, silent=True) or {}
    res = serial_svc.serial_set_pwm_percent(data.get("value"))
    code = 200 if res.get("ok") else 400
    return jsonify(res), code

