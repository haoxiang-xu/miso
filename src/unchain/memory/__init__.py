from __future__ import annotations

import importlib

__all__ = [
    "BaseMemoryHarness",
    "ContextStrategy",
    "HybridContextStrategy",
    "InMemorySessionStore",
    "JsonFileLongTermProfileStore",
    "KernelMemoryRuntime",
    "LastNTurnsStrategy",
    "LongTermExtractor",
    "LongTermMemoryConfig",
    "LongTermProfileStore",
    "LongTermRecallMemoryHarness",
    "LongTermVectorAdapter",
    "MemoryBootstrapHarness",
    "MemoryCommitHarness",
    "MemoryConfig",
    "MemoryContext",
    "MemoryHarness",
    "MemoryManager",
    "SessionStore",
    "ShortTermRecallMemoryHarness",
    "SummaryGenerator",
    "SummaryTokenStrategy",
    "VectorStoreAdapter",
]

_EXPORT_TO_MODULE = {
    "LongTermMemoryConfig": ".config",
    "MemoryConfig": ".config",
    "BaseMemoryHarness": ".base",
    "MemoryContext": ".base",
    "MemoryHarness": ".base",
    "MemoryBootstrapHarness": ".bootstrap",
    "MemoryCommitHarness": ".commit",
    "JsonFileLongTermProfileStore": ".long_term",
    "LongTermExtractor": ".long_term",
    "LongTermProfileStore": ".long_term",
    "LongTermVectorAdapter": ".long_term",
    "MemoryManager": ".manager",
    "SummaryGenerator": ".manager",
    "LongTermRecallMemoryHarness": ".recall_long_term",
    "KernelMemoryRuntime": ".runtime",
    "ShortTermRecallMemoryHarness": ".short_term",
    "InMemorySessionStore": ".stores",
    "SessionStore": ".stores",
    "VectorStoreAdapter": ".stores",
    "ContextStrategy": ".strategies",
    "HybridContextStrategy": ".strategies",
    "LastNTurnsStrategy": ".strategies",
    "SummaryTokenStrategy": ".strategies",
}


def __getattr__(name: str):
    module_name = _EXPORT_TO_MODULE.get(name)
    if module_name is None:
        raise AttributeError(name)
    module = importlib.import_module(module_name, __name__)
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
