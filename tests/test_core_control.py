import copy
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTAINER_ROOT = REPO_ROOT / "container"
if str(CONTAINER_ROOT) not in sys.path:
    sys.path.insert(0, str(CONTAINER_ROOT))

from services import disks  # noqa: E402
from services import pwm_calculator as pwm  # noqa: E402
from services import serial as serial_svc  # noqa: E402


BASE_CONFIG = {
    "controllers": [],
    "hdd_thresholds": [30, 35, 40, 44],
    "hdd_pwm": [10, 30, 50, 80],
    "ssd_thresholds": [35, 40, 45, 50],
    "ssd_pwm": [10, 30, 60, 90],
    "single_override_hdd_c": 90,
    "single_override_ssd_c": 90,
    "override_pwm": 100,
    "fallback_pwm": 10,
    "failsafe_pwm": 100,
    "exclude_devices": [],
    "drive_assignments": {},
    "auto_apply": False,
    "auto_apply_min_interval_seconds": 3,
    "auto_apply_refresh_interval_seconds": 20,
    "auto_apply_hysteresis_percent": 2,
}


def drive(
    dev: str,
    temp: int | None,
    *,
    dtype: str = "HDD",
    spun_down: bool = False,
) -> dict:
    return {
        "dev": dev,
        "slot": dev,
        "id": "",
        "section": dev,
        "type": dtype,
        "temp": temp,
        "state": "down" if spun_down else ("N/A" if temp is None else "up"),
        "spun_down": spun_down,
        "temp_status": (
            "spun_down" if spun_down else ("missing_active" if temp is None else "ok")
        ),
        "excluded": False,
    }


@pytest.fixture(autouse=True)
def isolated_process_state(monkeypatch):
    pwm.reset_auto_state()
    serial_svc._CTXS.clear()
    serial_svc._PORT_LOCKS.clear()
    serial_svc._LAST_RECONCILE_AT = 0.0
    monkeypatch.delenv("FANBRIDGE_DEV_SERIAL", raising=False)
    history = ModuleType("services.history")
    history.record_status = lambda *_args, **_kwargs: None
    history.record_statuses = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "services.history", history)
    yield
    pwm.reset_auto_state()
    serial_svc._CTXS.clear()
    serial_svc._PORT_LOCKS.clear()
    serial_svc._LAST_RECONCILE_AT = 0.0


def compute_with_source(
    tmp_path: Path,
    source_drives: list[dict],
    *,
    cfg: dict | None = None,
    source: dict | None = None,
    now: float | None = None,
    mtime: float | None = None,
) -> dict:
    path = tmp_path / "disks.ini"
    path.write_text("[placeholder]\n", encoding="utf-8")
    timestamp = time.time() if mtime is None else mtime
    os.utime(path, (timestamp, timestamp))
    context = {
        "cfg": copy.deepcopy(cfg or BASE_CONFIG),
        "disks_ini": str(path),
        "in_docker": lambda: True,
        "app_version": "test",
        "disks_stale_warn_sec": 1800,
        "allow_simulation": False,
        "dbg_should": lambda *_args: False,
        "warn_once": lambda *_args: None,
    }
    quality = source or {"ok": True, "error": None, "invalid_devices": []}
    with patch.object(
        pwm,
        "read_unraid_disks_with_status",
        return_value=(copy.deepcopy(source_drives), copy.deepcopy(quality)),
    ):
        if now is None:
            return pwm.compute_status(context)
        with patch.object(pwm.time, "time", return_value=now):
            return pwm.compute_status(context)


def test_unraid_parser_distinguishes_spun_down_and_active_missing(tmp_path, monkeypatch):
    ini = tmp_path / "disks.ini"
    ini.write_text(
        """
[disk1]
device = sda
name = Disk 1
temp = 0
spundown = 0

[disk2]
device = sdb
name = Disk 2
temp = 49
spundown = 1
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(disks, "_spin_state_from_sysfs", lambda _dev: None)
    monkeypatch.setattr(disks, "_is_hdd", lambda _dev: True)

    parsed, quality = disks.read_unraid_disks_with_status(str(ini), set())

    assert quality["ok"] is True
    assert parsed[0]["temp_status"] == "missing_active"
    assert parsed[0]["state"] == "N/A"
    assert parsed[1]["temp_status"] == "spun_down"
    assert parsed[1]["state"] == "down"
    assert parsed[1]["temp"] is None


def test_sysfs_running_transport_does_not_override_unraid_spindown(tmp_path, monkeypatch):
    ini = tmp_path / "disks.ini"
    ini.write_text(
        "[parity]\ndevice=sdb\nname=parity\nrotational=1\ntemp=*\nspundown=1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(disks, "_sysfs", lambda _dev, _rel: "running")

    parsed, quality = disks.read_unraid_disks_with_status(str(ini), set())

    assert quality["ok"] is True
    assert parsed[0]["spun_down"] is True
    assert parsed[0]["state"] == "down"
    assert parsed[0]["temp_status"] == "spun_down"


def test_unraid_parser_rejects_unsafe_device_names(tmp_path, monkeypatch):
    ini = tmp_path / "disks.ini"
    ini.write_text(
        "[disk1]\ndevice = ../../sda\nname = Disk 1\ntemp = 40\nspundown = 0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(disks, "_spin_state_from_sysfs", lambda _dev: None)

    parsed, quality = disks.read_unraid_disks_with_status(str(ini), set())

    assert parsed == []
    assert quality["ok"] is False
    assert quality["error"] == "invalid_device"
    assert quality["invalid_devices"] == ["../../sda"]


def test_unraid_parser_excludes_boot_media_and_empty_slots(tmp_path, monkeypatch):
    ini = tmp_path / "disks.ini"
    ini.write_text(
        """
[disk1]
device = sdb
name = disk1
type = Data
status = DISK_OK
rotational = 1
temp = 39
spundown = 0

[flash]
device = sda
name = flash
type = Flash
status = DISK_OK
rotational = 0
temp = *
spundown = 0

[disk2]
device =
name = disk2
type = Data
status = DISK_NP
temp = *
spundown = 0
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(disks, "_spin_state_from_sysfs", lambda _dev: None)

    parsed, quality = disks.read_unraid_disks_with_status(str(ini), set())

    assert quality["ok"] is True
    assert [item["dev"] for item in parsed] == ["sdb"]
    assert parsed[0]["type"] == "HDD"
    assert parsed[0]["unraid_type"] == "Data"
    assert parsed[0]["unraid_status"] == "DISK_OK"


def test_unraid_parser_exposes_stable_identifier_and_capacity(tmp_path, monkeypatch):
    ini = tmp_path / "disks.ini"
    ini.write_text(
        """
[disk1]
device = sdb
name = disk1
id = WDC_WD100EFAX-SERIAL123
size = 9766436812
type = Data
status = DISK_OK
rotational = 1
temp = 39
spundown = 0
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(disks, "_spin_state_from_sysfs", lambda _dev: None)

    parsed, quality = disks.read_unraid_disks_with_status(str(ini), set())

    assert quality["ok"] is True
    assert parsed[0]["slot"] == "disk1"
    assert parsed[0]["serial"] == "WDC_WD100EFAX-SERIAL123"
    assert parsed[0]["capacity_bytes"] == 9766436812 * 1024


def test_unraid_spin_state_disagreement_fails_safe_as_active_missing(tmp_path, monkeypatch):
    ini = tmp_path / "disks.ini"
    ini.write_text(
        "[disk1]\ndevice=sda\nname=disk1\ntemp=49\nspundown=1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(disks, "_spin_state_from_sysfs", lambda _dev: False)
    monkeypatch.setattr(disks, "_is_hdd", lambda _dev: True)

    parsed, quality = disks.read_unraid_disks_with_status(str(ini), set())

    assert quality["ok"] is True
    assert parsed[0]["spun_down"] is False
    assert parsed[0]["spin_state_conflict"] is True
    assert parsed[0]["temp"] is None
    assert parsed[0]["temp_status"] == "missing_active"


def test_nvme_temperature_uses_controller_name(monkeypatch):
    seen_patterns: list[str] = []

    def fake_glob(pattern: str) -> list[str]:
        seen_patterns.append(pattern)
        return ["/fake/temp1_input"]

    monkeypatch.setattr(disks.glob, "glob", fake_glob)
    monkeypatch.setattr(disks, "_read_file", lambda _path: "42000")

    assert disks._nvme_temp_sysfs("nvme12n3") == 42
    assert seen_patterns == [
        "/sys/class/nvme/nvme12/device/hwmon/hwmon*/temp*_input"
    ]
    assert disks._nvme_temp_sysfs("../../nvme0n1") is None


def test_pwm_curve_is_defensive_and_order_independent():
    assert pwm.map_temp_to_pwm(40, [], [], default=93) == 93
    assert pwm.map_temp_to_pwm(40, [30, 40], [20], default=91) == 91
    assert pwm.map_temp_to_pwm(40, [40, 30], [70, 20]) == 70
    assert pwm.map_temp_to_pwm(40, [30, 40], [80, 20], default=100) == 100
    assert pwm.map_temp_to_pwm("bad", [30], [20], default=100) == 100


def test_corrupt_persisted_config_fails_safe_without_breaking_status_json(tmp_path):
    config = copy.deepcopy(BASE_CONFIG)
    config["hdd_thresholds"] = None
    config["exclude_devices"] = [1, "sda"]
    config["drive_assignments"] = {1: ["bad-target"]}

    status = compute_with_source(tmp_path, [drive("sdb", 40)], cfg=config)

    assert status["recommended_pwm"] == 100
    assert status["faults"] == ["invalid_hdd_curve"]
    assert status["exclude_devices"] == ["1", "sda"]
    json.dumps(status)


def test_hottest_drive_controls_policy_instead_of_average(tmp_path):
    status = compute_with_source(
        tmp_path,
        [drive("sda", 44)] + [drive(f"sd{letter}", 30) for letter in "bcdefgh"],
    )

    assert status["hdd"]["avg"] == 31
    assert status["hdd"]["max"] == 44
    assert status["recommended_pwm"] == 80
    assert status["control_reason"] == "temperature_curve"


def test_active_missing_temp_uses_failsafe_but_spun_down_uses_idle(tmp_path):
    missing = compute_with_source(tmp_path, [drive("sda", None, spun_down=False)])
    sleeping = compute_with_source(tmp_path, [drive("sda", None, spun_down=True)])

    assert missing["recommended_pwm"] == 100
    assert missing["safety_state"] == "failsafe"
    assert "active_drive_temperature_missing" in missing["faults"]
    assert sleeping["recommended_pwm"] == 10
    assert sleeping["safety_state"] == "idle"


@pytest.mark.parametrize("error", ["missing", "parse_invalid", "invalid_device"])
def test_source_failure_uses_failsafe(tmp_path, error):
    status = compute_with_source(
        tmp_path,
        [],
        source={"ok": False, "error": error, "invalid_devices": []},
    )

    assert status["recommended_pwm"] == 100
    assert status["safety_state"] == "failsafe"
    assert status["faults"] == [f"temperature_source_{error}"]


def test_safety_fault_cannot_be_derated_by_persisted_config(tmp_path):
    config = copy.deepcopy(BASE_CONFIG)
    config["failsafe_pwm"] = 80
    config["override_pwm"] = 80

    failed = compute_with_source(
        tmp_path,
        [],
        cfg=config,
        source={"ok": False, "error": "missing", "invalid_devices": []},
    )
    hot = compute_with_source(tmp_path, [drive("sda", 100)], cfg=config)

    assert failed["recommended_pwm"] == 100
    assert failed["failsafe_pwm"] == 100
    assert hot["override"] is True
    assert hot["recommended_pwm"] == 100


def test_missing_local_source_does_not_silently_enable_simulation(tmp_path):
    missing = tmp_path / "missing-disks.ini"
    context = {
        "cfg": copy.deepcopy(BASE_CONFIG),
        "disks_ini": str(missing),
        "in_docker": lambda: False,
        "app_version": "test",
        "disks_stale_warn_sec": 1800,
        "allow_simulation": False,
        "dbg_should": lambda *_args: False,
        "warn_once": lambda *_args: None,
    }

    status = pwm.compute_status(context)

    assert status["mode"] == "unraid-missing"
    assert status["safety_state"] == "failsafe"
    assert status["recommended_pwm"] == 100


def test_simulation_never_actuates_temperature_derived_pwm(tmp_path, monkeypatch):
    sent: list[int] = []
    config = copy.deepcopy(BASE_CONFIG)
    config.update({
        "auto_apply": True,
        "controllers": [{
            "id": "left",
            "name": "Left",
            "type": "diy",
            "port": "/dev/ttyACM0",
            "baud": 115200,
        }],
        "sim": {"drives": [{"name": "sdb", "type": "HDD", "temp": 40}]},
    })
    missing_source = tmp_path / "missing-disks.ini"
    monkeypatch.setattr(
        serial_svc,
        "get_serial_status",
        lambda *_args, **_kwargs: {"connected": True},
    )
    monkeypatch.setattr(
        serial_svc,
        "serial_set_pwm_percent",
        lambda _cid, value: sent.append(int(value)) or {"ok": True, "value": int(value)},
    )

    status = pwm.compute_status({
        "cfg": config,
        "disks_ini": str(missing_source),
        "in_docker": lambda: False,
        "app_version": "test",
        "disks_stale_warn_sec": 600,
        "allow_simulation": True,
        "dbg_should": lambda *_args: False,
        "warn_once": lambda *_args: None,
    })

    assert status["mode"] == "sim"
    assert status["auto_apply_configured"] is True
    assert status["auto_apply"] is False
    assert status["auto_apply_blocked_reason"] == "simulation_source"
    assert sent == [100]


def test_stale_source_uses_failsafe(tmp_path):
    now = 50_000.0
    status = compute_with_source(
        tmp_path,
        [drive("sda", 35)],
        now=now,
        mtime=now - 2_000,
    )

    assert status["temperature_source"]["stale"] is True
    assert status["recommended_pwm"] == 100
    assert status["faults"] == ["temperature_source_stale"]


def test_controller_assignments_require_explicit_controller_targets(tmp_path):
    config = copy.deepcopy(BASE_CONFIG)
    config["controllers"] = [
        {"id": "left", "name": "Left", "port": "/dev/ttyACM0"},
        {"id": "right", "name": "Right", "port": "/dev/ttyACM1"},
    ]
    config["drive_assignments"] = {"sda": "left", "sdb": "right"}
    source_drives = [drive("sda", 44), drive("sdb", 32)]

    assigned = compute_with_source(tmp_path, source_drives, cfg=config)

    by_id = {controller["id"]: controller for controller in assigned["controllers"]}
    assert [item["dev"] for item in by_id["left"]["drives"]] == ["sda"]
    assert by_id["left"]["recommended_pwm"] == 80
    assert [item["dev"] for item in by_id["right"]["drives"]] == ["sdb"]
    assert by_id["right"]["recommended_pwm"] == 10
    assert assigned["recommended_pwm"] == 80

    config["drive_assignments"] = {}
    unassigned = compute_with_source(tmp_path, source_drives, cfg=config)
    for controller in unassigned["controllers"]:
        assert controller["drives"] == []
        assert controller["recommended_pwm"] == 10


def test_unassigned_controller_can_idle_during_source_failure(tmp_path):
    config = copy.deepcopy(BASE_CONFIG)
    config["controllers"] = [
        {"id": "left", "name": "Left", "port": "/dev/ttyACM0"},
        {"id": "right", "name": "Right", "port": "/dev/ttyACM1"},
    ]
    config["drive_assignments"] = {"sda": "left"}
    status = compute_with_source(
        tmp_path,
        [],
        cfg=config,
        source={"ok": False, "error": "missing", "invalid_devices": []},
    )

    by_id = {controller["id"]: controller for controller in status["controllers"]}
    assert by_id["left"]["recommended_pwm"] == 100
    assert by_id["right"]["recommended_pwm"] == 10
    assert by_id["right"]["control_reason"] == "idle_or_unassigned"


def test_unknown_assignment_is_not_routed_to_a_controller(tmp_path):
    config = copy.deepcopy(BASE_CONFIG)
    config["controllers"] = [
        {"id": "left", "name": "Left", "port": "/dev/ttyACM0"},
    ]
    config["drive_assignments"] = {"sda": "deleted-controller"}
    status = compute_with_source(
        tmp_path,
        [],
        cfg=config,
        source={"ok": False, "error": "missing", "invalid_devices": []},
    )

    controller = status["controllers"][0]
    assert controller["recommended_pwm"] == 10
    assert controller["safety_state"] == "idle"


def test_auto_apply_refreshes_lease_and_resends_after_reconnect(tmp_path, monkeypatch):
    config = copy.deepcopy(BASE_CONFIG)
    config["controllers"] = [
        {"id": "left", "name": "Left", "port": "/dev/ttyACM0"},
    ]
    config["drive_assignments"] = {"sda": "left"}
    config["auto_apply"] = True
    connected = {"value": True}
    sends: list[tuple[str, int]] = []
    monkeypatch.setattr(
        pwm.serial_svc,
        "get_serial_status",
        lambda _cid, full=False: {"connected": connected["value"]},
    )
    monkeypatch.setattr(
        pwm.serial_svc,
        "serial_set_pwm_percent",
        lambda cid, value: sends.append((cid, value)) or {"ok": True},
    )
    start = time.time()

    compute_with_source(tmp_path, [drive("sda", 44)], cfg=config, now=start, mtime=start)
    compute_with_source(
        tmp_path, [drive("sda", 44)], cfg=config, now=start + 5, mtime=start
    )
    compute_with_source(
        tmp_path, [drive("sda", 44)], cfg=config, now=start + 21, mtime=start
    )
    connected["value"] = False
    paused = compute_with_source(
        tmp_path, [drive("sda", 44)], cfg=config, now=start + 22, mtime=start
    )
    connected["value"] = True
    resumed = compute_with_source(
        tmp_path, [drive("sda", 44)], cfg=config, now=start + 23, mtime=start
    )

    assert sends == [("left", 80), ("left", 80), ("left", 80)]
    assert paused["controllers"][0]["auto_paused"] is True
    assert resumed["controllers"][0]["auto_paused"] is False
    assert resumed["controllers"][0]["auto_last_percent"] == 80


def test_disabling_auto_apply_sends_one_immediate_safe_stop(tmp_path, monkeypatch):
    config = copy.deepcopy(BASE_CONFIG)
    config["controllers"] = [
        {"id": "left", "name": "Left", "port": "/dev/ttyACM0"},
    ]
    sends: list[tuple[str, int]] = []
    monkeypatch.setattr(
        pwm.serial_svc,
        "get_serial_status",
        lambda _cid, full=False: {"connected": True},
    )
    monkeypatch.setattr(
        pwm.serial_svc,
        "serial_set_pwm_percent",
        lambda cid, value: sends.append((cid, value)) or {"ok": True},
    )
    start = time.time()

    first = compute_with_source(tmp_path, [drive("sda", 40)], cfg=config, now=start, mtime=start)
    second = compute_with_source(tmp_path, [drive("sda", 40)], cfg=config, now=start + 10, mtime=start)

    assert sends == [("left", 100)]
    assert first["controllers"][0]["auto_last_percent"] == 100
    assert second["controllers"][0]["auto_last_percent"] == 100


def test_manual_mode_ignores_curves_and_refreshes_the_saved_setpoint(tmp_path, monkeypatch):
    config = copy.deepcopy(BASE_CONFIG)
    config["controllers"] = [{
        "id": "left",
        "name": "Left",
        "port": "/dev/ttyACM0",
        "control_mode": "manual",
        "manual_pwm": 55,
    }]
    config["drive_assignments"] = {"sda": "left"}
    # The legacy aggregate flag may remain true while another controller is
    # automatic; the selected controller's explicit mode must win.
    config["auto_apply"] = True
    sends: list[tuple[str, int]] = []
    recorded: list[list[tuple[str, int | None, int | None, int]]] = []
    history = ModuleType("services.history")
    history.record_statuses = lambda rows: recorded.append(list(rows))
    monkeypatch.setitem(sys.modules, "services.history", history)
    monkeypatch.setattr(
        pwm.serial_svc,
        "get_serial_status",
        lambda _cid, full=False: {"connected": True},
    )
    monkeypatch.setattr(
        pwm.serial_svc,
        "serial_set_pwm_percent",
        lambda cid, value: sends.append((cid, value)) or {"ok": True},
    )
    start = time.time()

    first = compute_with_source(
        tmp_path, [drive("sda", 44)], cfg=config, now=start, mtime=start
    )
    compute_with_source(
        tmp_path, [drive("sda", 44)], cfg=config, now=start + 10, mtime=start
    )
    compute_with_source(
        tmp_path, [drive("sda", 44)], cfg=config, now=start + 21, mtime=start
    )

    controller = first["controllers"][0]
    assert controller["recommended_pwm"] == 80
    assert controller["control_mode"] == "manual"
    assert controller["manual_pwm"] == 55
    assert controller["output_target_percent"] == 55
    assert sends == [("left", 55), ("left", 55)]
    assert all(rows[1][0] == "left" and rows[1][3] == 55 for rows in recorded)


def test_manual_mode_forces_100_at_critical_temperature_until_hysteresis_clears(tmp_path, monkeypatch):
    config = copy.deepcopy(BASE_CONFIG)
    config["single_override_hdd_c"] = 45
    config["controllers"] = [{
        "id": "left",
        "name": "Left",
        "port": "/dev/ttyACM0",
        "control_mode": "manual",
        "manual_pwm": 0,
    }]
    config["drive_assignments"] = {"sda": "left"}
    sends = []
    monkeypatch.setattr(
        pwm.serial_svc,
        "get_serial_status",
        lambda _cid, full=False: {"connected": True},
    )
    monkeypatch.setattr(
        pwm.serial_svc,
        "serial_set_pwm_percent",
        lambda cid, value: sends.append((cid, value)) or {"ok": True},
    )
    start = time.time()

    critical = compute_with_source(
        tmp_path, [drive("sda", 45)], cfg=config, now=start, mtime=start
    )
    hysteresis = compute_with_source(
        tmp_path, [drive("sda", 43)], cfg=config, now=start + 10, mtime=start
    )
    cleared = compute_with_source(
        tmp_path, [drive("sda", 41)], cfg=config, now=start + 21, mtime=start
    )

    assert critical["controllers"][0]["manual_safety_override_active"] is True
    assert critical["controllers"][0]["output_target_percent"] == 100
    assert hysteresis["controllers"][0]["manual_safety_reason"] == "thermal_hysteresis"
    assert hysteresis["controllers"][0]["output_target_percent"] == 100
    assert cleared["controllers"][0]["manual_safety_override_active"] is False
    assert cleared["controllers"][0]["output_target_percent"] == 0
    assert sends == [("left", 100), ("left", 0)]


def test_mode_change_is_acknowledged_even_when_target_is_unchanged(tmp_path, monkeypatch, caplog):
    config = copy.deepcopy(BASE_CONFIG)
    config["controllers"] = [{
        "id": "left", "name": "Left", "port": "/dev/ttyACM0",
        "control_mode": "manual", "manual_pwm": 100,
    }]
    sends = []
    monkeypatch.setattr(
        pwm.serial_svc,
        "get_serial_status",
        lambda _cid, full=False: {"connected": True},
    )
    monkeypatch.setattr(
        pwm.serial_svc,
        "serial_set_pwm_percent",
        lambda cid, value: sends.append((cid, value)) or {"ok": True},
    )
    caplog.set_level(logging.INFO)
    start = time.time()

    compute_with_source(tmp_path, [], cfg=config, now=start, mtime=start)
    config["controllers"][0]["control_mode"] = "auto"
    config["fallback_pwm"] = 100
    compute_with_source(tmp_path, [], cfg=config, now=start + 1, mtime=start)

    assert sends == [("left", 100), ("left", 100)]
    assert "mode=automatic | target=100% | reason=mode_change" in caplog.text


def test_history_uses_null_for_missing_temperature_classes(tmp_path, monkeypatch):
    recorded: list[list[tuple[str, int | None, int | None, int]]] = []
    history = ModuleType("services.history")
    history.record_statuses = lambda rows: recorded.append(list(rows))
    monkeypatch.setitem(sys.modules, "services.history", history)

    compute_with_source(tmp_path, [drive("sda", 37)])

    assert recorded == [[("", 37, None, 30)]]


class FakeSerial:
    reply = b"ACK\n"
    active_writes = 0
    max_active_writes = 0
    counter_lock = threading.Lock()

    def __init__(self, port, baudrate, timeout):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout

    def reset_input_buffer(self):
        return None

    def reset_output_buffer(self):
        return None

    def write(self, payload):
        with self.counter_lock:
            type(self).active_writes += 1
            type(self).max_active_writes = max(
                type(self).max_active_writes, type(self).active_writes
            )
        time.sleep(0.02)
        with self.counter_lock:
            type(self).active_writes -= 1
        return len(payload)

    def flush(self):
        return None

    def readline(self):
        return self.reply

    def close(self):
        return None


class ScriptedIdentitySerial(FakeSerial):
    writes: list[str] = []
    legacy = False
    banner_first = False
    invalid_ack = False

    def __init__(self, port, baudrate, timeout):
        super().__init__(port, baudrate, timeout)
        self.responses: list[bytes] = []

    def write(self, payload):
        command = bytes(payload).decode("ascii").strip()
        type(self).writes.append(command)
        if command == "ID?":
            if type(self).legacy:
                self.responses.append(b"Unknown. Use: VERSION, PING, UPTIME, STATUS, TEST, BOOTSEL, 0..100\n")
            else:
                if type(self).banner_first:
                    self.responses.extend([
                        b"FANBRIDGE-LINK 2.3.0 ready @115200 (PWM-only)\n",
                        b"Commands: VERSION, PING, RPM, UPTIME, STATUS, TEST, BOOTSEL, 0..100\n",
                    ])
                self.responses.append(b"FANBRIDGE_DIY protocol=1 board=pico-dev channels=1\n")
        elif command == "VERSION":
            self.responses.append(b"2.2.0\n" if type(self).legacy else b"2.3.0\n")
        elif command.isdigit():
            value = 99 if type(self).invalid_ack else int(command)
            self.responses.append(f"Set fan to {value}%\n".encode("ascii"))
        return len(payload)

    def readline(self):
        return self.responses.pop(0) if self.responses else b""


class IdentifySerial(FakeSerial):
    writes: list[str] = []
    acknowledge = True

    def __init__(self, port, baudrate, timeout):
        super().__init__(port, baudrate, timeout)
        self.responses: list[bytes] = []

    def write(self, payload):
        command = bytes(payload).decode("ascii").strip()
        type(self).writes.append(command)
        if command == "ID?":
            self.responses.append(
                b"FANBRIDGE_DIY protocol=2 board=rp2040-zero channels=1 uid=A1B2C3D4E5F60718\n"
            )
        elif command == "IDENTIFY" and type(self).acknowledge:
            self.responses.append(b"IDENTIFYING duration_ms=10000\n")
        return len(payload)

    def readline(self):
        return self.responses.pop(0) if self.responses else b""


def test_protocol_two_identity_requires_and_normalises_persistent_uid():
    parsed = serial_svc.parse_identity_response(
        "FANBRIDGE_DIY protocol=2 board=pico-dev channels=1 uid=A1B2C3D4E5F60718"
    )

    assert parsed is not None
    assert parsed["supported"] is True
    assert parsed["hardware_uid"] == "a1b2c3d4e5f60718"
    assert serial_svc.parse_identity_response(
        "FANBRIDGE_DIY protocol=2 board=pico-dev channels=1"
    ) is None


def test_unregistered_controller_identify_uses_only_identity_and_led_commands(monkeypatch):
    IdentifySerial.writes = []
    IdentifySerial.acknowledge = True
    monkeypatch.setattr(serial_svc, "serial", SimpleNamespace(Serial=IdentifySerial))

    result = serial_svc.identify_unregistered_controller("/dev/ttyACM9")

    assert result["ok"] is True
    assert result["duration_ms"] == 10000
    assert result["identity"]["hardware_uid"] == "a1b2c3d4e5f60718"
    assert IdentifySerial.writes == ["ID?", "IDENTIFY"]


def test_unregistered_controller_identify_rejects_missing_firmware_ack(monkeypatch):
    IdentifySerial.writes = []
    IdentifySerial.acknowledge = False
    monkeypatch.setattr(serial_svc, "serial", SimpleNamespace(Serial=IdentifySerial))

    result = serial_svc.identify_unregistered_controller("/dev/ttyACM9", timeout=0.01)

    assert result["ok"] is False
    assert result["code"] == "upgrade_required"
    assert not any(command.isdigit() for command in IdentifySerial.writes)


def test_unregistered_controller_identify_rejects_configured_hardware_uid(monkeypatch):
    IdentifySerial.writes = []
    IdentifySerial.acknowledge = True
    monkeypatch.setattr(serial_svc, "serial", SimpleNamespace(Serial=IdentifySerial))

    result = serial_svc.identify_unregistered_controller(
        "/dev/ttyACM9",
        excluded_hardware_uids={"A1B2C3D4E5F60718"},
    )

    assert result["ok"] is False
    assert result["code"] == "already_assigned"
    assert IdentifySerial.writes == ["ID?"]


def test_persistent_uids_rebind_two_controllers_after_usb_paths_swap(monkeypatch):
    left_uid = "0011223344556677"
    right_uid = "8899aabbccddeeff"
    identities = {
        "/dev/ttyACM0": {
            "type": "diy", "protocol": 2, "board": "pico-dev", "channels": 1,
            "hardware_uid": right_uid, "supported": True, "legacy": False,
        },
        "/dev/ttyACM1": {
            "type": "diy", "protocol": 2, "board": "pico-dev", "channels": 1,
            "hardware_uid": left_uid, "supported": True, "legacy": False,
        },
    }
    monkeypatch.setattr(serial_svc, "list_serial_ports", lambda: list(identities))
    monkeypatch.setattr(serial_svc, "identify_port_details", lambda port, timeout=0.5: identities.get(port))
    assert serial_svc.register_controller(
        "left", "/dev/ttyACM0", 115200, expected_type="diy", expected_uid=left_uid
    )
    assert serial_svc.register_controller(
        "right", "/dev/ttyACM1", 115200, expected_type="diy", expected_uid=right_uid
    )

    result = serial_svc.reconcile_controller_ports(force=True)
    registered = {item["id"]: item for item in serial_svc.list_registered_controllers()}

    assert result["bindings"] == {"left": "/dev/ttyACM1", "right": "/dev/ttyACM0"}
    assert registered["left"]["preferred"] == "/dev/ttyACM1"
    assert registered["right"]["preferred"] == "/dev/ttyACM0"
    assert registered["left"]["configured"] == "/dev/ttyACM0"


def test_wrong_or_duplicated_hardware_uid_is_never_bound(monkeypatch):
    expected_uid = "0011223344556677"
    wrong_uid = "8899aabbccddeeff"
    identities = {
        "/dev/ttyACM0": {
            "type": "diy", "protocol": 2, "board": "pico-dev", "channels": 1,
            "hardware_uid": wrong_uid, "supported": True, "legacy": False,
        },
    }
    monkeypatch.setattr(serial_svc, "list_serial_ports", lambda: list(identities))
    monkeypatch.setattr(serial_svc, "identify_port_details", lambda port, timeout=0.5: identities.get(port))
    assert serial_svc.register_controller(
        "left", "/dev/ttyACM0", 115200, expected_type="diy", expected_uid=expected_uid
    )

    serial_svc.reconcile_controller_ports(force=True)
    ok, identity, error = serial_svc.verify_controller_identity("left")

    assert ok is False
    assert identity is None
    assert "hardware UID" in error
    assert serial_svc.list_registered_controllers()[0]["preferred"] == ""

    identities["/dev/ttyACM0"]["hardware_uid"] = expected_uid
    identities["/dev/ttyACM1"] = dict(identities["/dev/ttyACM0"])
    duplicate = serial_svc.reconcile_controller_ports(force=True)
    assert duplicate["duplicate_uids"] == [expected_uid]
    assert serial_svc.list_registered_controllers()[0]["preferred"] == ""


def test_serial_expected_reply_cannot_succeed_silently(monkeypatch):
    class NoReplySerial(FakeSerial):
        reply = b""

    monkeypatch.setattr(serial_svc, "serial", SimpleNamespace(Serial=NoReplySerial))
    assert serial_svc.register_controller("left", "/dev/ttyACM0", 115200)

    result = serial_svc.serial_send_line("left", "50", expect_reply=True)

    assert result["ok"] is False
    assert result["error"] == "no reply from controller"


def test_operator_serial_event_records_confirmed_reply_at_normal_level(caplog):
    caplog.set_level(logging.INFO)

    serial_svc.record_operator_transaction(
        "left",
        "PING\nforged",
        {"ok": True, "reply": "PONG\x1b[31m\nforged"},
    )

    message = caplog.records[-1].getMessage()
    assert caplog.records[-1].levelno == logging.INFO
    assert "cid=left" in message
    assert "TX PING forged" in message
    assert "RX PONG?[31m forged" in message
    assert "\n" not in message


def test_operator_status_event_is_summarised_instead_of_logging_raw_json(caplog):
    caplog.set_level(logging.INFO)
    reply = json.dumps({
        "fw": "2.5.2",
        "board": "rp2040-zero",
        "controller_uid": "50443405884d8d1c",
        "setpoint_pct": 100,
        "applied_pwm_pct": 100.0,
        "failsafe_active": False,
        "control_age_ms": 1234,
        "control_lease_ms": 60000,
        "capabilities": ["pwm.single", "failsafe.lease", "identify.led"],
    })

    serial_svc.record_operator_transaction(
        "left", "STATUS", {"ok": True, "reply": reply}
    )

    message = caplog.records[-1].getMessage()
    assert "RX STATUS fw=2.5.2; board=rp2040-zero" in message
    assert "setpoint=100%; applied=100.0%; failsafe=off" in message
    assert '"capabilities"' not in message


def test_serial_exceptions_are_logged_but_return_bounded_public_errors(monkeypatch, caplog):
    sentinel = "SENTINEL stack /private/device/path"

    class ExplodingSerial:
        def __init__(self, *_args, **_kwargs):
            raise OSError(5, sentinel)

    monkeypatch.setattr(serial_svc, "serial", SimpleNamespace(Serial=ExplodingSerial))
    assert serial_svc.register_controller("left", "/dev/ttyACM0", 115200)

    opened, probe_error = serial_svc.probe_serial_open("/dev/ttyACM0", 115200)
    result = serial_svc.serial_send_line("left", "PING")

    assert opened is False
    assert probe_error == "serial device operation failed; see server logs"
    assert result["ok"] is False
    assert result["error"] == "serial device operation failed; see server logs"
    assert sentinel not in probe_error
    assert sentinel not in result["error"]
    assert sentinel in caplog.text


def test_serial_pwm_rejects_out_of_range_instead_of_clamping(monkeypatch):
    monkeypatch.setattr(serial_svc, "serial", SimpleNamespace(Serial=FakeSerial))
    assert serial_svc.register_controller("left", "/dev/ttyACM0", 115200)

    assert serial_svc.serial_set_pwm_percent("left", -1)["ok"] is False
    assert serial_svc.serial_set_pwm_percent("left", 101)["ok"] is False
    assert serial_svc.serial_set_pwm_percent("left", True)["ok"] is False


def test_serial_pwm_requires_verified_identity_and_exact_ack(monkeypatch):
    ScriptedIdentitySerial.writes = []
    ScriptedIdentitySerial.legacy = False
    ScriptedIdentitySerial.banner_first = False
    ScriptedIdentitySerial.invalid_ack = False
    monkeypatch.setattr(serial_svc, "serial", SimpleNamespace(Serial=ScriptedIdentitySerial))
    assert serial_svc.register_controller("left", "/dev/ttyACM0", 115200, expected_type="diy")

    valid = serial_svc.serial_set_pwm_percent("left", 50)
    ScriptedIdentitySerial.invalid_ack = True
    invalid = serial_svc.serial_set_pwm_percent("left", 60)

    assert valid["ok"] is True
    assert invalid["ok"] is False
    assert invalid["error"] == "controller returned an invalid PWM acknowledgement"


def test_future_official_controller_is_not_driven_by_diy_global_protocol(monkeypatch):
    monkeypatch.setattr(serial_svc, "serial", SimpleNamespace(Serial=FakeSerial))
    monkeypatch.setattr(
        serial_svc,
        "identify_port_details",
        lambda _port, timeout=0.5: {
            "type": "official",
            "protocol": 1,
            "board": "custom-dev",
            "channels": 6,
            "supported": True,
            "legacy": False,
        },
    )
    assert serial_svc.register_controller("custom", "/dev/ttyACM1", 115200, expected_type="official")

    result = serial_svc.serial_set_pwm_percent("custom", 50)

    assert result["ok"] is False
    assert "six-channel" in result["error"]


def test_identity_handshake_ignores_delayed_startup_banners(monkeypatch):
    ScriptedIdentitySerial.writes = []
    ScriptedIdentitySerial.legacy = False
    ScriptedIdentitySerial.banner_first = True
    ScriptedIdentitySerial.invalid_ack = False
    monkeypatch.setattr(serial_svc, "serial", SimpleNamespace(Serial=ScriptedIdentitySerial))
    monkeypatch.setattr(serial_svc, "list_serial_ports", lambda: ["/dev/ttyACM0"])
    assert serial_svc.register_controller("left", "/dev/ttyACM0", 115200, expected_type="diy")

    status = serial_svc.get_serial_status("left", full=False)

    assert status["connected"] is True
    assert status["identity"]["protocol"] == 1
    assert "100" not in ScriptedIdentitySerial.writes


def test_released_legacy_firmware_is_forced_safe_then_quarantined(monkeypatch):
    ScriptedIdentitySerial.writes = []
    ScriptedIdentitySerial.legacy = True
    ScriptedIdentitySerial.banner_first = False
    ScriptedIdentitySerial.invalid_ack = False
    monkeypatch.setattr(serial_svc, "serial", SimpleNamespace(Serial=ScriptedIdentitySerial))
    monkeypatch.setattr(serial_svc, "list_serial_ports", lambda: ["/dev/ttyACM0"])
    assert serial_svc.register_controller("left", "/dev/ttyACM0", 115200, expected_type="diy")

    first = serial_svc.get_serial_status("left", full=False)
    second = serial_svc.get_serial_status("left", full=False)

    assert first["connected"] is False
    assert first["identity"]["legacy"] is True
    assert first["identity"]["safe_stop_ok"] is True
    assert second["connected"] is False
    assert ScriptedIdentitySerial.writes == ["ID?", "VERSION", "100"]


def test_serial_transactions_are_serialized_per_controller(monkeypatch):
    FakeSerial.active_writes = 0
    FakeSerial.max_active_writes = 0
    monkeypatch.setattr(serial_svc, "serial", SimpleNamespace(Serial=FakeSerial))
    assert serial_svc.register_controller("left", "/dev/ttyACM0", 115200)
    barrier = threading.Barrier(3)
    results: list[dict] = []

    def send():
        barrier.wait()
        results.append(serial_svc.serial_send_line("left", "50", expect_reply=True))

    threads = [threading.Thread(target=send), threading.Thread(target=send)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert [result["ok"] for result in results] == [True, True]
    assert FakeSerial.max_active_writes == 1


def test_serial_registration_deduplicates_physical_aliases(tmp_path):
    physical = tmp_path / "controller"
    physical.touch()
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.symlink_to(physical)
    second.symlink_to(physical)

    assert serial_svc.register_controller("left", str(first), 115200) is True
    assert serial_svc.register_controller("right", str(second), 115200) is False
    assert serial_svc.controller_for_port(str(physical)) == "left"


def test_serial_discovery_hides_dev_pseudoterminals_by_default(monkeypatch):
    candidates = {
        "/host-dev/serial/by-id/*": [],
        "/host-dev/ttyACM*": [],
        "/host-dev/ttyUSB*": [],
        "/dev/serial/by-id/*": ["/dev/serial/by-id/fanbridge"],
        "/dev/ttyACM*": ["/dev/ttyACM0"],
        "/dev/ttyUSB*": [],
        "/dev/pts/*": ["/dev/pts/5"],
        "/tmp/ttyFAN*": ["/tmp/ttyFAN0"],
    }
    physical = {
        "/dev/serial/by-id/fanbridge": "/dev/ttyACM0",
        "/dev/ttyACM0": "/dev/ttyACM0",
    }
    monkeypatch.setattr(serial_svc.glob, "glob", lambda value: candidates[value])
    monkeypatch.setattr(
        serial_svc,
        "canonical_port",
        lambda value: physical.get(str(value), str(value)),
    )
    monkeypatch.setattr(serial_svc, "list_ports", None)

    assert serial_svc.list_serial_ports() == ["/dev/serial/by-id/fanbridge"]
    monkeypatch.setenv("FANBRIDGE_DEV_SERIAL", "1")
    assert serial_svc.list_serial_ports() == [
        "/dev/serial/by-id/fanbridge",
        "/dev/pts/5",
        "/tmp/ttyFAN0",
    ]


def test_rp2040_firmware_has_safe_boot_and_control_lease_contract():
    source = (REPO_ROOT / "fanbridge-link/rp2040/src/main.cpp").read_text(
        encoding="utf-8"
    )

    assert "START_PERCENT     = 100" in source
    assert "CONTROL_LEASE_MS  = 60000UL" in source
    assert 'F(",\\\"failsafe_active\\\":")' in source
    assert 'F(",\\\"control_age_ms\\\":")' in source
    assert "lastControlTime = millis();" in source
    assert "hasReceivedControl = true;" in source
    numeric_start = source.index("// Numeric percent 0..100")
    lease_renewal = source.index("lastControlTime = millis();")
    assert lease_renewal > numeric_start
    assert "millis() - lastCmdTime" not in source
    assert "WATCHDOG_MS       = 4000UL" in source
    assert "startHardwareWatchdog();" in source
    assert "watchdogStarted = watchdog.is_running();" in source
    assert "if (!watchdogStarted && value < 100U)" in source
    assert "ERR: watchdog unavailable; fan held at 100%" in source
    assert source.index("setFanPercent(START_PERCENT, false);") < source.index("Serial.begin(115200);")
    assert "ERR: RPM unsupported" in source
