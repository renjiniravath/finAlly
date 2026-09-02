"""Ensures `market_data.massive_source` is importable even when the `massive` PyPI package
isn't installed in the test environment, by installing a minimal stand-in before collection.
If the real package is present, this is a no-op."""

import sys
import types

try:
    import massive  # noqa: F401
except ImportError:
    massive_module = types.ModuleType("massive")

    class RESTClient:
        def __init__(self, api_key: str | None = None) -> None:
            self.api_key = api_key

        def get_snapshot_all(self, market_type: str = "stocks", tickers: list[str] | None = None):
            raise NotImplementedError("stub RESTClient — patch get_snapshot_all in tests")

    exceptions_module = types.ModuleType("massive.exceptions")

    class AuthError(Exception):
        pass

    class BadResponse(Exception):
        pass

    exceptions_module.AuthError = AuthError
    exceptions_module.BadResponse = BadResponse

    massive_module.RESTClient = RESTClient
    massive_module.exceptions = exceptions_module

    sys.modules["massive"] = massive_module
    sys.modules["massive.exceptions"] = exceptions_module
