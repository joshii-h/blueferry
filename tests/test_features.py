"""Feature switches in settings.json: allowlist, precedence, GetFeatures/SetFeature."""
from __future__ import annotations

import json
import os

import pytest

from blueferry import config, features
from blueferry.backend_operations import BackendDependencies, BackendOperations
from blueferry.errors import InvalidArgumentsError, NotReadyError
from blueferry.features import FEATURES, FeatureSettings
from blueferry.settings_store import SettingsStore


def test_allowlist_is_a_subset_of_local_env_and_matches_the_table() -> None:
    assert {feature.variable for feature in FEATURES} == set(config.FEATURE_ENV_KEYS)
    assert config.FEATURE_ENV_KEYS <= config.LOCAL_ENV_KEYS
    assert set(features.running_values()) == set(features.BY_NAME)
    # Pairing target and adapter are never switchable from a client.
    assert not {"BLUEFERRY_MAC", "BLUEFERRY_ADAPTER", "BLUEFERRY_ANCS_ENABLED"} & set(
        config.FEATURE_ENV_KEYS)


def _settings(path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def test_only_allowlisted_booleans_are_read(tmp_path) -> None:
    path = tmp_path / "settings.json"
    _settings(path, {"features": {
        "BLUEFERRY_CALLS_ENABLED": True, "BLUEFERRY_MAC": True,
        "BLUEFERRY_OTP_AUTOCOPY": "yes", "BLUEFERRY_CONTACT_PHOTOS": False,
    }})
    assert config.read_feature_settings(path) == {
        "BLUEFERRY_CALLS_ENABLED": True, "BLUEFERRY_CONTACT_PHOTOS": False,
    }
    _settings(path, {"features": ["x"]})
    assert config.read_feature_settings(path) == {}
    assert config.read_feature_settings(tmp_path / "missing.json") == {}


def test_precedence_environment_then_settings_then_local_env(tmp_path, monkeypatch) -> None:
    directory = tmp_path / "blueferry"
    _settings(directory / "settings.json", {"features": {
        "BLUEFERRY_CALLS_ENABLED": True, "BLUEFERRY_CONTACT_PHOTOS": True,
        "BLUEFERRY_OTP_AUTOCOPY": False,
    }})
    local = directory / "local.env"
    local.write_text("BLUEFERRY_CALLS_ENABLED=false\nBLUEFERRY_OTP_AUTOCOPY=true\n"
                     "BLUEFERRY_ANCS_ACTIONS=true\n")
    local.chmod(0o600)
    monkeypatch.setattr(config, "CONFIG_DIR", directory)
    monkeypatch.setattr(config, "LOCAL_ENV_PATH", local)
    monkeypatch.setattr(config, "EXPLICIT_ENV_KEYS", frozenset({"BLUEFERRY_CONTACT_PHOTOS"}))
    monkeypatch.setattr(config, "SETTINGS_FEATURE_KEYS", frozenset())
    for key in ("BLUEFERRY_CALLS_ENABLED", "BLUEFERRY_OTP_AUTOCOPY", "BLUEFERRY_ANCS_ACTIONS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BLUEFERRY_CONTACT_PHOTOS", "false")
    config._load_local_env()
    assert os.environ["BLUEFERRY_CALLS_ENABLED"] == "true"      # settings over local.env
    assert os.environ["BLUEFERRY_OTP_AUTOCOPY"] == "false"
    assert os.environ["BLUEFERRY_ANCS_ACTIONS"] == "true"       # local.env alone
    assert os.environ["BLUEFERRY_CONTACT_PHOTOS"] == "false"    # explicit env wins
    assert config.SETTINGS_FEATURE_KEYS == {"BLUEFERRY_CALLS_ENABLED", "BLUEFERRY_OTP_AUTOCOPY"}


def _running(**overrides):
    values = {feature.name: feature.default for feature in FEATURES}
    values.update(overrides)
    return lambda: values


def test_snapshot_and_set_report_source_and_restart(tmp_path) -> None:
    path = tmp_path / "config" / "settings.json"
    store = FeatureSettings(
        path, running=_running(otp_autocopy=True),
        explicit=frozenset({"BLUEFERRY_CONTACT_PHOTOS"}),
        local_env=lambda: {"BLUEFERRY_OTP_AUTOCOPY": "true"},
    )
    snapshot = store.snapshot()
    assert snapshot["calls_enabled"] == {
        "value": False, "running": False, "source": "default",
        "variable": "BLUEFERRY_CALLS_ENABLED", "restart_required": False,
    }
    assert snapshot["otp_autocopy"]["source"] == "local.env"
    assert snapshot["contact_photos"]["source"] == "environment"
    assert store.set("calls_enabled", True) == "restart-required"
    assert store.set("otp_autocopy", True) == "active"
    assert store.set("contact_photos", True) == "environment"
    snapshot = store.snapshot()
    assert snapshot["calls_enabled"]["value"] is True
    assert snapshot["calls_enabled"]["restart_required"] is True
    assert snapshot["calls_enabled"]["source"] == "settings"
    assert snapshot["contact_photos"]["value"] is False  # still the environment's
    assert SettingsStore(path).read()["features"]["BLUEFERRY_CALLS_ENABLED"] is True
    with pytest.raises(KeyError):
        store.set("mac", True)
    # MPRIS needs media control; without it nothing changes on restart.
    store.set("media_mpris_enabled", True)
    assert store.snapshot()["media_mpris_enabled"]["restart_required"] is False
    store.set("media_control_enabled", True)
    assert store.snapshot()["media_mpris_enabled"]["restart_required"] is True


def test_backend_operations_validate(tmp_path) -> None:
    operations = BackendOperations(object())  # type: ignore[arg-type]
    with pytest.raises(NotReadyError):
        operations.get_features()
    operations = BackendOperations(object(), BackendDependencies(  # type: ignore[arg-type]
        features=FeatureSettings(tmp_path / "s.json", running=_running(),
                                 explicit=frozenset(), local_env=dict),
    ))
    with pytest.raises(InvalidArgumentsError):
        operations.set_feature("BLUEFERRY_MAC", True)
    assert operations.set_feature("ancs_actions", True) == "restart-required"
    assert operations.get_features()["ancs_actions"]["value"] is True
