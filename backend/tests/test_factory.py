import pytest

from market_data.factory import get_market_data_source
from market_data.massive_source import MassiveSource
from market_data.simulator import SimulatorSource


def test_no_api_key_selects_simulator(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    source = get_market_data_source()
    assert isinstance(source, SimulatorSource)


def test_empty_api_key_selects_simulator(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "")
    source = get_market_data_source()
    assert isinstance(source, SimulatorSource)


def test_whitespace_only_api_key_selects_simulator(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "   ")
    source = get_market_data_source()
    assert isinstance(source, SimulatorSource)


def test_api_key_selects_massive_source(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "test-key-123")
    monkeypatch.delenv("MASSIVE_POLL_INTERVAL_SECONDS", raising=False)
    source = get_market_data_source()
    assert isinstance(source, MassiveSource)
    assert source._poll_interval == 15.0


def test_custom_poll_interval_is_read_from_env(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "test-key-123")
    monkeypatch.setenv("MASSIVE_POLL_INTERVAL_SECONDS", "5")
    source = get_market_data_source()
    assert isinstance(source, MassiveSource)
    assert source._poll_interval == 5.0
