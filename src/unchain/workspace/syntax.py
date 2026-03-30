from __future__ import annotations

import shlex
import warnings
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

PARSER_NAME = "tree_sitter"
TREE_KIND = "concrete_syntax_tree"
MAX_TEXT_PREVIEW_CHARS = 120
MAX_SYNTAX_ERRORS = 24

_LANGUAGE_ALIASES = {
    "c#": "c_sharp",
    "c++": "cpp",
    "cc": "cpp",
    "csharp": "c_sharp",
    "golang": "go",
    "js": "javascript",
    "jsx": "javascript",
    "md": "markdown",
    "objc": "objc",
    "objective-c": "objc",
    "objective_c": "objc",
    "py": "python",
    "rb": "ruby",
    "rs": "rust",
    "shell": "bash",
    "sh": "bash",
    "ts": "typescript",
    "yml": "yaml",
    "zsh": "bash",
}

_SUPPORTED_LANGUAGE_NAMES = tuple(
    sorted(
        {
            "bash",
            "c",
            "c_sharp",
            "commonlisp",
            "cpp",
            "css",
            "dockerfile",
            "dot",
            "elixir",
            "elm",
            "embedded_template",
            "erlang",
            "fixed_form_fortran",
            "fortran",
            "go",
            "go_mod",
            "hack",
            "haskell",
            "hcl",
            "html",
            "java",
            "javascript",
            "jsdoc",
            "json",
            "julia",
            "kotlin",
            "lua",
            "make",
            "markdown",
            "objc",
            "ocaml",
            "perl",
            "php",
            "python",
            "ql",
            "r",
            "regex",
            "rst",
            "ruby",
            "rust",
            "scala",
            "sql",
            "sqlite",
            "toml",
            "tsq",
            "tsx",
            "typescript",
            "yaml",
        }
    )
)

_SPECIAL_FILENAME_LANGUAGE_MAP = {
    ".bash_profile": "bash",
    ".bashrc": "bash",
    ".envrc": "bash",
    ".profile": "bash",
    ".zprofile": "bash",
    ".zshrc": "bash",
    "dockerfile": "dockerfile",
    "justfile": "make",
    "makefile": "make",
}

_COMPOUND_SUFFIX_LANGUAGE_MAP = {
    (".d", ".ts"): "typescript",
    (".go", ".mod"): "go_mod",
}

_SUFFIX_LANGUAGE_MAP = {
    ".bash": "bash",
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cs": "c_sharp",
    ".css": "css",
    ".cxx": "cpp",
    ".dot": "dot",
    ".elm": "elm",
    ".erl": "erlang",
    ".ex": "elixir",
    ".exs": "elixir",
    ".go": "go",
    ".h": "c",
    ".hack": "hack",
    ".hcl": "hcl",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".hs": "haskell",
    ".htm": "html",
    ".html": "html",
    ".java": "java",
    ".jl": "julia",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".lua": "lua",
    ".m": "objc",
    ".make": "make",
    ".md": "markdown",
    ".ml": "ocaml",
    ".mli": "ocaml",
    ".mts": "typescript",
    ".php": "php",
    ".pl": "perl",
    ".pm": "perl",
    ".py": "python",
    ".pyi": "python",
    ".pyw": "python",
    ".ql": "ql",
    ".r": "r",
    ".rb": "ruby",
    ".regex": "regex",
    ".rs": "rust",
    ".rst": "rst",
    ".scala": "scala",
    ".scm": "commonlisp",
    ".sh": "bash",
    ".sql": "sql",
    ".sqlite": "sqlite",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".zsh": "bash",
}

_SHEBANG_LANGUAGE_MAP = {
    "ash": "bash",
    "bash": "bash",
    "ksh": "bash",
    "lua": "lua",
    "node": "javascript",
    "nodejs": "javascript",
    "perl": "perl",
    "php": "php",
    "python": "python",
    "python3": "python",
    "ruby": "ruby",
    "sh": "bash",
    "zsh": "bash",
}

_DECLARATION_TYPE_HINTS = (
    "annotation_type",
    "assignment",
    "class",
    "const",
    "constructor",
    "declaration",
    "declarator",
    "definition",
    "enum",
    "function",
    "impl",
    "interface",
    "method",
    "module",
    "namespace",
    "object",
    "protocol",
    "record",
    "service",
    "static",
    "struct",
    "trait",
    "type",
    "var",
)


@dataclass(frozen=True)
class ParsedSyntaxTree:
    language: str
    source_bytes: bytes
    tree: Any
    parser: str = PARSER_NAME
    tree_kind: str = TREE_KIND

    @property
    def root_node(self) -> Any:
        return self.tree.root_node


@dataclass(frozen=True)
class DeclarationCandidate:
    language: str
    type: str
    name: str
    start_line: int
    end_line: int


def supported_languages() -> tuple[str, ...]:
    return _SUPPORTED_LANGUAGE_NAMES


def normalize_language_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    normalized = normalized.replace("-", "_").replace(" ", "_")
    return _LANGUAGE_ALIASES.get(normalized, normalized)


def looks_binary(source_bytes: bytes) -> bool:
    return b"\0" in source_bytes[:4096]


def detect_language(
    path: Path,
    *,
    source_bytes: bytes,
    explicit_language: str | None = None,
) -> str | None:
    normalized_explicit = normalize_language_name(explicit_language)
    if normalized_explicit is not None:
        return normalized_explicit

    name = path.name.lower()
    if name in _SPECIAL_FILENAME_LANGUAGE_MAP:
        return _SPECIAL_FILENAME_LANGUAGE_MAP[name]

    suffixes = tuple(s.lower() for s in path.suffixes)
    for length in range(len(suffixes), 1, -1):
        compound = suffixes[-length:]
        if compound in _COMPOUND_SUFFIX_LANGUAGE_MAP:
            return _COMPOUND_SUFFIX_LANGUAGE_MAP[compound]

    if suffixes:
        language = _SUFFIX_LANGUAGE_MAP.get(suffixes[-1])
        if language is not None:
            return language

    return _detect_shebang_language(source_bytes)


def parse_source_bytes(
    path: Path,
    *,
    source_bytes: bytes,
    language: str | None = None,
) -> ParsedSyntaxTree | None:
    resolved_language = detect_language(path, source_bytes=source_bytes, explicit_language=language)
    if resolved_language is None or looks_binary(source_bytes):
        return None
    if not is_language_supported(resolved_language):
        return None
    parser = _load_parser(resolved_language)
    return ParsedSyntaxTree(
        language=resolved_language,
        source_bytes=source_bytes,
        tree=parser.parse(source_bytes),
    )


def build_syntax_tree_payload(
    path: Path,
    *,
    source_bytes: bytes,
    language: str | None = None,
    max_nodes: int = 400,
) -> dict[str, Any]:
    resolved_language = detect_language(path, source_bytes=source_bytes, explicit_language=language)
    payload: dict[str, Any] = {
        "path": str(path),
        "language": resolved_language,
    }

    if max_nodes < 1:
        payload["error"] = "max_nodes must be >= 1"
        return payload

    if looks_binary(source_bytes):
        payload["error"] = "read_file_ast does not support binary files"
        payload["supported_languages"] = list(supported_languages())
        return payload

    if resolved_language is None:
        payload["error"] = "read_file_ast could not detect a supported language for this file"
        payload["supported_languages"] = list(supported_languages())
        return payload

    if not is_language_supported(resolved_language):
        payload["error"] = "read_file_ast does not support this language"
        payload["supported_languages"] = list(supported_languages())
        return payload

    parsed = parse_source_bytes(path, source_bytes=source_bytes, language=resolved_language)
    if parsed is None:
        payload["error"] = "read_file_ast could not parse this file"
        payload["supported_languages"] = list(supported_languages())
        return payload

    total_nodes = _count_serializable_nodes(parsed.root_node)
    state = {
        "max_nodes": max_nodes,
        "emitted_nodes": 0,
        "truncated": False,
    }
    ast = _serialize_tree_node(parsed.root_node, source_bytes=source_bytes, state=state)
    syntax_errors = _collect_syntax_errors(parsed.root_node, source_bytes=source_bytes)

    payload.update(
        {
            "parser": parsed.parser,
            "tree_kind": parsed.tree_kind,
            "node_count": total_nodes,
            "returned_node_count": state["emitted_nodes"],
            "truncated": state["truncated"] or total_nodes > state["emitted_nodes"],
            "has_syntax_errors": bool(syntax_errors),
            "syntax_errors": syntax_errors,
            "ast": ast,
        }
    )
    return payload


def build_declaration_metadata(
    path: Path,
    *,
    source_bytes: bytes,
    start_line: int,
    end_line: int,
) -> dict[str, Any]:
    parsed = parse_source_bytes(path, source_bytes=source_bytes)
    if parsed is None:
        return _empty_declaration_metadata()

    candidate = select_declaration_for_range(
        collect_declaration_candidates(parsed),
        start_line=start_line,
        end_line=end_line,
    )
    if candidate is None:
        return _empty_declaration_metadata()

    return {
        "declaration_kind": candidate.language,
        "declaration_type": candidate.type,
        "declaration_name": candidate.name,
        "declaration_offset": candidate.start_line - start_line,
        "declaration_end_offset": end_line - candidate.end_line,
    }


def collect_declaration_candidates(parsed: ParsedSyntaxTree) -> list[DeclarationCandidate]:
    candidates: list[DeclarationCandidate] = []
    stack = [parsed.root_node]

    while stack:
        node = stack.pop()
        if not getattr(node, "is_named", False):
            continue

        candidate = _candidate_from_node(node, parsed)
        if candidate is not None:
            candidates.append(candidate)

        named_children = list(getattr(node, "named_children", []))
        stack.extend(reversed(named_children))

    return candidates


def select_declaration_for_range(
    candidates: list[DeclarationCandidate],
    *,
    start_line: int,
    end_line: int,
) -> DeclarationCandidate | None:
    overlapping: list[tuple[tuple[int, int, int], DeclarationCandidate]] = []

    for candidate in candidates:
        contains = candidate.start_line <= start_line and candidate.end_line >= end_line
        overlaps = not (candidate.end_line < start_line or candidate.start_line > end_line)
        if not overlaps:
            continue
        score = (
            0 if contains else 1,
            candidate.end_line - candidate.start_line,
            abs(candidate.start_line - start_line) + abs(candidate.end_line - end_line),
        )
        overlapping.append((score, candidate))

    if not overlapping:
        return None

    overlapping.sort(key=lambda item: item[0])
    return overlapping[0][1]


def find_declaration_by_name(
    parsed: ParsedSyntaxTree,
    *,
    name: str,
    original_start: int,
    original_end: int,
    declaration_type: str | None = None,
) -> DeclarationCandidate | None:
    matching = [candidate for candidate in collect_declaration_candidates(parsed) if candidate.name == name]
    if not matching:
        return None

    scored: list[tuple[tuple[int, int, int], DeclarationCandidate]] = []
    for candidate in matching:
        score = (
            0 if declaration_type and candidate.type == declaration_type else 1,
            abs(candidate.start_line - original_start) + abs(candidate.end_line - original_end),
            candidate.end_line - candidate.start_line,
        )
        scored.append((score, candidate))

    scored.sort(key=lambda item: item[0])
    return scored[0][1]


def is_language_supported(language: str | None) -> bool:
    if language is None:
        return False
    try:
        _load_parser(language)
    except RuntimeError:
        raise
    except Exception:
        return False
    return True


def _empty_declaration_metadata() -> dict[str, Any]:
    return {
        "declaration_kind": None,
        "declaration_type": None,
        "declaration_name": None,
        "declaration_offset": None,
        "declaration_end_offset": None,
    }


def _candidate_from_node(node: Any, parsed: ParsedSyntaxTree) -> DeclarationCandidate | None:
    node_type = str(getattr(node, "type", ""))
    if not _looks_like_declaration_type(node_type):
        return None

    name_node = node.child_by_field_name("name") or node.child_by_field_name("declarator")
    if name_node is None and node_type == "variable_declarator":
        name_node = node.child(0)
    if name_node is None:
        return None

    name = _decode_node_text(name_node, parsed.source_bytes)
    if not name or "\n" in name:
        return None

    start_line = _point_to_line(node.start_point)
    end_line = _end_point_to_line(node.end_point, start_line)
    return DeclarationCandidate(
        language=parsed.language,
        type=node_type,
        name=name,
        start_line=start_line,
        end_line=end_line,
    )


def _looks_like_declaration_type(node_type: str) -> bool:
    return any(hint in node_type for hint in _DECLARATION_TYPE_HINTS)


def _count_serializable_nodes(node: Any) -> int:
    count = 1
    for _, child in _iter_serializable_children(node):
        count += _count_serializable_nodes(child)
    return count


def _serialize_tree_node(
    node: Any,
    *,
    source_bytes: bytes,
    state: dict[str, Any],
) -> dict[str, Any]:
    if state["emitted_nodes"] >= state["max_nodes"]:
        state["truncated"] = True
        return {
            "type": node.type,
            "truncated": True,
        }

    state["emitted_nodes"] += 1
    start_line = _point_to_line(node.start_point)
    data: dict[str, Any] = {
        "type": node.type,
        "named": bool(node.is_named),
        "start_line": start_line,
        "end_line": _end_point_to_line(node.end_point, start_line),
        "start_column": node.start_point[1],
        "end_column": node.end_point[1],
    }
    if node.is_error or node.is_missing or node.type == "ERROR":
        data["error"] = True

    children: list[dict[str, Any]] = []
    for index, child in _iter_serializable_children(node):
        serialized = _serialize_tree_node(child, source_bytes=source_bytes, state=state)
        field_name = node.field_name_for_child(index)
        if field_name is not None:
            serialized["field_name"] = field_name
        children.append(serialized)
        if state["truncated"]:
            break

    if children:
        data["children"] = children
    else:
        text = _preview_text(_decode_node_text(node, source_bytes))
        if text:
            data["text"] = text

    return data


def _collect_syntax_errors(node: Any, *, source_bytes: bytes) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    stack = [node]

    while stack and len(errors) < MAX_SYNTAX_ERRORS:
        current = stack.pop()
        if current.is_error or current.is_missing or current.type == "ERROR":
            start_line = _point_to_line(current.start_point)
            errors.append(
                {
                    "type": current.type,
                    "missing": bool(current.is_missing),
                    "start_line": start_line,
                    "end_line": _end_point_to_line(current.end_point, start_line),
                    "start_column": current.start_point[1],
                    "end_column": current.end_point[1],
                    "text": _preview_text(_decode_node_text(current, source_bytes)),
                }
            )
        children = list(_iter_serializable_children(current))
        for _, child in reversed(children):
            stack.append(child)

    return errors


def _iter_serializable_children(node: Any) -> list[tuple[int, Any]]:
    children: list[tuple[int, Any]] = []
    for index in range(node.child_count):
        child = node.child(index)
        if child is None:
            continue
        if child.is_named or child.is_error or child.is_missing or child.type == "ERROR":
            children.append((index, child))
    return children


def _decode_node_text(node: Any, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace").strip()


def _preview_text(value: str) -> str | None:
    if not value:
        return None
    if len(value) <= MAX_TEXT_PREVIEW_CHARS:
        return value
    return f"{value[:MAX_TEXT_PREVIEW_CHARS]}..."


def _point_to_line(point: tuple[int, int]) -> int:
    return point[0] + 1


def _end_point_to_line(point: tuple[int, int], fallback: int) -> int:
    line = point[0] + 1
    if point[1] == 0 and line > fallback:
        return line - 1
    return max(fallback, line)


def _detect_shebang_language(source_bytes: bytes) -> str | None:
    if not source_bytes.startswith(b"#!"):
        return None

    first_line = source_bytes.splitlines()[0].decode("utf-8", errors="ignore")
    try:
        tokens = shlex.split(first_line[2:].strip())
    except ValueError:
        return None

    if not tokens:
        return None

    executable = Path(tokens[0]).name
    args = list(tokens[1:])
    if executable == "env":
        filtered = [token for token in args if not token.startswith("-")]
        if not filtered:
            return None
        executable = Path(filtered[0]).name

    return _SHEBANG_LANGUAGE_MAP.get(executable)


@lru_cache(maxsize=128)
def _load_parser(language: str) -> Any:
    try:
        from tree_sitter_languages import get_parser
    except ImportError as exc:  # pragma: no cover - dependency missing is an environment issue
        raise RuntimeError("tree-sitter dependencies are not installed") from exc

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        return get_parser(language)


__all__ = [
    "DeclarationCandidate",
    "PARSER_NAME",
    "ParsedSyntaxTree",
    "TREE_KIND",
    "build_declaration_metadata",
    "build_syntax_tree_payload",
    "collect_declaration_candidates",
    "detect_language",
    "find_declaration_by_name",
    "is_language_supported",
    "looks_binary",
    "normalize_language_name",
    "parse_source_bytes",
    "select_declaration_for_range",
    "supported_languages",
]
