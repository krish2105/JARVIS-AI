"""Client-side implementation of Anthropic's memory tool file operations,
repurposed as a plain local tool for the local-LLM tool loop (src/brain/agent.py).

Claude's original memory tool spec (view/create/str_replace/insert/delete/
rename against a virtual /memories path) is a good design regardless of
which model is doing the calling, so we keep the same command set and
response strings here — only the registration mechanism changed from a
Claude Agent SDK MCP tool to a plain Python dispatch function.

https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool

Every path is validated to stay inside MEMORY_ROOT — do not weaken this,
it is the only thing standing between "remember my coffee order" and a
path-traversal write to an arbitrary file on disk.
"""

from __future__ import annotations

import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MEMORY_ROOT = PROJECT_ROOT / "memories"
VIRTUAL_PREFIX = "/memories"


class PathViolation(Exception):
    pass


def _resolve(virtual_path: str) -> Path:
    """Map a virtual /memories/... path onto a real path inside MEMORY_ROOT.

    Raises PathViolation for anything that isn't a genuine child of
    MEMORY_ROOT once symlinks/.. are resolved.
    """
    if not virtual_path.startswith(VIRTUAL_PREFIX):
        raise PathViolation(f"Path must start with {VIRTUAL_PREFIX}: {virtual_path}")

    MEMORY_ROOT.mkdir(parents=True, exist_ok=True)
    relative = virtual_path[len(VIRTUAL_PREFIX):].lstrip("/")
    candidate = (MEMORY_ROOT / relative).resolve() if relative else MEMORY_ROOT.resolve()

    real_root = MEMORY_ROOT.resolve()
    if candidate != real_root and real_root not in candidate.parents:
        raise PathViolation(f"Path escapes memory root: {virtual_path}")
    return candidate


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "K", "M", "G"):
        if size < 1024 or unit == "G":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return f"{size:.1f}G"


class MemoryStore:
    """Executes the six memory-tool commands against MEMORY_ROOT."""

    def view(self, path: str, view_range: list[int] | None = None) -> str:
        try:
            real = _resolve(path)
        except PathViolation as e:
            return f"Error: {e}"

        if not real.exists():
            if path.rstrip("/") == VIRTUAL_PREFIX:
                real.mkdir(parents=True, exist_ok=True)
            else:
                return f"The path {path} does not exist. Please provide a valid path."

        if real.is_dir():
            lines = [f"Here're the files and directories up to 2 levels deep in {path}, "
                     f"excluding hidden items and node_modules:"]
            lines.append(f"{_human_size(self._dir_size(real))}\t{path}")
            for depth1 in sorted(real.iterdir()):
                if depth1.name.startswith(".") or depth1.name == "node_modules":
                    continue
                rel1 = f"{path.rstrip('/')}/{depth1.name}"
                size = depth1.stat().st_size if depth1.is_file() else self._dir_size(depth1)
                lines.append(f"{_human_size(size)}\t{rel1}")
                if depth1.is_dir():
                    for depth2 in sorted(depth1.iterdir()):
                        if depth2.name.startswith(".") or depth2.name == "node_modules":
                            continue
                        rel2 = f"{rel1}/{depth2.name}"
                        size2 = depth2.stat().st_size if depth2.is_file() else self._dir_size(depth2)
                        lines.append(f"{_human_size(size2)}\t{rel2}")
            return "\n".join(lines)

        # file view
        text = real.read_text(errors="replace")
        all_lines = text.split("\n")
        if len(all_lines) > 999_999:
            return f"File {path} exceeds maximum line limit of 999,999 lines."

        start, end = 1, len(all_lines)
        if view_range:
            start = max(1, view_range[0])
            end = len(all_lines) if view_range[1] == -1 else min(len(all_lines), view_range[1])

        numbered = "\n".join(
            f"{i:>6}\t{all_lines[i - 1]}" for i in range(start, end + 1)
        )
        return f"Here's the content of {path} with line numbers:\n{numbered}"

    def create(self, path: str, file_text: str) -> str:
        try:
            real = _resolve(path)
        except PathViolation as e:
            return f"Error: {e}"
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text(file_text)
        return f"File created successfully at: {path}"

    def str_replace(self, path: str, old_str: str, new_str: str | None = None) -> str:
        try:
            real = _resolve(path)
        except PathViolation as e:
            return f"Error: {e}"
        if not real.exists() or real.is_dir():
            return f"Error: The path {path} does not exist. Please provide a valid path."

        content = real.read_text()
        count = content.count(old_str)
        if count == 0:
            return f"No replacement was performed, old_str `{old_str}` did not appear verbatim in {path}."
        if count > 1:
            lines = [i + 1 for i, line in enumerate(content.split("\n")) if old_str in line]
            return (f"No replacement was performed. Multiple occurrences of old_str "
                     f"`{old_str}` in lines: {lines}. Please ensure it is unique")

        new_content = content.replace(old_str, new_str or "", 1)
        real.write_text(new_content)
        snippet = "\n".join(
            f"{i:>6}\t{line}" for i, line in enumerate(new_content.split("\n"), start=1)
        )
        return f"The memory file has been edited.\n{snippet}"

    def insert(self, path: str, insert_line: int, insert_text: str) -> str:
        try:
            real = _resolve(path)
        except PathViolation as e:
            return f"Error: {e}"
        if not real.exists() or real.is_dir():
            return f"Error: The path {path} does not exist"

        lines = real.read_text().split("\n")
        if insert_line < 0 or insert_line > len(lines):
            return (f"Error: Invalid `insert_line` parameter: {insert_line}. It should be "
                     f"within the range of lines of the file: [0, {len(lines)}]")

        lines.insert(insert_line, insert_text.rstrip("\n"))
        real.write_text("\n".join(lines))
        return f"The file {path} has been edited."

    def delete(self, path: str) -> str:
        try:
            real = _resolve(path)
        except PathViolation as e:
            return f"Error: {e}"
        if real.resolve() == MEMORY_ROOT.resolve():
            return "Error: cannot delete the /memories root directory"
        if not real.exists():
            return f"Error: The path {path} does not exist"
        if real.is_dir():
            shutil.rmtree(real)
        else:
            real.unlink()
        return f"Successfully deleted {path}"

    def rename(self, old_path: str, new_path: str) -> str:
        try:
            real_old = _resolve(old_path)
            real_new = _resolve(new_path)
        except PathViolation as e:
            return f"Error: {e}"
        if real_old.resolve() == MEMORY_ROOT.resolve():
            return "Error: cannot rename the /memories root directory"
        if not real_old.exists():
            return f"Error: The path {old_path} does not exist"
        if real_new.exists():
            return f"Error: The destination {new_path} already exists"
        real_new.parent.mkdir(parents=True, exist_ok=True)
        real_old.rename(real_new)
        return f"Successfully renamed {old_path} to {new_path}"

    @staticmethod
    def _dir_size(d: Path) -> int:
        return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())


def memory_dispatch(tool_input: dict) -> str:
    """Plain function the local tool loop calls for the "memory" tool.
    Dispatches on tool_input["command"] to the matching MemoryStore method."""
    store = MemoryStore()
    command = tool_input.get("command")
    try:
        if command == "view":
            return store.view(tool_input["path"], tool_input.get("view_range"))
        if command == "create":
            return store.create(tool_input["path"], tool_input.get("file_text", ""))
        if command == "str_replace":
            return store.str_replace(tool_input["path"], tool_input["old_str"], tool_input.get("new_str"))
        if command == "insert":
            return store.insert(tool_input["path"], int(tool_input["insert_line"]), tool_input["insert_text"])
        if command == "delete":
            return store.delete(tool_input["path"])
        if command == "rename":
            return store.rename(tool_input["old_path"], tool_input["new_path"])
        return f"Error: unknown command {command}"
    except KeyError as e:
        return f"Error: missing required parameter {e}"


def seed_default_memories() -> None:
    """Create memories/preferences.md on first run so Jarvis has something
    to introduce itself with instead of a cold, empty memory directory."""
    MEMORY_ROOT.mkdir(parents=True, exist_ok=True)
    prefs = MEMORY_ROOT / "preferences.md"
    if not prefs.exists():
        prefs.write_text(
            "# User preferences\n\n"
            "- User is Krishna, MAIB student, prefers concise direct answers.\n"
            "- Wake word: jarvis\n"
            "- Preferred TTS voice: am_liam\n"
        )
