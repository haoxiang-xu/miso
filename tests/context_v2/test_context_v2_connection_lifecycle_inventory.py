"""Every opener of context_v2.sqlite3 must run inside the database mutex.

A raw connection is "born" one of two ways in this codebase: a direct
``sqlite3.connect(...)`` call, or a call to a private ``_connect`` helper
(``self._connect()`` or a module-level ``_connect(path)``) that wraps
``sqlite3.connect`` once and is reused by several methods.  The helper's own
*definition* is not required to sit inside a ``with`` block -- it has no
opinion about serialization -- but every *call site* that acquires a
connection, whether directly or through such a helper, must be lexically
inside ``serialized_context_v2_database_access`` or
``existing_context_v2_readonly_connection`` so the open, use, and close of
that connection cannot interleave with any other lifecycle participant.
"""

import ast
from pathlib import Path

PERSISTENCE = Path(__file__).resolve().parents[2] / "src" / "unchain" / "persistence"
CONTEXT_DB_MODULES = (
    "sqlite_v2.py",
    "sqlite_context_compiler_v2.py",
    "sqlite_generation_lifecycle_v2.py",
    "sqlite_legacy_bootstrap_v2.py",
    "sqlite_chat_deletion_v2.py",
    "sqlite_context_memory_bootstrap_v2.py",
    "sqlite_read_v2.py",
)
GUARDS = {
    "serialized_context_v2_database_access",
    "_serialized_context_v2_database_access",
    "existing_context_v2_readonly_connection",
}


def _guarded(node: ast.AST, parents: dict) -> bool:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.With):
            for item in current.items:
                call = item.context_expr
                func = getattr(call, "func", None)
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name in GUARDS:
                    return True
    return False


def _enclosing_function_name(node: ast.AST, parents: dict) -> str | None:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return None


def _find_unguarded_connections(module: str) -> list[str]:
    tree = ast.parse((PERSISTENCE / module).read_text())
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    unguarded = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_raw_connect = (
            isinstance(func, ast.Attribute)
            and func.attr == "connect"
            and getattr(func.value, "id", None) == "sqlite3"
        )
        is_connect_helper_call = (
            (isinstance(func, ast.Name) and func.id == "_connect")
            or (isinstance(func, ast.Attribute) and func.attr == "_connect")
        )
        if not (is_raw_connect or is_connect_helper_call):
            continue
        if is_raw_connect and _enclosing_function_name(node, parents) == "_connect":
            # This is the primitive's own definition; its callers are the
            # ones that must be guarded, checked as is_connect_helper_call.
            continue
        if not _guarded(node, parents):
            unguarded.append(f"{module}:{node.lineno}")
    return unguarded


def test_every_context_v2_connect_is_inside_the_mutex():
    unguarded: list[str] = []
    for module in CONTEXT_DB_MODULES:
        unguarded.extend(_find_unguarded_connections(module))
    assert not unguarded, f"unguarded Context V2 connects: {unguarded}"
