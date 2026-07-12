import os
import time
import logging

from services.disks import read_unraid_disks, is_bind_mounted_file
from services import serial as serial_svc

_AUTO_LAST_DUTY: int | None = None
_AUTO_LAST_TS: float | None = None
_AUTO_PAUSED_MSG: str | None = None

def map_temp_to_pwm(temp: int, thresholds: list[int], pwms: list[int]) -> int:
    step = 0
    for i, th in enumerate(thresholds):
        if temp >= th:
            step = i
        else:
            break
    return int(pwms[step])

def compute_status(app_context: dict) -> dict:
    global _AUTO_LAST_DUTY, _AUTO_LAST_TS

    # Unpack context
    cfg = app_context['cfg']
    disks_ini = app_context['disks_ini']
    in_docker = app_context['in_docker']
    app_version = app_context['app_version']
    disks_stale_warn_sec = app_context['disks_stale_warn_sec']
    dbg_should = app_context['dbg_should']
    log = logging.getLogger("fanbridge")
    warn_once = app_context['warn_once']

    if os.path.exists(disks_ini):
        mode = "unraid"
        try:
            excludes = set(cfg.get("exclude_devices") or [])
        except Exception:
            excludes = set()
        drives = read_unraid_disks(disks_ini, excludes)
    else:
        if in_docker():
            mode = "unraid-missing"
            drives = []
        else:
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

    for d in drives:
        if d["state"] == "N/A":
            log.warning("disks.ini has no temp | dev=%s type=%s", d['dev'], d['type'])

    hdd_vals = [d["temp"] for d in drives if d.get("type") == "HDD" and not d.get("excluded") and d.get("temp") is not None]
    ssd_vals = [d["temp"] for d in drives if d.get("type") == "SSD" and not d.get("excluded") and d.get("temp") is not None]

    def stats(vals):
        if not vals:
            return {"avg": 0, "min": 0, "max": 0, "count": 0}
        return {"avg": int(sum(vals)/len(vals)), "min": min(vals), "max": max(vals), "count": len(vals)}

    hdd = stats(hdd_vals)
    ssd = stats(ssd_vals)

    override = False
    if hdd_vals and max(hdd_vals) >= int(cfg.get("single_override_hdd_c", 45)): override = True
    if ssd_vals and max(ssd_vals) >= int(cfg.get("single_override_ssd_c", 60)): override = True

    if override:
        recommended_pwm = int(cfg.get("override_pwm", 100))
    else:
        pwm_hdd = map_temp_to_pwm(hdd["avg"], cfg.get("hdd_thresholds", []), cfg.get("hdd_pwm", [])) if hdd["count"] else 0
        pwm_ssd = map_temp_to_pwm(ssd["avg"], cfg.get("ssd_thresholds", []), cfg.get("ssd_pwm", [])) if ssd["count"] else 0
        recommended_pwm = max(pwm_hdd, pwm_ssd)
        if hdd["count"] == 0 and ssd["count"] == 0:
            recommended_pwm = int(cfg.get("fallback_pwm", 10))

    disks_mtime = None
    try:
        if os.path.exists(disks_ini):
            disks_mtime = int(os.path.getmtime(disks_ini))
        else:
            warn_once(
                "disks_ini_missing",
                f"Could not read {disks_ini}; Unraid mapping missing. Map /var/local/emhttp -> /unraid:ro or bind /var/local/emhttp/disks.ini -> /unraid/disks.ini:ro",
            )
    except Exception as e:
        try:
            log.warning("Failed to stat %s: %s", disks_ini, e)
        except Exception:
            pass

    auto_enabled = bool(cfg.get("auto_apply"))
    auto_last_duty = _AUTO_LAST_DUTY
    auto_last_ts = _AUTO_LAST_TS
    auto_paused_msg = None
    if auto_enabled:
        try:
            sstat = serial_svc.get_serial_status(full=False)
            if not sstat.get("connected"):
                auto_paused_msg = "controller not connected"
                # Backoff logging for disconnected state
                if dbg_should("auto_apply_disconnected", 60):
                    log.warning("auto-apply paused: controller not connected")
            else:
                pct = int(recommended_pwm)
                if pct < 0: pct = 0
                if pct > 100: pct = 100
                duty = int(round(pct * 255 / 100))
                min_ivl = int(cfg.get("auto_apply_min_interval_s", 3) or 3)
                hyst = int(cfg.get("auto_apply_hysteresis_duty", 5) or 5)
                now_ts = time.time()
                delta_ok = (_AUTO_LAST_DUTY is None) or (abs(duty - int(_AUTO_LAST_DUTY)) >= max(0, hyst))
                ivl_ok = (_AUTO_LAST_TS is None) or ((now_ts - float(_AUTO_LAST_TS)) >= max(1, min_ivl))
                if delta_ok and ivl_ok:
                    res = serial_svc.serial_set_pwm_percent(pct)
                    if res.get("ok"):
                        auto_last_duty = duty
                        auto_last_ts = now_ts
                        _AUTO_LAST_DUTY = duty
                        _AUTO_LAST_TS = now_ts
                    else:
                        auto_paused_msg = str(res.get("error") or "send failed")
                        if dbg_should("auto_apply_send_failed", 30):
                            log.warning("auto-apply send failed: %s", auto_paused_msg)
        except Exception as e:
            if dbg_should("auto_apply_error", 30):
                log.warning("auto-apply error: %s", e)
            auto_paused_msg = "auto-apply error"

    payload = {
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
        "version": app_version,
        "disks_ini_mtime": disks_mtime,
        "disks_stale_warn_s": int(disks_stale_warn_sec),
        "auto_apply": auto_enabled,
        "auto_last_duty": int(auto_last_duty) if auto_last_duty is not None else None,
        "auto_last_ts": int(auto_last_ts) if auto_last_ts is not None else None,
        "auto_paused": bool(auto_paused_msg),
        "auto_message": auto_paused_msg,
        "auto_apply_min_interval_s": int(cfg.get("auto_apply_min_interval_s", 3) or 3),
        "auto_apply_hysteresis_duty": int(cfg.get("auto_apply_hysteresis_duty", 5) or 5),
    }
    try:
        if dbg_should("status", 10):
            log.debug(
                "status | mode=%s hdd_avg=%s ssd_avg=%s pwm=%s drives=%s",
                mode, hdd.get("avg"), ssd.get("avg"), payload["recommended_pwm"], len(drives)
            )
        try:
            if dbg_should("disks_ini_bind_advice", 9999999) and is_bind_mounted_file(disks_ini):
                log.warning("/unraid/disks.ini is bind-mounted as a single file; map the directory /var/local/emhttp -> /unraid:ro to see instant updates")
        except Exception:
            pass
        if disks_mtime:
            try:
                if (time.time() - float(disks_mtime)) > max(60, int(disks_stale_warn_sec)) and dbg_should("disks_ini_stale_warn", 600):
                    age = int(time.time() - float(disks_mtime))
                    log.warning("/unraid/disks.ini appears stale | age_s=%s", age)
            except Exception:
                pass
    except Exception:
        pass
    try:
        from services.history import record_status
        record_status(hdd.get("avg", 0), ssd.get("avg", 0), payload["recommended_pwm"])
    except Exception as e:
        log.warning("failed to record history: %s", e)
    return payload
