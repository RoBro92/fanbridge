import logging
import os
import time
from typing import Any

from services.disks import (
    is_bind_mounted_file,
    read_unraid_disks_with_status,
)
from services import serial as serial_svc


# Process-local delivery state.  Percent is deliberately stored in the same
# unit as the wire protocol; the old implementation mixed percent and 0..255
# duty units.  A periodic refresh makes this cache an optimisation, not a
# safety dependency.
_AUTO_LAST_PERCENT: dict[str, int] = {}
_AUTO_LAST_TS: dict[str, float] = {}
_AUTO_CONNECTED: dict[str, bool] = {}
_AUTO_SAFE_STOP_SENT: set[str] = set()


def reset_auto_state() -> None:
    """Reset delivery state (used at service lifecycle boundaries and tests)."""
    _AUTO_LAST_PERCENT.clear()
    _AUTO_LAST_TS.clear()
    _AUTO_CONNECTED.clear()
    _AUTO_SAFE_STOP_SENT.clear()


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _curve_pairs(thresholds: Any, pwms: Any, *, strict: bool) -> list[tuple[int, int]] | None:
    if not isinstance(thresholds, (list, tuple)) or not isinstance(pwms, (list, tuple)):
        return None
    if not thresholds or len(thresholds) != len(pwms) or len(thresholds) > 32:
        return None
    pairs: list[tuple[int, int]] = []
    try:
        for threshold, pwm in zip(thresholds, pwms):
            t = int(threshold)
            p = int(pwm)
            if not 0 <= t <= 120 or not 0 <= p <= 100:
                return None
            pairs.append((t, p))
    except (TypeError, ValueError):
        return None
    if strict:
        if any(pairs[index][0] >= pairs[index + 1][0] for index in range(len(pairs) - 1)):
            return None
    else:
        pairs.sort(key=lambda pair: pair[0])
        if any(pairs[index][0] == pairs[index + 1][0] for index in range(len(pairs) - 1)):
            return None
    if any(pairs[index][1] > pairs[index + 1][1] for index in range(len(pairs) - 1)):
        return None
    return pairs


def map_temp_to_pwm(temp: int, thresholds: list[int], pwms: list[int], default: int = 100) -> int:
    """Map a temperature defensively; malformed curves return a safe default."""
    pairs = _curve_pairs(thresholds, pwms, strict=False)
    if not pairs:
        return _clamp(_int_value(default, 100), 0, 100)
    try:
        current_temp = int(temp)
    except (TypeError, ValueError):
        return _clamp(_int_value(default, 100), 0, 100)
    selected = pairs[0][1]
    for threshold, pwm in pairs:
        if current_temp >= threshold:
            selected = pwm
        else:
            break
    return _clamp(selected, 0, 100)


def _stats(values: list[int]) -> dict:
    if not values:
        return {"avg": 0, "min": 0, "max": 0, "count": 0}
    return {
        "avg": int(sum(values) / len(values)),
        "min": min(values),
        "max": max(values),
        "count": len(values),
    }


def _simulation_drives(cfg: dict) -> list[dict]:
    drives: list[dict] = []
    sim = cfg.get("sim") if isinstance(cfg.get("sim"), dict) else {}
    for item in (sim.get("drives") or []):
        if not isinstance(item, dict):
            continue
        dev = str(item.get("name") or "").strip()
        if not dev:
            continue
        dtype = "SSD" if str(item.get("type", "HDD")).upper() == "SSD" else "HDD"
        raw_temp = item.get("temp")
        temp = None
        if raw_temp is not None:
            try:
                candidate = int(raw_temp)
                if 1 <= candidate <= 120:
                    temp = candidate
            except (TypeError, ValueError):
                pass
        spun_down = bool(item.get("spun_down", temp is None))
        if spun_down:
            temp = None
            state = "down" if dtype == "HDD" else "spun down"
            temp_status = "spun_down"
        elif temp is None:
            state = "N/A"
            temp_status = "missing_active"
        else:
            state = "up" if dtype == "HDD" else "on"
            temp_status = "ok"
        drives.append({
            "dev": dev,
            "slot": str(item.get("slot") or dev),
            "id": str(item.get("id") or ""),
            "section": str(item.get("section") or dev),
            "type": dtype,
            "temp": temp,
            "state": state,
            "spun_down": spun_down,
            "temp_status": temp_status,
            "excluded": False,
        })
    return drives


def _assignment_for(drive: dict, assignments: dict, controller_ids: set[str]) -> str:
    value: Any = None
    # Accept current kernel names plus stable identifiers so config migrations
    # can move away from sdX without breaking existing users.
    for key in (drive.get("id"), drive.get("slot"), drive.get("section"), drive.get("dev")):
        if key and key in assignments:
            value = assignments[key]
            break
    if value is None:
        return "none"
    selected = str(value).strip()
    if selected == "none" or selected in controller_ids:
        return selected
    # Legacy global values and unknown/deleted controller IDs are unassigned.
    return "none"


def _policy_for_drives(
    drives: list[dict],
    cfg: dict,
    source_fault: str | None,
    *,
    source_required: bool,
) -> dict:
    included = [drive for drive in drives if not drive.get("excluded")]
    active_missing = [
        drive for drive in included
        if drive.get("temp_status") == "missing_active"
        or (not drive.get("spun_down") and drive.get("temp") is None)
    ]
    hdd_values = [
        int(drive["temp"]) for drive in included
        if drive.get("type") == "HDD" and drive.get("temp") is not None
    ]
    ssd_values = [
        int(drive["temp"]) for drive in included
        if drive.get("type") == "SSD" and drive.get("temp") is not None
    ]
    hdd = _stats(hdd_values)
    ssd = _stats(ssd_values)

    # A temperature-source or sensor fault always commands maximum cooling.
    # Configurable derating here defeats the independent firmware fail-safe by
    # continually renewing its lease at the lower value.
    failsafe_pwm = 100
    fallback_pwm = _clamp(_int_value(cfg.get("fallback_pwm", 10), 10), 0, 100)
    faults: list[str] = []
    if source_required and source_fault:
        faults.append(source_fault)
    if active_missing:
        faults.append("active_drive_temperature_missing")

    hdd_curve_valid = _curve_pairs(cfg.get("hdd_thresholds"), cfg.get("hdd_pwm"), strict=True) is not None
    ssd_curve_valid = _curve_pairs(cfg.get("ssd_thresholds"), cfg.get("ssd_pwm"), strict=True) is not None
    if hdd_values and not hdd_curve_valid:
        faults.append("invalid_hdd_curve")
    if ssd_values and not ssd_curve_valid:
        faults.append("invalid_ssd_curve")

    override = False
    if hdd_values and max(hdd_values) >= _int_value(cfg.get("single_override_hdd_c", 45), 45):
        override = True
    if ssd_values and max(ssd_values) >= _int_value(cfg.get("single_override_ssd_c", 60), 60):
        override = True

    if faults:
        recommended_pwm = failsafe_pwm
        reason = "failsafe:" + ",".join(faults)
        safety_state = "failsafe"
    elif override:
        recommended_pwm = 100
        reason = "single_drive_override"
        safety_state = "normal"
    elif hdd_values or ssd_values:
        # Cooling policy follows the hottest assigned disk; averages remain in
        # the payload for display/history only.
        hdd_pwm = map_temp_to_pwm(
            max(hdd_values), cfg.get("hdd_thresholds", []), cfg.get("hdd_pwm", []), failsafe_pwm
        ) if hdd_values else 0
        ssd_pwm = map_temp_to_pwm(
            max(ssd_values), cfg.get("ssd_thresholds", []), cfg.get("ssd_pwm", []), failsafe_pwm
        ) if ssd_values else 0
        recommended_pwm = max(hdd_pwm, ssd_pwm)
        reason = "temperature_curve"
        safety_state = "normal"
    else:
        # No assigned disks, all assigned disks excluded, or all assigned disks
        # legitimately asleep: a configurable idle fallback is appropriate.
        recommended_pwm = fallback_pwm
        reason = "idle_or_unassigned"
        safety_state = "idle"

    return {
        "hdd": hdd,
        "ssd": ssd,
        "recommended_pwm": int(recommended_pwm),
        "override": override,
        "safety_state": safety_state,
        "control_reason": reason,
        "faults": faults,
        "active_missing_temp_devices": [str(drive.get("dev") or "") for drive in active_missing],
    }


def _canonical_auto_settings(cfg: dict) -> tuple[int, int, int]:
    min_interval = _clamp(_int_value(
        cfg.get("auto_apply_min_interval_seconds", cfg.get("auto_apply_min_interval_s", 3)), 3
    ), 1, 60)
    refresh_interval = _clamp(_int_value(
        cfg.get("auto_apply_refresh_interval_seconds", cfg.get("auto_apply_refresh_interval_s", 20)), 20
    ), 1, 30)
    if "auto_apply_hysteresis_percent" in cfg:
        hysteresis_percent = _clamp(_int_value(cfg.get("auto_apply_hysteresis_percent"), 2), 0, 100)
    else:
        legacy_duty = _clamp(_int_value(cfg.get("auto_apply_hysteresis_duty", 5), 5), 0, 255)
        hysteresis_percent = _clamp(int(round(legacy_duty * 100 / 255)), 0, 100)
    return min_interval, refresh_interval, hysteresis_percent


def _apply_auto(
    cid: str,
    percent: int,
    enabled: bool,
    min_interval: int,
    refresh_interval: int,
    hysteresis_percent: int,
    now_ts: float,
    dbg_should,
    log: logging.Logger,
) -> dict:
    last_percent = _AUTO_LAST_PERCENT.get(cid)
    last_ts = _AUTO_LAST_TS.get(cid)
    message = None

    if not enabled:
        # On startup or an enabled->disabled transition, release automatic
        # control into the known-safe 100% state immediately instead of
        # leaving the previous lease active for up to 60 seconds. Retry on a
        # later cycle if the device is currently absent.
        if cid not in _AUTO_SAFE_STOP_SENT:
            try:
                serial_status = serial_svc.get_serial_status(cid, full=False)
                if serial_status.get("connected"):
                    result = serial_svc.serial_set_pwm_percent(cid, 100)
                    if result.get("ok"):
                        _AUTO_SAFE_STOP_SENT.add(cid)
                        _AUTO_LAST_PERCENT[cid] = 100
                        _AUTO_LAST_TS[cid] = now_ts
                        last_percent = 100
                        last_ts = now_ts
                    elif dbg_should("auto_safe_stop_failed_" + cid, 30):
                        log.warning(
                            "automatic output disabled but safe-stop failed for %s: %s",
                            cid,
                            result.get("error") or "send failed",
                        )
            except Exception as exc:
                if dbg_should("auto_safe_stop_error_" + cid, 30):
                    log.warning("automatic output disabled; safe-stop pending for %s: %s", cid, exc)
        # Enabling auto again must always transmit a fresh control lease.
        _AUTO_CONNECTED[cid] = False
    else:
        _AUTO_SAFE_STOP_SENT.discard(cid)
        try:
            serial_status = serial_svc.get_serial_status(cid, full=False)
            connected = bool(serial_status.get("connected"))
            was_connected = bool(_AUTO_CONNECTED.get(cid, False))
            _AUTO_CONNECTED[cid] = connected
            if not connected:
                message = "controller not connected"
                if dbg_should("auto_apply_disconnected_" + cid, 60):
                    log.warning("auto-apply paused for %s: not connected", cid)
            else:
                elapsed = None if last_ts is None else max(0.0, now_ts - float(last_ts))
                changed = (
                    last_percent is None
                    or (
                        int(percent) != int(last_percent)
                        and abs(int(percent) - int(last_percent)) >= hysteresis_percent
                    )
                )
                change_due = changed and (elapsed is None or elapsed >= min_interval)
                refresh_due = elapsed is None or elapsed >= refresh_interval
                reconnected = not was_connected
                if reconnected or change_due or refresh_due:
                    result = serial_svc.serial_set_pwm_percent(cid, percent)
                    if result.get("ok"):
                        last_percent = int(percent)
                        last_ts = now_ts
                        _AUTO_LAST_PERCENT[cid] = int(percent)
                        _AUTO_LAST_TS[cid] = now_ts
                    else:
                        message = str(result.get("error") or "send failed")
                        if dbg_should("auto_apply_send_failed_" + cid, 30):
                            log.warning("auto-apply send failed for %s: %s", cid, message)
        except Exception as exc:
            _AUTO_CONNECTED[cid] = False
            message = "auto-apply error"
            if dbg_should("auto_apply_error_" + cid, 30):
                log.warning("auto-apply error for %s: %s", cid, exc)

    return {
        "auto_last_percent": int(last_percent) if last_percent is not None else None,
        # Backward-compatible display field only; internal state remains percent.
        "auto_last_duty": int(round(last_percent * 255 / 100)) if last_percent is not None else None,
        "auto_last_ts": int(last_ts) if last_ts is not None else None,
        "auto_paused": bool(message),
        "auto_message": message,
    }


def compute_status(app_context: dict) -> dict:
    cfg = app_context["cfg"] if isinstance(app_context.get("cfg"), dict) else {}
    disks_ini = app_context["disks_ini"]
    app_version = app_context["app_version"]
    disks_stale_warn_sec = _int_value(app_context["disks_stale_warn_sec"], 1800)
    allow_simulation = app_context.get("allow_simulation") is True
    dbg_should = app_context["dbg_should"]
    warn_once = app_context["warn_once"]
    log = logging.getLogger("fanbridge")
    now_ts = time.time()
    stale_after_seconds = max(60, disks_stale_warn_sec)
    raw_excludes = cfg.get("exclude_devices")
    if isinstance(raw_excludes, (list, tuple, set)):
        excludes = {
            str(value).strip()
            for value in raw_excludes
            if str(value).strip()
        }
    else:
        excludes = set()

    disks_mtime = None
    source_status: dict = {"ok": True, "error": None, "invalid_devices": []}
    if os.path.exists(disks_ini):
        mode = "unraid"
        drives, source_status = read_unraid_disks_with_status(disks_ini, excludes)
        try:
            disks_mtime = int(os.path.getmtime(disks_ini))
        except Exception as exc:
            source_status = {**source_status, "ok": False, "error": "stat_failed"}
            log.warning("Failed to stat %s: %s", disks_ini, exc)
    elif allow_simulation:
        mode = "sim"
        drives = _simulation_drives(cfg)
        if not drives:
            source_status = {"ok": False, "error": "simulation_empty", "invalid_devices": []}
    else:
        mode = "unraid-missing"
        drives = []
        source_status = {"ok": False, "error": "missing", "invalid_devices": []}
        warn_once(
            "disks_ini_missing",
            f"Could not read {disks_ini}; Unraid mapping missing. Map /var/local/emhttp -> /unraid:ro",
        )

    stale = False
    source_fault = None if source_status.get("ok") else "temperature_source_" + str(source_status.get("error") or "invalid")
    if mode == "unraid" and disks_mtime is not None:
        age = max(0, int(now_ts - float(disks_mtime)))
        stale = age > stale_after_seconds
        if stale:
            source_fault = "temperature_source_stale"
            if dbg_should("disks_ini_stale_warn", 600):
                log.warning("%s appears stale | age_s=%s", disks_ini, age)

    for drive in drives:
        if drive.get("temp_status") == "missing_active" and dbg_should(
            "missing_active_temp_" + str(drive.get("dev") or "unknown"), 60
        ):
            log.warning("active drive has no temperature | dev=%s type=%s", drive.get("dev"), drive.get("type"))

    controllers_cfg = cfg.get("controllers") if isinstance(cfg.get("controllers"), list) else []
    controller_ids = {
        str(controller.get("id")) for controller in controllers_cfg
        if isinstance(controller, dict) and controller.get("id")
    }
    raw_assignments = cfg.get("drive_assignments")
    assignments = {}
    if isinstance(raw_assignments, dict):
        for raw_key, raw_value in raw_assignments.items():
            key = str(raw_key).strip()
            if key:
                assignments[key] = str(raw_value).strip()
    annotated_drives: list[dict] = []
    for drive in drives:
        item = dict(drive)
        item["assignment"] = _assignment_for(item, assignments, controller_ids)
        annotated_drives.append(item)

    # The aggregate server health view remains based on every reported drive;
    # drive assignments only route temperature sources to controller policies.
    top_drives = annotated_drives
    top_source_required = True
    aggregate = _policy_for_drives(
        top_drives, cfg, source_fault, source_required=top_source_required
    )

    configured_auto_enabled = cfg.get("auto_apply") is True
    # Simulation exists for UI/control-policy development only. It must never
    # renew a real controller lease at a synthetic temperature-derived value.
    auto_enabled = configured_auto_enabled and mode != "sim"
    auto_blocked_reason = "simulation_source" if configured_auto_enabled and mode == "sim" else None
    min_interval, refresh_interval, hysteresis_percent = _canonical_auto_settings(cfg)
    controller_states: list[dict] = []
    for controller in controllers_cfg:
        if not isinstance(controller, dict):
            continue
        cid = str(controller.get("id") or "").strip()
        if not cid:
            continue
        assigned = [
            drive for drive in annotated_drives
            if drive.get("assignment") == cid
        ]
        source_required = any(str(value) == cid for value in assignments.values())
        policy = _policy_for_drives(
            assigned, cfg, source_fault, source_required=source_required
        )
        delivery = _apply_auto(
            cid,
            policy["recommended_pwm"],
            auto_enabled,
            min_interval,
            refresh_interval,
            hysteresis_percent,
            now_ts,
            dbg_should,
            log,
        )
        controller_states.append({
            "id": cid,
            "type": controller.get("type", "unknown"),
            "name": controller.get("name", cid),
            "port": controller.get("port", ""),
            "hardware_uid": controller.get("hardware_uid"),
            "persistent_identity": bool(controller.get("hardware_uid")),
            "drives": assigned,
            **policy,
            **delivery,
        })

    source_age = None
    if disks_mtime is not None:
        source_age = max(0, int(now_ts - float(disks_mtime)))
    source_payload = {
        **source_status,
        "path": disks_ini,
        "mtime": disks_mtime,
        "age_seconds": source_age,
        "stale": stale,
        "stale_after_seconds": stale_after_seconds,
        "fault": source_fault,
    }
    hdd_thresholds = cfg.get("hdd_thresholds", [])
    if not isinstance(hdd_thresholds, (list, tuple)):
        hdd_thresholds = []
    hdd_pwm = cfg.get("hdd_pwm", [])
    if not isinstance(hdd_pwm, (list, tuple)):
        hdd_pwm = []
    ssd_thresholds = cfg.get("ssd_thresholds", [])
    if not isinstance(ssd_thresholds, (list, tuple)):
        ssd_thresholds = []
    ssd_pwm = cfg.get("ssd_pwm", [])
    if not isinstance(ssd_pwm, (list, tuple)):
        ssd_pwm = []
    payload = {
        "drives": annotated_drives,
        "hdd": aggregate["hdd"],
        "ssd": aggregate["ssd"],
        "override_hdd_c": _int_value(cfg.get("single_override_hdd_c", 45), 45),
        "override_ssd_c": _int_value(cfg.get("single_override_ssd_c", 60), 60),
        "exclude_devices": sorted(excludes),
        "drive_assignments": dict(assignments),
        "hdd_thresholds": hdd_thresholds,
        "hdd_pwm": hdd_pwm,
        "ssd_thresholds": ssd_thresholds,
        "ssd_pwm": ssd_pwm,
        "curves": {
            "hdd": list(zip(hdd_thresholds, hdd_pwm)),
            "ssd": list(zip(ssd_thresholds, ssd_pwm)),
        },
        "recommended_pwm": aggregate["recommended_pwm"],
        "override": aggregate["override"],
        "safety_state": aggregate["safety_state"],
        "control_reason": aggregate["control_reason"],
        "faults": aggregate["faults"],
        "active_missing_temp_devices": aggregate["active_missing_temp_devices"],
        "failsafe_pwm": 100,
        "mode": mode,
        "version": app_version,
        "disks_ini_mtime": disks_mtime,
        "disks_stale_warn_s": int(disks_stale_warn_sec),
        "temperature_source": source_payload,
        "auto_apply": auto_enabled,
        "auto_apply_configured": configured_auto_enabled,
        "auto_apply_blocked_reason": auto_blocked_reason,
        "controllers": controller_states,
        "auto_apply_min_interval_seconds": min_interval,
        "auto_apply_refresh_interval_seconds": refresh_interval,
        "auto_apply_hysteresis_percent": hysteresis_percent,
        # Legacy aliases retained while persisted configs/UI migrate.
        "auto_apply_min_interval_s": min_interval,
        "auto_apply_hysteresis_duty": int(round(hysteresis_percent * 255 / 100)),
        "config": {
            "failsafe_pwm": 100,
            "drive_assignments": dict(assignments),
            "auto_apply_min_interval_seconds": min_interval,
            "auto_apply_refresh_interval_seconds": refresh_interval,
            "auto_apply_hysteresis_percent": hysteresis_percent,
        },
    }

    try:
        if dbg_should("status", 10):
            log.debug(
                "status | mode=%s hdd_max=%s ssd_max=%s pwm=%s safety=%s drives=%s",
                mode,
                aggregate["hdd"].get("max"),
                aggregate["ssd"].get("max"),
                payload["recommended_pwm"],
                payload["safety_state"],
                len(annotated_drives),
            )
        if dbg_should("disks_ini_bind_advice", 9999999) and is_bind_mounted_file(disks_ini):
            log.warning(
                "%s is bind-mounted as a single file; map /var/local/emhttp to /unraid:ro for replacements",
                disks_ini,
            )
    except Exception:
        pass

    try:
        from services.history import record_status
        record_status(
            aggregate["hdd"].get("avg") if aggregate["hdd"].get("count") else None,
            aggregate["ssd"].get("avg") if aggregate["ssd"].get("count") else None,
            payload["recommended_pwm"],
        )
    except Exception as exc:
        log.warning("failed to record history: %s", exc)
    return payload
