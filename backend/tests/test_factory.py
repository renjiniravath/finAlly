from market_data import massive_source as massive_source_module
from market_data.factory import get_market_data_source
from market_data.massive_source import DEFAULT_POLL_INTERVAL_SECONDS, MassiveSource
from market_data.simulator import SimulatorSource


def test_returns_simulator_when_key_unset(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    source = get_market_data_source()
    assert isinstance(source, SimulatorSource)


def test_returns_simulator_when_key_empty(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "")
    source = get_market_data_source()
    assert isinstance(source, SimulatorSource)


def test_returns_simulator_when_key_only_whitespace(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "   ")
    source = get_market_data_source()
    assert isinstance(source, SimulatorSource)


def test_returns_massive_when_key_set(monkeypatch):
    monkeypatch.setattr(massive_source_module, "RESTClient", lambda api_key=None: object())
    monkeypatch.setenv("MASSIVE_API_KEY", "secret-key")
    monkeypatch.delenv("MASSIVE_POLL_INTERVAL_SECONDS", raising=False)

    source = get_market_data_source()

    assert isinstance(source, MassiveSource)
    assert source._poll_interval == DEFAULT_POLL_INTERVAL_SECONDS


def test_massive_uses_custom_poll_interval(monkeypatch):
    monkeypatch.setattr(massive_source_module, "RESTClient", lambda api_key=None: object())
    monkeypatch.setenv("MASSIVE_API_KEY", "secret-key")
    monkeypatch.setenv("MASSIVE_POLL_INTERVAL_SECONDS", "5")

    source = get_market_data_source()

    assert isinstance(source, MassiveSource)
    assert source._poll_interval == 5.0
