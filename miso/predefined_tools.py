import os
import re
import shutil
import subprocess
import venv
from pathlib import Path
from typing import Any

from .tool import LLM_tool, LLM_toolkit

class _PythonRuntime:
    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()
        self.runtime_dir = self.workspace_root / ".miso_python_runtime"

    @property
    def python_bin(self) -> Path:
        if os.name == "nt":
            return self.runtime_dir / "Scripts" / "python.exe"
        return self.runtime_dir / "bin" / "python"

    def ensure(self, reset: bool = False) -> dict[str, Any]:
        if reset and self.runtime_dir.exists():
            shutil.rmtree(self.runtime_dir)

        if not self.python_bin.exists():
            builder = venv.EnvBuilder(with_pip=True, clear=False)
            builder.create(str(self.runtime_dir))

        return {
            "runtime_dir": str(self.runtime_dir),
            "python_bin": str(self.python_bin),
            "created": self.python_bin.exists(),
        }

    def install(self, packages: list[str], timeout_seconds: int = 180) -> dict[str, Any]:
        if not packages:
            return {"error": "packages is required"}

        self.ensure()

        command = [str(self.python_bin), "-m", "pip", "install", *packages]
        completed = subprocess.run(
            command,
            cwd=str(self.workspace_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

    def run_code(self, code: str, timeout_seconds: int = 30) -> dict[str, Any]:
        if not code:
            return {"error": "code is required"}

        self.ensure()

        command = [str(self.python_bin), "-c", code]
        completed = subprocess.run(
            command,
            cwd=str(self.workspace_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

    def reset(self) -> dict[str, Any]:
        if self.runtime_dir.exists():
            shutil.rmtree(self.runtime_dir)
            return {"reset": True, "runtime_dir": str(self.runtime_dir)}
        return {"reset": False, "runtime_dir": str(self.runtime_dir)}

class LLM_predefined_toolkit(LLM_toolkit):
    """Dedicated toolkit that bundles predefined tools."""

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        include_python_runtime: bool = True,
    ):
        super().__init__()
        self.workspace_root = Path(workspace_root or os.getcwd()).resolve()
        self.python_runtime = _PythonRuntime(self.workspace_root)

        self._register_core_tools()
        if include_python_runtime:
            self._register_python_runtime_tools()

    def _resolve_workspace_path(self, path: str) -> Path:
        path_obj = Path(path)
        if path_obj.is_absolute():
            resolved = path_obj.resolve()
        else:
            resolved = (self.workspace_root / path_obj).resolve()

        try:
            resolved.relative_to(self.workspace_root)
        except ValueError:
            raise ValueError("path is outside workspace_root")

        return resolved

    def _register_core_tools(self):
        self.register(
            LLM_tool.from_callable(
                self.read_text_file,
                name="read_text_file",
                description="Read UTF-8 text file from workspace.",
            )
        )
        self.register(
            LLM_tool.from_callable(
                self.write_text_file,
                name="write_text_file",
                description="Write UTF-8 text file into workspace.",
            )
        )
        self.register(
            LLM_tool.from_callable(
                self.list_directory,
                name="list_directory",
                description="List files and folders under a workspace path.",
            )
        )
        self.register(
            LLM_tool.from_callable(
                self.search_text,
                name="search_text",
                description="Search text pattern in workspace files.",
                observe=True,
            )
        )

    def _register_python_runtime_tools(self):
        self.register(
            LLM_tool.from_callable(
                self.python_runtime_init,
                name="python_runtime_init",
                description="Create isolated Python runtime (venv) in workspace.",
            )
        )
        self.register(
            LLM_tool.from_callable(
                self.python_runtime_install,
                name="python_runtime_install",
                description="Install Python packages into isolated runtime via pip.",
                observe=True,
            )
        )
        self.register(
            LLM_tool.from_callable(
                self.python_runtime_run,
                name="python_runtime_run",
                description="Run Python code inside isolated runtime.",
                observe=True,
            )
        )
        self.register(
            LLM_tool.from_callable(
                self.python_runtime_reset,
                name="python_runtime_reset",
                description="Delete and reset isolated Python runtime.",
            )
        )

    def read_text_file(self, path: str, max_chars: int = 20000) -> dict[str, Any]:
        target = self._resolve_workspace_path(path)
        if not target.exists():
            return {"error": f"file not found: {target}"}
        if not target.is_file():
            return {"error": f"not a file: {target}"}

        content = target.read_text(encoding="utf-8", errors="replace")
        truncated = len(content) > max_chars
        if truncated:
            content = content[:max_chars]

        return {
            "path": str(target),
            "content": content,
            "truncated": truncated,
        }

    def write_text_file(self, path: str, content: str, append: bool = False) -> dict[str, Any]:
        target = self._resolve_workspace_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        mode = "a" if append else "w"
        with target.open(mode, encoding="utf-8") as fp:
            fp.write(content)

        return {
            "path": str(target),
            "bytes_written": len(content.encode("utf-8")),
            "append": append,
        }

    def list_directory(self, path: str = ".", recursive: bool = False, max_entries: int = 200) -> dict[str, Any]:
        target = self._resolve_workspace_path(path)
        if not target.exists():
            return {"error": f"path not found: {target}"}

        entries: list[str] = []
        iterator = target.rglob("*") if recursive else target.iterdir()

        for entry in iterator:
            rel = entry.relative_to(self.workspace_root)
            suffix = "/" if entry.is_dir() else ""
            entries.append(f"{rel}{suffix}")
            if len(entries) >= max_entries:
                break

        return {
            "path": str(target),
            "entries": entries,
            "truncated": len(entries) >= max_entries,
        }

    def search_text(
        self,
        pattern: str,
        path: str = ".",
        max_results: int = 100,
        case_sensitive: bool = False,
    ) -> dict[str, Any]:
        if not pattern:
            return {"error": "pattern is required"}

        target = self._resolve_workspace_path(path)
        if not target.exists():
            return {"error": f"path not found: {target}"}

        flags = 0 if case_sensitive else re.IGNORECASE
        compiled = re.compile(pattern, flags)

        root_iter = [target] if target.is_file() else list(target.rglob("*"))
        results: list[dict[str, Any]] = []

        for file_path in root_iter:
            if not file_path.is_file():
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            for line_number, line in enumerate(text.splitlines(), start=1):
                if compiled.search(line):
                    results.append(
                        {
                            "path": str(file_path.relative_to(self.workspace_root)),
                            "line": line_number,
                            "text": line,
                        }
                    )
                    if len(results) >= max_results:
                        return {
                            "path": str(target),
                            "pattern": pattern,
                            "matches": results,
                            "truncated": True,
                        }

        return {
            "path": str(target),
            "pattern": pattern,
            "matches": results,
            "truncated": False,
        }

    def python_runtime_init(self, reset: bool = False) -> dict[str, Any]:
        return self.python_runtime.ensure(reset=reset)

    def python_runtime_install(self, packages: list[str], timeout_seconds: int = 180) -> dict[str, Any]:
        return self.python_runtime.install(packages=packages, timeout_seconds=timeout_seconds)

    def python_runtime_run(self, code: str, timeout_seconds: int = 30) -> dict[str, Any]:
        return self.python_runtime.run_code(code=code, timeout_seconds=timeout_seconds)

    def python_runtime_reset(self) -> dict[str, Any]:
        return self.python_runtime.reset()

def build_predefined_toolkit(
    *,
    workspace_root: str | Path | None = None,
    include_python_runtime: bool = True,
) -> LLM_predefined_toolkit:
    return LLM_predefined_toolkit(
        workspace_root=workspace_root,
        include_python_runtime=include_python_runtime,
    )

__all__ = [
    "LLM_predefined_toolkit",
    "build_predefined_toolkit",
]
