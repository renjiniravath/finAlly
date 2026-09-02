from unittest.mock import MagicMock

import pytest

from market_data.factory import get_market_data_source
from market_data.massive_source import MassiveSource
from market_data.simulator import SimulatorSource


@pytest.fixture(autouse=True)
def stub_rest_client(monkeypatch):
    """Every test in this module may construct a MassiveSource; keep the real network
    client out of it regardless of whether that happens."""
    monkeypatch.setattr("market_data.massive_source.RESTClient", MagicMock())


def test_returns_simulator_when_api_key_unset(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    source = get_market_data_source()
    assert isinstance(source, SimulatorSource)


def test_returns_simulator_when_api_key_empty_string(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "")
    source = get_market_data_source()
    assert isinstance(source, SimulatorSource)


def test_returns_simulator_when_api_key_only_whitespace(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "   ")
    source = get_market_data_source()
    assert isinstance(source, SimulatorSource)


def test_returns_massive_source_when_api_key_set(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "secret-key")
    source = get_market_data_source()
    assert isinstance(source, MassiveSource)


def test_massive_source_uses_default_poll_interval_when_unset(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "secret-key")
    monkeypatch.delenv("MASSIVE_POLL_INTERVAL_SECONDS", raising=False)
    source = get_market_data_source()
    assert source._poll_interval == 15.0


def test_massive_source_respects_poll_interval_env_var(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "secret-key")
    monkeypatch.setenv("MASSIVE_POLL_INTERVAL_SECONDS", "5")
    source = get_market_data_source()
    assert source._poll_interval == 5.0
