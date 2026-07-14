import os
import pathlib
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash


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
from core.http import _allowed_api_url  # noqa: E402


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


def test_first_run_requires_csrf_setup_token_and_strong_password():
    client = fanbridge.app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]

    weak = client.post("/login", data={
        "csrf_token": csrf,
        "setup_token": "test-bootstrap-token-123",
        "username": "admin",
        "password": "short",
        "confirm": "short",
    })
    assert weak.status_code == 200
    assert not fanbridge._load_users().get("users")

    wrong_token = client.post("/login", data={
        "csrf_token": csrf,
        "setup_token": "wrong",
        "username": "admin",
        "password": "correct-horse-battery",
        "confirm": "correct-horse-battery",
    })
    assert wrong_token.status_code == 403

    created = client.post("/login", data={
        "csrf_token": csrf,
        "setup_token": "test-bootstrap-token-123",
        "username": "admin",
        "password": "correct-horse-battery",
        "confirm": "correct-horse-battery",
    })
    assert created.status_code == 302
    assert created.headers["Location"].endswith("/")


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


def test_firmware_flash_is_disabled_by_default():
    client, headers = _authenticated_client()
    response = client.post("/api/rp/flash", json={"cid": "primary"}, headers=headers)
    assert response.status_code == 403
    assert "disabled" in response.get_json()["error"]


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
