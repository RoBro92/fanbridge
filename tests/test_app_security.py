import os
import pathlib
import struct
import hashlib
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest
from werkzeug.security import check_password_hash, generate_password_hash


_RUNTIME = pathlib.Path(tempfile.mkdtemp(prefix="fanbridge-app-tests-"))
_CONTAINER = pathlib.Path(__file__).resolve().parents[1] / "container"
sys.path.insert(0, str(_CONTAINER))
os.environ["FANBRIDGE_CONFIG"] = str(_RUNTIME / "config.yml")
os.environ["FANBRIDGE_USERS"] = str(_RUNTIME / "users.yml")
os.environ["FANBRIDGE_SECRET_PATH"] = str(_RUNTIME / "secret.key")
os.environ["FANBRIDGE_SETUP_TOKEN_PATH"] = str(_RUNTIME / "setup.token")
os.environ["FANBRIDGE_SETUP_TOKEN"] = "test-bootstrap-token-123"
os.environ["FANBRIDGE_CONTROL_LOOP"] = "0"

import app as fanbridge  # noqa: E402
from core.appver import latest_github_release  # noqa: E402
from core.http import _allowed_api_url, _allowed_firmware_download_url  # noqa: E402
from core.logging_setup import LOG_LOCK, LOG_RING  # noqa: E402


@pytest.fixture(autouse=True)
def reset_state():
    fanbridge._RATE.clear()
    fanbridge._LAST_GOOD_CONFIG = None
    pathlib.Path(fanbridge.USERS_PATH).unlink(missing_ok=True)
    pathlib.Path(fanbridge.CONFIG_PATH).unlink(missing_ok=True)
    fanbridge.ensure_config_exists()
    fanbridge.load_config()
    fanbridge._CONTROL_THREAD = None
    fanbridge._CONTROL_STATE.update({
        "started_at": None,
        "last_attempt_at": None,
        "last_success_at": None,
        "last_error": None,
        "snapshot": None,
    })
    fanbridge._FIRMWARE_RELEASE_CACHE.update({
        "expires_at": time.monotonic() + 3600,
        "release": None,
        "error": None,
    })
    fanbridge.app.config.update(TESTING=True)
    yield


def _authenticated_client():
    username = "admin"
    fanbridge._save_users({
        "users": {username: generate_password_hash("correct-horse-battery")},
        "session_versions": {username: 1},
    })
    client = fanbridge.app.test_client()
    with client.session_transaction() as session:
        session["user"] = username
        session["auth_version"] = 1
        session["csrf_token"] = "csrf-test-token"
    return client, {"X-CSRF-Token": "csrf-test-token"}


def test_api_authentication_failure_is_json_not_login_html():
    response = fanbridge.app.test_client().get("/api/status")
    assert response.status_code == 401
    assert response.is_json
    assert response.get_json()["error"] == "authentication required"


@pytest.mark.parametrize("endpoint", ["/api/auto_apply", "/api/config", "/api/controllers"])
def test_mutation_apis_reject_non_object_json(endpoint):
    client, headers = _authenticated_client()

    response = client.post(endpoint, json=["not", "an", "object"], headers=headers)

    assert response.status_code == 400
    assert response.is_json


def test_first_run_requires_csrf_setup_token_and_minimum_password():
    client = fanbridge.app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]

    weak = client.post("/login", data={
        "csrf_token": csrf,
        "setup_token": "test-bootstrap-token-123",
        "username": "admin",
        "password": "seven77",
        "confirm": "seven77",
    })
    assert weak.status_code == 200
    assert not fanbridge._load_users().get("users")

    wrong_token = client.post("/login", data={
        "csrf_token": csrf,
        "setup_token": "wrong",
        "username": "admin",
        "password": "eight888",
        "confirm": "eight888",
    })
    assert wrong_token.status_code == 403

    created = client.post("/login", data={
        "csrf_token": csrf,
        "setup_token": "test-bootstrap-token-123",
        "username": "admin",
        "password": "eight888",
        "confirm": "eight888",
    })
    assert created.status_code == 302
    assert created.headers["Location"].endswith("/")


def test_setup_token_is_highlighted_in_container_console(monkeypatch, tmp_path, capsys):
    token_path = tmp_path / "setup.token"
    monkeypatch.delenv("FANBRIDGE_SETUP_TOKEN")
    monkeypatch.setenv("FANBRIDGE_SETUP_TOKEN_PATH", str(token_path))
    monkeypatch.setattr(fanbridge, "_SETUP_TOKEN_BANNER_WRITTEN", False)

    token = fanbridge._load_or_create_setup_token()
    console = capsys.readouterr().err

    assert "FANBRIDGE FIRST RUN SETUP TOKEN" in console
    assert token in console
    assert console.count(token) == 1
    assert "+======================================================================+" in console
    assert "/config/setup.token" in console

    monkeypatch.setattr(fanbridge, "_SETUP_TOKEN_BANNER_WRITTEN", False)
    assert fanbridge._load_or_create_setup_token() == token
    restarted_console = capsys.readouterr().err
    assert restarted_console.count(token) == 1


def test_change_password_accepts_eight_characters_and_rejects_seven():
    client, headers = _authenticated_client()

    too_short = client.post("/api/change_password", json={
        "current": "correct-horse-battery",
        "new": "seven77",
        "confirm": "seven77",
    }, headers=headers)
    assert too_short.status_code == 400
    assert too_short.get_json()["error"] == "new password must be 8 to 256 characters"

    changed = client.post("/api/change_password", json={
        "current": "correct-horse-battery",
        "new": "eight888",
        "confirm": "eight888",
    }, headers=headers)
    assert changed.status_code == 200
    stored = fanbridge._load_users()["users"]["admin"]
    assert check_password_hash(stored, "eight888")


def test_login_rejects_external_next_redirect():
    fanbridge._save_users({
        "users": {"admin": generate_password_hash("correct-horse-battery")},
        "session_versions": {"admin": 1},
    })
    client = fanbridge.app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    response = client.post("/login?next=https://attacker.invalid/", data={
        "csrf_token": csrf,
        "username": "admin",
        "password": "correct-horse-battery",
    })
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    assert "attacker.invalid" not in response.headers["Location"]


def test_application_update_check_is_fixed_to_the_github_api_boundary():
    assert _allowed_api_url("https://api.github.com/repos/RoBroLabs/fanbridge/releases/latest")
    assert not _allowed_api_url("http://api.github.com/repos/RoBroLabs/fanbridge/releases/latest")
    assert not _allowed_api_url("https://api.github.com.attacker.invalid/repos/example")
    assert not _allowed_api_url("file:///etc/passwd")
    assert latest_github_release("../attacker") is None
    approved_asset = (
        "https://github.com/RoBroLabs/fanbridge/releases/download/"
        "fw-v2.5.3/fanbridge-rp2040-2.5.3.uf2"
    )
    assert _allowed_firmware_download_url(approved_asset)
    assert _allowed_firmware_download_url(f"{approved_asset}.sha256")
    assert not _allowed_firmware_download_url(approved_asset.replace("https://", "http://"))
    assert not _allowed_firmware_download_url(approved_asset.replace("RoBroLabs", "attacker"))
    assert not _allowed_firmware_download_url(f"{approved_asset}?token=attacker")


def test_settings_reject_unknown_fields_and_persist_canonical_schema():
    client, headers = _authenticated_client()
    unknown = client.post("/api/settings", json={"pretend_setting": 1}, headers=headers)
    assert unknown.status_code == 400

    response = client.post("/api/settings", json={
        "poll_interval_seconds": 9,
        "auto_apply_hysteresis_percent": 4,
        "excluded_devices": ["sdb"],
        "drive_assignments": {"sda": "none", "sdb": "none"},
    }, headers=headers)
    assert response.status_code == 200
    saved = fanbridge.load_config()
    assert saved["poll_interval_seconds"] == 9
    assert saved["auto_apply_hysteresis_percent"] == 4
    assert saved["exclude_devices"] == ["sdb"]
    assert saved["drive_assignments"] == {"sda": "none", "sdb": "none"}

    global_assignment = client.post("/api/settings", json={
        "drive_assignments": {"sda": "global"},
    }, headers=headers)
    assert global_assignment.status_code == 400


def test_curves_require_paired_monotonic_values():
    client, headers = _authenticated_client()
    invalid = client.post("/api/curves", json={
        "hdd_thresholds": [30, 40],
        "hdd_pwm": [80, 20],
    }, headers=headers)
    assert invalid.status_code == 400

    valid = client.post("/api/curves", json={
        "hdd": [[30, 20], [40, 70]],
        "ssd": [[35, 20], [55, 100]],
    }, headers=headers)
    assert valid.status_code == 200
    saved = fanbridge.load_config()
    assert saved["hdd_thresholds"] == [30, 40]
    assert saved["hdd_pwm"] == [20, 70]


def test_atomic_configuration_rejects_all_when_one_value_is_invalid():
    client, headers = _authenticated_client()
    before = fanbridge.load_config()
    curves = {
        "hdd_thresholds": before["hdd_thresholds"],
        "hdd_pwm": before["hdd_pwm"],
        "ssd_thresholds": before["ssd_thresholds"],
        "ssd_pwm": before["ssd_pwm"],
    }

    rejected = client.post("/api/config", json={
        "settings": {"poll_interval_seconds": 9, "auto_apply": "false"},
        "curves": curves,
    }, headers=headers)

    assert rejected.status_code == 400
    after_rejection = fanbridge.load_config()
    assert after_rejection["poll_interval_seconds"] == before["poll_interval_seconds"]
    assert after_rejection["auto_apply"] is False

    accepted = client.post("/api/config", json={
        "settings": {"poll_interval_seconds": 9, "auto_apply": False},
        "curves": curves,
    }, headers=headers)
    assert accepted.status_code == 200
    assert fanbridge.load_config()["poll_interval_seconds"] == 9


def test_health_is_read_only(monkeypatch):
    monkeypatch.setattr(fanbridge, "compute_status", lambda: pytest.fail("health actuated control"))
    response = fanbridge.app.test_client().get("/health")
    assert response.status_code == 503
    assert response.get_json()["status"] == "degraded"


def test_valid_yaml_wrong_types_are_normalised_without_enabling_output():
    pathlib.Path(fanbridge.CONFIG_PATH).write_text(
        'controllers: null\nauto_apply: "false"\nfailsafe_pwm: 80\n',
        encoding="utf-8",
    )
    fanbridge._LAST_GOOD_CONFIG = None

    loaded = fanbridge.load_config()

    assert loaded["controllers"] == []
    assert loaded["auto_apply"] is False
    assert loaded["failsafe_pwm"] == 100


def test_status_rejects_stale_cached_control_snapshot():
    client, _headers = _authenticated_client()
    fanbridge._CONTROL_THREAD = SimpleNamespace(is_alive=lambda: True)
    fanbridge._CONTROL_STATE.update({
        "last_attempt_at": int(time.time()) - 120,
        "last_success_at": int(time.time()) - 120,
        "last_error": "control cycle failed",
        "snapshot": {"controllers": [{"telemetry": {"failsafe_active": False}}]},
    })

    response = client.get("/api/status")

    assert response.status_code == 503
    assert response.get_json()["ok"] is False
    assert "snapshot" not in response.get_json()


def test_controller_delete_safe_stops_and_unassigns_its_drives(monkeypatch):
    client, headers = _authenticated_client()
    config = fanbridge.load_config()
    config["controllers"] = [
        {"id": "left", "name": "Left", "type": "diy", "port": "/dev/ttyACM0", "baud": 115200},
        {"id": "right", "name": "Right", "type": "diy", "port": "/dev/ttyACM1", "baud": 115200},
    ]
    config["drive_assignments"] = {"SERIAL-A": "left", "SERIAL-B": "right"}
    fanbridge.save_config(config)
    stopped: list[str] = []
    monkeypatch.setattr(
        fanbridge.serial_svc,
        "safe_stop_controller",
        lambda cid: stopped.append(cid) or {"ok": True, "value": 100},
    )

    response = client.delete("/api/controllers/left", headers=headers)

    assert response.status_code == 200
    assert stopped == ["left"]
    assert fanbridge.load_config()["drive_assignments"] == {
        "SERIAL-A": "none",
        "SERIAL-B": "right",
    }


def test_controller_rename_persists_only_the_name():
    client, headers = _authenticated_client()
    config = fanbridge.load_config()
    config["controllers"] = [
        {"id": "left", "name": "Left", "type": "diy", "port": "/dev/ttyACM0", "baud": 115200},
    ]
    fanbridge.save_config(config)

    response = client.patch(
        "/api/controllers/left",
        json={"name": "Rack Intake"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.get_json()["controller"] == {"id": "left", "name": "Rack Intake"}
    assert fanbridge.load_config()["controllers"] == [{
        "id": "left",
        "name": "Rack Intake",
        "type": "diy",
        "port": "/dev/ttyACM0",
        "baud": 115200,
    }]


def test_controller_rename_rejects_invalid_names():
    client, headers = _authenticated_client()
    config = fanbridge.load_config()
    config["controllers"] = [
        {"id": "left", "name": "Left", "type": "diy", "port": "/dev/ttyACM0", "baud": 115200},
    ]
    fanbridge.save_config(config)

    response = client.patch(
        "/api/controllers/left",
        json={"name": "bad\nname"},
        headers=headers,
    )

    assert response.status_code == 400
    assert fanbridge.load_config()["controllers"][0]["name"] == "Left"


def test_controller_name_is_limited_to_twenty_four_characters():
    client, headers = _authenticated_client()
    config = fanbridge.load_config()
    config["controllers"] = [
        {"id": "left", "name": "Left", "type": "diy", "port": "/dev/ttyACM0", "baud": 115200},
    ]
    fanbridge.save_config(config)

    response = client.patch(
        "/api/controllers/left",
        json={"name": "x" * 25},
        headers=headers,
    )

    assert response.status_code == 400
    assert "1-24" in response.get_json()["error"]
    assert fanbridge.load_config()["controllers"][0]["name"] == "Left"


def test_existing_long_controller_name_is_truncated_without_dropping_controller():
    normalised = fanbridge._normalise_config({
        **fanbridge.DEFAULT_CONFIG,
        "controllers": [{
            "id": "left",
            "name": "Long controller name that previously overflowed",
            "type": "diy",
            "port": "/dev/ttyACM0",
            "baud": 115200,
        }],
    })

    assert normalised["controllers"][0]["name"] == "Long controller name tha"
    assert len(normalised["controllers"][0]["name"]) == 24


def test_graceful_process_exit_safe_stops_every_registered_controller(monkeypatch):
    monkeypatch.setattr(
        fanbridge.serial_svc,
        "list_registered_controllers",
        lambda: [{"id": "left"}, {"id": "right"}],
    )
    stopped: list[str] = []
    monkeypatch.setattr(
        fanbridge.serial_svc,
        "safe_stop_controller",
        lambda cid: stopped.append(cid) or {"ok": True, "value": 100},
    )

    fanbridge._safe_stop_registered_controllers_on_exit()

    assert stopped == ["left", "right"]


def test_controller_creation_does_not_persist_registration_failure(monkeypatch):
    client, headers = _authenticated_client()
    monkeypatch.setattr(fanbridge.serial_svc, "identify_port_details", lambda _port: {
        "type": "diy", "protocol": 1, "board": "pico-dev", "channels": 1,
    })
    monkeypatch.setattr(fanbridge.serial_svc, "register_controller", lambda *_args, **_kwargs: False)

    response = client.post("/api/controllers", json={
        "id": "left",
        "name": "Left",
        "port": "/dev/ttyACM0",
        "baud": 115200,
    }, headers=headers)

    assert response.status_code == 409
    assert fanbridge.load_config()["controllers"] == []


def test_future_six_channel_controller_is_not_persisted_as_diy_compatible(monkeypatch):
    client, headers = _authenticated_client()
    monkeypatch.setattr(fanbridge.serial_svc, "identify_port_details", lambda _port: {
        "type": "official", "protocol": 2, "board": "custom-dev", "channels": 6,
        "hardware_uid": "0011223344556677",
    })

    response = client.post("/api/controllers", json={
        "id": "custom",
        "name": "Six channel",
        "port": "/dev/ttyACM0",
        "baud": 115200,
    }, headers=headers)

    assert response.status_code == 409
    assert "six-channel" in response.get_json()["error"]
    assert fanbridge.load_config()["controllers"] == []


def test_legacy_official_label_migrates_to_existing_diy_product():
    migrated = fanbridge._migrate_config({
        "controllers": [{
            "id": "primary",
            "name": "Existing Pico",
            "type": "official",
            "port": "/dev/ttyACM0",
            "baud": 115200,
        }],
    })

    assert migrated["schema_version"] == 3
    assert migrated["controllers"][0]["type"] == "diy"


def test_schema_two_official_label_remains_reserved_for_six_channel_product():
    migrated = fanbridge._migrate_config({
        "schema_version": 2,
        "controllers": [{
            "id": "custom",
            "name": "Future custom board",
            "type": "official",
            "port": "/dev/ttyACM1",
            "baud": 115200,
        }],
    })

    assert migrated["controllers"][0]["type"] == "official"


def test_controller_creation_persists_detected_hardware_uid(monkeypatch):
    client, headers = _authenticated_client()
    monkeypatch.setattr(fanbridge.serial_svc, "identify_port_details", lambda _port: {
        "type": "diy",
        "protocol": 2,
        "board": "pico-dev",
        "channels": 1,
        "hardware_uid": "A1B2C3D4E5F60718",
        "supported": True,
    })

    response = client.post("/api/controllers", json={
        "id": "left",
        "name": "Left",
        "port": "/dev/ttyACM0",
        "baud": 115200,
    }, headers=headers)

    assert response.status_code == 200
    assert response.get_json()["persistent_identity"] is True
    assert fanbridge.load_config()["controllers"][0]["hardware_uid"] == "a1b2c3d4e5f60718"


def test_port_discovery_presents_diy_controller_with_short_unique_name(monkeypatch):
    client, _headers = _authenticated_client()
    monkeypatch.setattr(fanbridge.serial_svc, "list_serial_ports", lambda: ["/dev/ttyACM9"])
    monkeypatch.setattr(fanbridge.serial_svc, "identify_port_details", lambda _port: {
        "type": "diy",
        "protocol": 2,
        "board": "rp2040-zero",
        "channels": 1,
        "hardware_uid": "a1b2c3d4e5f60718",
        "supported": True,
    })

    response = client.get("/api/ports")
    port = response.get_json()["ports"][0]

    assert response.status_code == 200
    assert port["suggested_name"] == "DIY-RP2040-0718"
    assert port["identify_supported"] is True


def test_pre_enrollment_identify_endpoint_is_fixed_and_bounded(monkeypatch):
    client, headers = _authenticated_client()
    calls: list[tuple[str, set[str]]] = []
    monkeypatch.setattr(
        fanbridge.serial_svc,
        "identify_unregistered_controller",
        lambda port, excluded_hardware_uids: calls.append((port, excluded_hardware_uids)) or {
            "ok": True,
            "port": port,
            "duration_ms": 10000,
            "reply": "IDENTIFYING duration_ms=10000",
        },
    )

    response = client.post(
        "/api/ports/identify",
        json={"port": "/dev/ttyACM9"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.get_json()["duration_ms"] == 10000
    assert calls == [("/dev/ttyACM9", set())]


def test_controller_creation_rejects_duplicate_hardware_uid(monkeypatch):
    client, headers = _authenticated_client()
    config = fanbridge.load_config()
    config["controllers"] = [{
        "id": "left",
        "name": "Left",
        "type": "diy",
        "port": "/dev/ttyACM0",
        "baud": 115200,
        "hardware_uid": "0011223344556677",
    }]
    fanbridge.save_config(config)
    monkeypatch.setattr(fanbridge.serial_svc, "identify_port_details", lambda _port: {
        "type": "diy",
        "protocol": 2,
        "board": "pico-dev",
        "channels": 1,
        "hardware_uid": "0011223344556677",
        "supported": True,
    })

    response = client.post("/api/controllers", json={
        "id": "right",
        "name": "Right",
        "port": "/dev/ttyACM1",
        "baud": 115200,
    }, headers=headers)

    assert response.status_code == 409
    assert [item["id"] for item in fanbridge.load_config()["controllers"]] == ["left"]


def test_protocol_two_handshake_enrolls_existing_controller_without_losing_settings(monkeypatch):
    config = fanbridge.load_config()
    config["controllers"] = [{
        "id": "left",
        "name": "Rack Intake",
        "type": "diy",
        "port": "/dev/ttyACM0",
        "baud": 115200,
    }]
    config["drive_assignments"] = {"SERIAL-A": "left"}
    fanbridge.save_config(config)
    monkeypatch.setattr(
        fanbridge.serial_svc,
        "safe_stop_controller",
        lambda _cid: {"ok": True, "value": 100},
    )

    enrolled = fanbridge._adopt_persistent_controller_identity("left", {
        "type": "diy",
        "protocol": 2,
        "hardware_uid": "A1B2C3D4E5F60718",
    })
    saved = fanbridge.load_config()

    assert enrolled == "a1b2c3d4e5f60718"
    assert saved["controllers"] == [{
        "id": "left",
        "name": "Rack Intake",
        "type": "diy",
        "port": "/dev/ttyACM0",
        "baud": 115200,
        "hardware_uid": "a1b2c3d4e5f60718",
    }]
    assert saved["drive_assignments"] == {"SERIAL-A": "left"}


def test_manual_pwm_api_requires_explicit_maintenance_mode(monkeypatch):
    client, headers = _authenticated_client()
    monkeypatch.delenv("FANBRIDGE_MAINTENANCE_MODE", raising=False)

    response = client.post(
        "/api/serial/pwm",
        json={"cid": "left", "value": 50},
        headers=headers,
    )

    assert response.status_code == 403


def test_manual_pwm_atomically_persists_manual_mode_and_setpoint(monkeypatch):
    client, headers = _authenticated_client()
    monkeypatch.setenv("FANBRIDGE_MAINTENANCE_MODE", "1")
    config = fanbridge.load_config()
    config["controllers"] = [{
        "id": "left",
        "name": "Left",
        "type": "diy",
        "port": "/dev/ttyACM0",
        "baud": 115200,
    }]
    config["auto_apply"] = True
    fanbridge.save_config(config)
    monkeypatch.setattr(
        fanbridge.serial_svc,
        "serial_set_pwm_percent",
        lambda cid, value: {
            "ok": True,
            "reply": f"Set fan to {int(value)}%",
            "value": int(value),
        },
    )
    monkeypatch.setattr(
        fanbridge,
        "_manual_safety_for_snapshot",
        lambda _cid, _value: (False, None),
    )
    transactions = []
    monkeypatch.setattr(
        fanbridge.serial_svc,
        "record_operator_transaction",
        lambda cid, command, result: transactions.append((cid, command, result)),
    )

    response = client.post(
        "/api/serial/pwm",
        json={"cid": "left", "value": 55},
        headers=headers,
    )

    assert response.status_code == 200
    saved = fanbridge.load_config()
    assert saved["auto_apply"] is False
    assert saved["controllers"][0]["control_mode"] == "manual"
    assert saved["controllers"][0]["manual_pwm"] == 55
    assert transactions == [("left", "55", {
        "ok": True,
        "reply": "Set fan to 55%",
        "value": 55,
        "requested_value": 55,
        "safety_override": False,
        "safety_reason": None,
    })]


def test_manual_pwm_forces_safe_output_without_trusted_temperature_state(monkeypatch):
    client, headers = _authenticated_client()
    monkeypatch.setenv("FANBRIDGE_MAINTENANCE_MODE", "1")
    config = fanbridge.load_config()
    config["controllers"] = [{
        "id": "left", "name": "Left", "type": "diy",
        "port": "/dev/ttyACM0", "baud": 115200,
    }]
    fanbridge.save_config(config)
    applied = []
    monkeypatch.setattr(
        fanbridge.serial_svc,
        "serial_set_pwm_percent",
        lambda _cid, value: applied.append(value) or {
            "ok": True, "reply": f"Set fan to {value}%", "value": value,
        },
    )
    monkeypatch.setattr(
        fanbridge.serial_svc,
        "record_operator_transaction",
        lambda *_args: None,
    )

    response = client.post(
        "/api/serial/pwm",
        json={"cid": "left", "value": 0},
        headers=headers,
    )

    assert response.status_code == 200
    assert applied == [100]
    assert response.get_json()["requested_value"] == 0
    assert response.get_json()["value"] == 100
    assert response.get_json()["safety_override"] is True
    assert response.get_json()["safety_reason"] == "control_state_unavailable"


def test_controller_mode_toggle_only_changes_selected_controller():
    client, headers = _authenticated_client()
    config = fanbridge.load_config()
    config["controllers"] = [
        {
            "id": "left", "name": "Left", "type": "diy",
            "port": "/dev/ttyACM0", "baud": 115200,
        },
        {
            "id": "right", "name": "Right", "type": "diy",
            "port": "/dev/ttyACM1", "baud": 115200,
        },
    ]
    config["auto_apply"] = True
    fanbridge.save_config(config)

    response = client.post(
        "/api/auto_apply",
        json={"cid": "left", "enabled": False},
        headers=headers,
    )

    assert response.status_code == 200
    saved = fanbridge.load_config()
    by_id = {item["id"]: item for item in saved["controllers"]}
    assert by_id["left"]["control_mode"] == "manual"
    assert by_id["left"]["manual_pwm"] == 100
    assert "control_mode" not in by_id["right"]
    assert saved["auto_apply"] is True


def test_read_only_serial_diagnostics_do_not_require_maintenance_mode(monkeypatch):
    client, headers = _authenticated_client()
    monkeypatch.delenv("FANBRIDGE_MAINTENANCE_MODE", raising=False)
    monkeypatch.setattr(
        fanbridge.serial_svc,
        "get_serial_status",
        lambda *_args, **_kwargs: {"connected": True, "available": True},
    )
    monkeypatch.setattr(
        fanbridge.serial_svc,
        "serial_send_line",
        lambda *_args, **_kwargs: {"ok": True, "reply": "PONG"},
    )
    transactions = []
    monkeypatch.setattr(
        fanbridge.serial_svc,
        "record_operator_transaction",
        lambda cid, command, result: transactions.append((cid, command, result)),
    )

    response = client.post(
        "/api/serial/send",
        json={"cid": "left", "line": "PING"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.get_json()["reply"] == "PONG"
    assert transactions == [("left", "PING", {"ok": True, "reply": "PONG"})]


def test_maintenance_raw_console_cannot_bypass_verified_pwm_path(monkeypatch):
    client, headers = _authenticated_client()
    monkeypatch.setenv("FANBRIDGE_MAINTENANCE_MODE", "1")

    response = client.post(
        "/api/serial/send",
        json={"cid": "left", "line": "0"},
        headers=headers,
    )

    assert response.status_code == 403
    assert "read-only" in response.get_json()["error"]


def test_serial_apis_do_not_expose_internal_exception_details(monkeypatch):
    client, headers = _authenticated_client()
    sentinel = "SENTINEL stack /private/device/path"
    disconnected = {
        "connected": False,
        "message": sentinel,
        "preferred": "/dev/ttyACM0",
    }
    monkeypatch.setattr(
        fanbridge.serial_svc,
        "get_serial_status",
        lambda *_args, **_kwargs: dict(disconnected),
    )

    status_response = client.get("/api/serial/status?cid=left")
    tools_response = client.get("/api/serial/tools?cid=left")
    assert status_response.status_code == 200
    assert tools_response.status_code == 200
    assert sentinel not in status_response.get_data(as_text=True)
    assert sentinel not in tools_response.get_data(as_text=True)

    monkeypatch.setenv("FANBRIDGE_MAINTENANCE_MODE", "1")
    unavailable = client.post(
        "/api/serial/send",
        json={"cid": "left", "line": "PING"},
        headers=headers,
    )
    assert unavailable.status_code == 409
    assert unavailable.get_json()["error"] == "controller unavailable or identity not verified"
    assert sentinel not in unavailable.get_data(as_text=True)

    monkeypatch.setattr(
        fanbridge.serial_svc,
        "get_serial_status",
        lambda *_args, **_kwargs: {"connected": True, "message": sentinel},
    )
    connected_status = client.get("/api/serial/status?cid=left")
    assert sentinel not in connected_status.get_data(as_text=True)
    monkeypatch.setattr(
        fanbridge.serial_svc,
        "serial_send_line",
        lambda *_args, **_kwargs: {"ok": False, "error": sentinel},
    )
    failed_diagnostic = client.post(
        "/api/serial/send",
        json={"cid": "left", "line": "PING"},
        headers=headers,
    )
    assert failed_diagnostic.status_code == 502
    assert sentinel not in failed_diagnostic.get_data(as_text=True)

    monkeypatch.setattr(
        fanbridge.serial_svc,
        "serial_set_pwm_percent",
        lambda *_args, **_kwargs: {"ok": False, "error": sentinel},
    )
    failed_pwm = client.post(
        "/api/serial/pwm",
        json={"cid": "left", "value": 50},
        headers=headers,
    )
    assert failed_pwm.status_code == 400
    assert sentinel not in failed_pwm.get_data(as_text=True)


def test_log_diagnostics_do_not_command_a_quarantined_legacy_controller(monkeypatch):
    client, _headers = _authenticated_client()
    monkeypatch.setattr(
        fanbridge.serial_svc,
        "get_serial_status",
        lambda *_args, **_kwargs: {
            "connected": False,
            "message": "legacy DIY firmware forced to 100% and quarantined",
        },
    )

    def unexpected_command(*_args, **_kwargs):
        raise AssertionError("quarantined controller received a diagnostic command")

    monkeypatch.setattr(fanbridge.serial_svc, "serial_send_line", unexpected_command)

    response = client.get("/api/logs/download?cid=legacy&format=json")

    assert response.status_code == 200
    assert response.get_json()["diagnostics"]["serial_status"]["connected"] is False


def test_log_api_separates_system_and_controller_scopes():
    client, _headers = _authenticated_client()
    original = list(LOG_RING)
    fixtures = [
        {"id": 900001, "ts": 1, "level": "INFO", "name": "fanbridge", "msg": "FanBridge control loop started"},
        {"id": 900002, "ts": 2, "level": "INFO", "name": "fanbridge", "msg": "controller output acknowledged | cid=left | target=55%"},
        {"id": 900003, "ts": 3, "level": "INFO", "name": "fanbridge", "msg": 'audit | {"controller": "left", "event": "manual_pwm.set"}'},
        {"id": 900004, "ts": 4, "level": "WARNING", "name": "fanbridge", "msg": "serial unavailable | cid=right"},
        {"id": 900005, "ts": 5, "level": "WARNING", "name": "fanbridge", "msg": "serial path reassigned | displaced_cid=left owner_cid=right"},
    ]
    try:
        with LOG_LOCK:
            LOG_RING.clear()
            LOG_RING.extend(fixtures)

        system = client.get("/api/logs?scope=system&min_level=DEBUG")
        assert system.status_code == 200
        system_messages = [item["msg"] for item in system.get_json()["items"]]
        assert system_messages == ["FanBridge control loop started"]

        controller = client.get("/api/logs?scope=controller&cid=left&min_level=DEBUG")
        assert controller.status_code == 200
        controller_messages = [item["msg"] for item in controller.get_json()["items"]]
        assert controller_messages == [fixtures[1]["msg"], fixtures[2]["msg"], fixtures[4]["msg"]]

        download = client.get("/api/logs/download?scope=system&format=json")
        assert download.status_code == 200
        assert [item["msg"] for item in download.get_json()["items"]] == [fixtures[0]["msg"]]
        assert "serial_status" not in download.get_json()["diagnostics"]
    finally:
        with LOG_LOCK:
            LOG_RING.clear()
            LOG_RING.extend(original)


def test_scoped_log_clear_preserves_other_log_streams():
    client, headers = _authenticated_client()
    original = list(LOG_RING)
    fixtures = [
        {"id": 910001, "ts": 1, "level": "INFO", "name": "fanbridge", "msg": "FanBridge service ready"},
        {"id": 910002, "ts": 2, "level": "INFO", "name": "fanbridge", "msg": "operator serial | cid=left | TX PING | RX PONG"},
        {"id": 910003, "ts": 3, "level": "INFO", "name": "fanbridge", "msg": "operator serial | cid=right | TX PING | RX PONG"},
    ]
    try:
        with LOG_LOCK:
            LOG_RING.clear()
            LOG_RING.extend(fixtures)

        cleared = client.post("/api/logs/clear", json={"scope": "system"}, headers=headers)
        assert cleared.status_code == 200
        remaining = client.get("/api/logs?scope=all&min_level=DEBUG").get_json()["items"]
        assert [item["msg"] for item in remaining] == [fixtures[1]["msg"], fixtures[2]["msg"]]

        cleared = client.post(
            "/api/logs/clear",
            json={"scope": "controller", "cid": "left"},
            headers=headers,
        )
        assert cleared.status_code == 200
        remaining = client.get("/api/logs?scope=all&min_level=DEBUG").get_json()["items"]
        assert [item["msg"] for item in remaining] == [fixtures[2]["msg"]]

        invalid = client.get("/api/logs?scope=controller")
        assert invalid.status_code == 400
        invalid = client.post("/api/logs/clear", json={"scope": "unknown"}, headers=headers)
        assert invalid.status_code == 400
    finally:
        with LOG_LOCK:
            LOG_RING.clear()
            LOG_RING.extend(original)


def test_remote_firmware_flash_requires_an_approved_release(monkeypatch):
    monkeypatch.setattr(
        fanbridge,
        "_latest_approved_diy_firmware",
        lambda **_kwargs: (None, None),
    )
    client, headers = _authenticated_client()
    response = client.post("/api/rp/flash", json={"cid": "primary"}, headers=headers)
    assert response.status_code == 404

    config = fanbridge.load_config()
    config["controllers"] = [{
        "id": "primary",
        "name": "Primary",
        "type": "diy",
        "port": "/dev/ttyACM0",
        "baud": 115200,
    }]
    fanbridge.save_config(config)
    response = client.post("/api/rp/flash", json={"cid": "primary"}, headers=headers)
    assert response.status_code == 409
    assert "no hardware-approved" in response.get_json()["error"]


def test_firmware_release_discovery_ignores_unsafe_or_incomplete_assets(monkeypatch):
    releases = [
        {
            "tag_name": "fw-v2.2.0",
            "draft": False,
            "prerelease": False,
            "assets": [{"name": "fanbridge-rp2040-2.2.0.uf2"}],
        },
        {
            "tag_name": "fw-v2.5.3",
            "draft": False,
            "prerelease": False,
            "assets": [
                {"name": "fanbridge-rp2040-2.5.3.uf2"},
                {"name": "fanbridge-rp2040-2.5.3.uf2.sha256"},
            ],
        },
        {
            "tag_name": "fw-v2.6.0",
            "draft": False,
            "prerelease": True,
            "assets": [
                {"name": "fanbridge-rp2040-2.6.0.uf2"},
                {"name": "fanbridge-rp2040-2.6.0.uf2.sha256"},
            ],
        },
    ]
    monkeypatch.setattr(fanbridge, "http_get_json", lambda *_args, **_kwargs: releases)
    release, error = fanbridge._latest_approved_diy_firmware(refresh=True)
    assert error is None
    assert release["version"] == "2.5.3"
    assert release["asset_url"].endswith("/fw-v2.5.3/fanbridge-rp2040-2.5.3.uf2")


def test_remote_firmware_install_verifies_release_checksum_before_flashing(monkeypatch):
    client, headers = _authenticated_client()
    config = fanbridge.load_config()
    config["controllers"] = [{
        "id": "primary",
        "name": "Primary",
        "type": "diy",
        "port": "/dev/ttyACM0",
        "baud": 115200,
    }]
    fanbridge.save_config(config)
    firmware = b"bounded firmware bytes"
    digest = hashlib.sha256(firmware).hexdigest()
    release = {
        "version": "2.5.3",
        "version_tuple": (2, 5, 3),
        "asset": "fanbridge-rp2040-2.5.3.uf2",
        "asset_url": "https://github.com/RoBroLabs/fanbridge/releases/download/fw-v2.5.3/fanbridge-rp2040-2.5.3.uf2",
        "checksum_url": "https://github.com/RoBroLabs/fanbridge/releases/download/fw-v2.5.3/fanbridge-rp2040-2.5.3.uf2.sha256",
    }
    monkeypatch.setattr(
        fanbridge,
        "_latest_approved_diy_firmware",
        lambda **_kwargs: (release, None),
    )
    monkeypatch.setattr(fanbridge, "_firmware_flash_availability", lambda _controller: (True, None))
    monkeypatch.setattr(
        fanbridge,
        "http_get_firmware_asset",
        lambda url, **_kwargs: (
            f"{digest}  {release['asset']}\n".encode("ascii")
            if url.endswith(".sha256") else firmware
        ),
    )
    monkeypatch.setattr(
        fanbridge,
        "_validate_rp2040_uf2",
        lambda _path: (True, "ok", digest),
    )
    flashed = {}

    def fake_flash(cid, _path, actual_digest, **kwargs):
        flashed.update({"cid": cid, "digest": actual_digest, **kwargs})
        return {"ok": True, "verified": True, "controller_version": "2.5.3"}, 200

    monkeypatch.setattr(fanbridge, "_flash_validated_rp2040", fake_flash)
    response = client.post(
        "/api/rp/flash",
        json={"cid": "primary", "version": "2.5.3"},
        headers=headers,
    )
    assert response.status_code == 200
    assert flashed == {
        "cid": "primary",
        "digest": digest,
        "source": "remote",
        "release_version": "2.5.3",
    }


def test_rp2040_uf2_validation_rejects_wrong_family_and_accepts_complete_image(tmp_path):
    def block(family: int) -> bytes:
        value = bytearray(512)
        struct.pack_into(
            "<IIIIIIII",
            value,
            0,
            fanbridge._UF2_MAGIC_START_0,
            fanbridge._UF2_MAGIC_START_1,
            fanbridge._UF2_FLAG_FAMILY_ID,
            0x10000000,
            256,
            0,
            1,
            family,
        )
        struct.pack_into("<I", value, 508, fanbridge._UF2_MAGIC_END)
        return bytes(value)

    valid_path = tmp_path / "valid.uf2"
    valid_path.write_bytes(block(fanbridge._RP2040_FAMILY_ID))
    valid, message, digest = fanbridge._validate_rp2040_uf2(str(valid_path))
    assert valid is True
    assert message == "ok"
    assert digest and len(digest) == 64

    wrong_path = tmp_path / "wrong-family.uf2"
    wrong_path.write_bytes(block(0x12345678))
    valid, message, digest = fanbridge._validate_rp2040_uf2(str(wrong_path))
    assert valid is False
    assert "RP2040" in message
    assert digest is None


def test_corrupt_config_is_not_overwritten_and_last_good_is_retained():
    expected = fanbridge.load_config()
    broken = "controllers: [\n"
    pathlib.Path(fanbridge.CONFIG_PATH).write_text(broken, encoding="utf-8")
    loaded = fanbridge.load_config()
    assert loaded == expected
    assert pathlib.Path(fanbridge.CONFIG_PATH).read_text(encoding="utf-8") == broken


def test_private_state_files_are_mode_0600():
    fanbridge._save_users({"users": {}})
    assert pathlib.Path(fanbridge.CONFIG_PATH).stat().st_mode & 0o777 == 0o600
    assert pathlib.Path(fanbridge.USERS_PATH).stat().st_mode & 0o777 == 0o600
    assert pathlib.Path(os.environ["FANBRIDGE_SECRET_PATH"]).stat().st_mode & 0o777 == 0o600
