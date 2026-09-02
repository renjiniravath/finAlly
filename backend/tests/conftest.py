"""Ensures `market_data.massive_source` is importable in test environments where the `massive`
PyPI package isn't installed, by injecting a minimal stand-in module before collection. Production
code always uses the real `massive` package (declared in pyproject.toml); this shim only backstops
test collection and is fully overridden by mocks within each test.
"""
import sys
import types

if "massive" not in sys.modules:
    massive_module = types.ModuleType("massive")

    class RESTClient:  # pragma: no cover - replaced by mocks in tests
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def get_snapshot_all(self, market_type: str, tickers: list[str]):
            raise NotImplementedError

    massive_module.RESTClient = RESTClient

    exceptions_module = types.ModuleType("massive.exceptions")

    class AuthError(Exception):
        pass

    class BadResponse(Exception):
        pass

    exceptions_module.AuthError = AuthError
    exceptions_module.BadResponse = BadResponse

    massive_module.exceptions = exceptions_module

    sys.modules["massive"] = massive_module
    sys.modules["massive.exceptions"] = exceptions_module
