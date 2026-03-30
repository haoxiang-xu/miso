from .payloads import (
    DEFAULT_PAYLOADS_RESOURCE,
    MODEL_CAPABILITIES_RESOURCE,
    load_default_payloads,
    load_model_capabilities,
)


def __getattr__(name):
    # Lazy import Broth and friends from miso.runtime.engine to avoid circular imports
    _BROTH_NAMES = {"Broth", "ProviderTurnResult", "BrothToolCall", "ToolExecutionOutcome", "providers"}
    if name in _BROTH_NAMES:
        if name == "providers":
            try:
                from miso.runtime import providers
                return providers
            except ImportError:
                raise AttributeError(name)
        try:
            import miso.runtime.engine as _engine
            if name == "BrothToolCall":
                return getattr(_engine, "ToolCall", None)
            return getattr(_engine, name)
        except (ImportError, AttributeError):
            raise AttributeError(name)
    raise AttributeError(name)


__all__ = [
    "Broth",
    "ProviderTurnResult",
    "BrothToolCall",
    "ToolExecutionOutcome",
    "DEFAULT_PAYLOADS_RESOURCE",
    "MODEL_CAPABILITIES_RESOURCE",
    "load_default_payloads",
    "load_model_capabilities",
]
