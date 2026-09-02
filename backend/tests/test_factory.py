from unittest.mock import patch

from market_data.factory import get_market_data_source
from market_data.massive_source import MassiveSource
from market_data.simulator import SimulatorSource


def test_returns_simulator_when_api_key_unset(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    assert isinstance(get_market_data_source(), SimulatorSource)


def test_returns_simulator_when_api_key_empty(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "")
    assert isinstance(get_market_data_source(), SimulatorSource)


def test_returns_simulator_when_api_key_whitespace_only(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "   ")
    assert isinstance(get_market_data_source(), SimulatorSource)


@patch("market_data.massive_source.RESTClient")
def test_returns_massive_when_api_key_set(mock_rest_client, monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "test-key")
    source = get_market_data_source()
    assert isinstance(source, MassiveSource)


@patch("market_data.massive_source.RESTClient")
def test_default_poll_interval_is_15_seconds(mock_rest_client, monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "test-key")
    monkeypatch.delenv("MASSIVE_POLL_INTERVAL_SECONDS", raising=False)
    source = get_market_data_source()
    assert source._poll_interval == 15.0


@patch("market_data.massive_source.RESTClient")
def test_poll_interval_is_configurable(mock_rest_client, monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "test-key")
    monkeypatch.setenv("MASSIVE_POLL_INTERVAL_SECONDS", "5")
    source = get_market_data_source()
    assert source._poll_interval == 5.0
