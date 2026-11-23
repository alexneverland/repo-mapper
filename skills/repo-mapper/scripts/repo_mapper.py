"""Build a compact repository map without importing the scanned code."""

import argparse
import ast
from contextlib import closing
import heapq
import itertools
import os
from pathlib import Path
import re
import stat
import sys

from scan_paths import has_venv_marker, is_reparse_point, open_directory, read_source


DEFAULT_DEPTH = 3
MAX_DEPTH = 8
MAX_LINES = 80
MAX_LINE_LENGTH = 100
MAX_SYMBOLS = 6
MAX_SOURCE_BYTES = 256 * 1024
MAX_START_LINES = 16
MAX_FOLDER_PREVIEW = 20
MAX_ORDINARY_LINES = 12
INCOMPLETE, FILE, DIRECTORY = range(3)

SKIP_DIRS = {
    "venv", "virtualenv", "env", "__pycache__", "node_modules",
    "build", "dist", "eggs", "htmlcov", "site-packages", "__pypackages__",
    "cache", "caches",
}
SECRET_NAME = re.compile(
    r"(?:^|[._-])(?:env|secrets?|credentials?|tokens?|passwords?|"
    r"passwd|api[._-]?keys?|private[._-]?keys?|service[._-]?accounts?)(?:[._-]|$)"
)
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".kdbx"}
SSH_KEY_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "id_ecdsa_sk", "id_ed25519_sk"}
SKIP_SUFFIXES = SECRET_SUFFIXES | {".pyc", ".pyo"}
DATABASE_SUFFIXES = tuple(
    extension + companion
    for extension in (".db", ".sqlite", ".sqlite3")
    for companion in ("", "-journal", "-shm", "-wal")
)
START_FILES = {
    "agents.md", "pyproject.toml", "setup.py", "setup.cfg",
    "__main__.py", "main.py", "app.py", "api.py", "server.py", "cli.py",
    "boot.py", "manage.py", "asgi.py", "wsgi.py",
}
SOURCE_DIRS = {
    "api", "app", "apps", "core", "examples", "lib", "packages", "services",
    "skills", "src", "test", "tests", "tools",
}
ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
TOOL_RECEIVERS = {"mcp", "server", "langchain", "tools"}
FUNCTION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def entries(directory):
    """Return visible regular files and directories, without following links."""
    incomplete = False
    with os.scandir(directory) as children:
        for child in children:
            name = child.name.casefold()
            if name.startswith(".") or SECRET_NAME.search(name) or Path(name).suffix in SKIP_SUFFIXES:
                continue
            if name.split(".", 1)[0].rstrip("~") in SSH_KEY_NAMES:
                continue
            if name.endswith(("_cache", ".egg-info")):
                continue
            try:
                metadata = child.stat(follow_symlinks=False)
                if is_reparse_point(metadata):
                    continue
                if stat.S_ISDIR(metadata.st_mode) and name not in SKIP_DIRS:
                    with open_directory(child.name, directory) as child_directory:
                        if not has_venv_marker(child_directory):
                            yield DIRECTORY, child.name
                elif stat.S_ISREG(metadata.st_mode) and not name.endswith(DATABASE_SUFFIXES):
                    yield FILE, child.name
            except OSError:
                incomplete = True
    if incomplete:
        yield INCOMPLETE, ""


def decorator_label(decorator):
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Name):
        return "tool" if target.id in {"tool", "function_tool"} else None
    if not isinstance(target, ast.Attribute) or not isinstance(target.value, ast.Name):
        return None

    receiver, attribute = target.value.id, target.attr
    if attribute == "tool" and receiver in TOOL_RECEIVERS:
        return "tool"
    if receiver not in {"app", "router", "server", "bp", "blueprint"}:
        return None
    if attribute not in ROUTE_METHODS | {"route", "api_route"}:
        return None
    method = attribute.upper() if attribute in ROUTE_METHODS else "ROUTE"
    if isinstance(decorator, ast.Call):
        keywords = {item.arg: item.value for item in decorator.keywords}
        methods = keywords.get("methods")
        if method == "ROUTE" and isinstance(methods, (ast.List, ast.Tuple)):
            names = [
                value.value.upper() for value in methods.elts
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
                and value.value.lower() in ROUTE_METHODS
            ]
            method = ",".join(names) or method
        path = decorator.args[0] if decorator.args else keywords.get("path", keywords.get("rule"))
        if isinstance(path, ast.Constant) and isinstance(path.value, str):
            return f"{method} {path.value}"
    return method


def function_label(node, owner=""):
    kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    tags = [label for decorator in node.decorator_list if (label := decorator_label(decorator))]
    details = "  [" + "; ".join(tags) + "]" if tags else ""
    priority = 1 if not owner and node.name in {"main", "create_app"} else 3
    return (0 if tags else priority), f"{kind} {owner}{node.name}() L{node.lineno}{details}"


def python_lines(directory, name):
    try:
        data = read_source(directory, name, MAX_SOURCE_BYTES + 1)
        if len(data) > MAX_SOURCE_BYTES:
            yield "AST skipped (file exceeds 256 KiB)"
            return
        module = ast.parse(data, filename=name)
    except SyntaxError as error:
        location = f" L{error.lineno}" if error.lineno else ""
        yield f"SyntaxError{location}"
        return
    except (OSError, ValueError, RecursionError):
        yield "AST unavailable"
        return

    symbols = []
    for node in module.body:
        if isinstance(node, ast.ClassDef):
            symbols.append((2, f"class {node.name} L{node.lineno}"))
            symbols.extend(
                function_label(method, f"{node.name}.")
                for method in node.body if isinstance(method, FUNCTION_TYPES)
            )
        elif isinstance(node, FUNCTION_TYPES):
            symbols.append(function_label(node))

    # Keep routes, tools, and conventional entry points visible ahead of helpers.
    symbols.sort(key=lambda symbol: symbol[0])
    for _, label in symbols[:MAX_SYMBOLS]:
        yield label
    if len(symbols) > MAX_SYMBOLS:
        yield f"... {len(symbols) - MAX_SYMBOLS} more definitions"


def entry_order(entry):
    kind, name = entry
    folded = name.casefold()
    if kind == INCOMPLETE:
        priority = 0
    elif kind == FILE and folded.split(".", 1)[0] == "readme":
        priority = 1
    elif kind == FILE and folded in START_FILES:
        priority = 2
    elif kind == DIRECTORY:
        priority = 3 if folded in SOURCE_DIRS else 4
    else:
        priority = 5
    return priority, folded, name


def file_lines(directory, name, indent):
    yield f"{indent}|- {name}"
    if name.lower().endswith(".py"):
        for line in python_lines(directory, name):
            yield f"{indent}   {line}"


def preview_lines(lines, limit, notice):
    visible = list(itertools.islice(lines, limit + 1))
    yield from visible[:limit]
    if len(visible) > limit:
        yield notice


def tree_lines(directory, depth, prefix=""):
    indent = "   " if prefix else ""
    try:
        # Only keep as many sorted entries as the output can show.
        children = heapq.nsmallest(MAX_LINES, entries(directory), key=entry_order)
    except OSError:
        yield f"{indent}[directory unreadable]"
        return

    folders = [name for kind, name in children if kind == DIRECTORY]
    if prefix and children:
        yield compact_line(prefix, keep_end=True)

    starting_files = []
    ordinary_files = []
    for kind, name in children:
        if kind == INCOMPLETE:
            yield f"{indent}[scan incomplete: some entries unavailable]"
        elif kind == FILE:
            files = starting_files if entry_order((kind, name))[0] < 3 else ordinary_files
            files.append(name)

    starting_lines = (
        line for name in starting_files for line in file_lines(directory, name, indent)
    )
    folder_lines = (f"{indent}|- {name}/" for name in folders)
    ordinary_lines = (
        line for name in ordinary_files for line in file_lines(directory, name, indent)
    )
    if depth > 1 and folders:
        yield from preview_lines(starting_lines, MAX_START_LINES, f"{indent}... more starting files or definitions.")
        yield from preview_lines(folder_lines, MAX_FOLDER_PREVIEW, f"{indent}... more folders. Use --depth 1.")
        yield from preview_lines(
            ordinary_lines, MAX_ORDINARY_LINES,
            f"{indent}... more files or definitions. Use --depth 1 for this folder.",
        )
        for name in folders:
            child_prefix = f"{prefix}{name}/"
            try:
                with open_directory(name, directory) as child_directory:
                    if not has_venv_marker(child_directory):
                        yield from tree_lines(child_directory, depth - 1, child_prefix)
            except OSError:
                yield compact_line(f"{child_prefix} [directory unavailable]", keep_end=True)
    else:
        yield from starting_lines
        yield from folder_lines
        yield from ordinary_lines


def compact_line(line, *, keep_end=False):
    escaped = "".join(char if char.isprintable() else ascii(char)[1:-1] for char in line)
    if len(escaped) > MAX_LINE_LENGTH:
        if keep_end:
            return "..." + escaped[-(MAX_LINE_LENGTH - 3):]
        return escaped[:MAX_LINE_LENGTH - 3] + "..."
    return escaped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="Repository folder")
    parser.add_argument(
        "-d", "--depth", type=int, default=DEFAULT_DEPTH,
        metavar=f"1-{MAX_DEPTH}", help="Tree depth (default: %(default)s)",
    )
    args = parser.parse_args()
    if not 1 <= args.depth <= MAX_DEPTH:
        parser.error(f"--depth must be between 1 and {MAX_DEPTH}")
    try:
        root = args.folder.resolve(strict=True)
        if not root.is_dir():
            parser.error("folder must be a directory")
    except (OSError, RuntimeError):
        parser.error("folder must be an accessible directory")

    sys.stdout.reconfigure(errors="backslashreplace")
    print(compact_line(f"Repo map: {root.name or root.anchor} (depth {args.depth})"))
    try:
        with open_directory(root) as directory, closing(tree_lines(directory, args.depth)) as scan:
            lines = list(itertools.islice(scan, MAX_LINES))
    except OSError:
        parser.error("folder must be an accessible directory")
    truncated = len(lines) == MAX_LINES
    visible_lines = lines[:MAX_LINES - 2] if truncated else lines
    for line in visible_lines:
        print(compact_line(line))
    if truncated:
        print("... output limit reached. Scan a subfolder for more detail.")
    elif not lines:
        print("(no visible entries)")


if __name__ == "__main__":
    main()
