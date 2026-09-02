import sys
import types


def _install_massive_stub() -> None:
    """Provide a minimal stand-in for the `massive` package so the test suite can still be
    collected in environments where `uv sync` couldn't resolve it from PyPI. Real behavior is
    always exercised through mocks/monkeypatching in test_massive_source.py, never this stub's
    bodies — this only needs to satisfy `from massive import RESTClient` at import time."""
    massive_module = types.ModuleType("massive")

    class RESTClient:  # pragma: no cover - replaced by mocks in tests
        def __init__(self, api_key=None):
            self.api_key = api_key

        def get_snapshot_all(self, market_type="stocks", tickers=None):
            raise NotImplementedError("stub RESTClient; tests must mock this method")

    massive_module.RESTClient = RESTClient

    exceptions_module = types.ModuleType("massive.exceptions")

    class BadResponse(Exception):
        pass

    class AuthError(Exception):
        pass

    exceptions_module.BadResponse = BadResponse
    exceptions_module.AuthError = AuthError
    massive_module.exceptions = exceptions_module

    sys.modules["massive"] = massive_module
    sys.modules["massive.exceptions"] = exceptions_module


try:
    import massive  # noqa: F401
except ImportError:
    _install_massive_stub()
