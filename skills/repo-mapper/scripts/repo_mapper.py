"""Build a compact repository map without importing the scanned code."""

import argparse
import ast
import itertools
from pathlib import Path
import re
import stat
import sys


DEFAULT_DEPTH = 3
MAX_DEPTH = 8
MAX_LINES = 80
MAX_LINE_LENGTH = 100
MAX_SYMBOLS = 6
MAX_SOURCE_BYTES = 256 * 1024

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
SKIP_SUFFIXES = SECRET_SUFFIXES | {".pyc", ".pyo"}
ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
TOOL_RECEIVERS = {"mcp", "server", "langchain", "tools"}
FUNCTION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def entries(directory):
    """Return visible regular files and directories, without following links."""
    for path in sorted(directory.iterdir(), key=lambda item: (item.name.casefold(), item.name)):
        name = path.name.casefold()
        if name.startswith(".") or SECRET_NAME.search(name) or path.suffix.casefold() in SKIP_SUFFIXES:
            continue
        if name.startswith("id_") or name.endswith(("_cache", ".egg-info")):
            continue
        try:
            metadata = path.lstat()
            if getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                continue
            if stat.S_ISDIR(metadata.st_mode):
                if name not in SKIP_DIRS and not (path / "pyvenv.cfg").is_file():
                    yield path, True
            elif stat.S_ISREG(metadata.st_mode):
                yield path, False
        except OSError:
            continue


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
    return bool(tags), f"{kind} {owner}{node.name}() L{node.lineno}{details}"


def python_lines(path):
    try:
        with path.open("rb") as source:
            data = source.read(MAX_SOURCE_BYTES + 1)
        if len(data) > MAX_SOURCE_BYTES:
            yield "AST skipped (file exceeds 256 KiB)"
            return
        module = ast.parse(data, filename=path.name)
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
            symbols.append((False, f"class {node.name} L{node.lineno}"))
            symbols.extend(
                function_label(method, f"{node.name}.")
                for method in node.body if isinstance(method, FUNCTION_TYPES)
            )
        elif isinstance(node, FUNCTION_TYPES):
            symbols.append(function_label(node))

    # Keep tagged definitions visible even in modules with many helpers.
    symbols.sort(key=lambda symbol: not symbol[0])
    for _, label in symbols[:MAX_SYMBOLS]:
        yield label
    if len(symbols) > MAX_SYMBOLS:
        yield f"... {len(symbols) - MAX_SYMBOLS} more definitions"


def tree_lines(directory, depth, indent=""):
    try:
        children = list(entries(directory))
    except OSError:
        yield f"{indent}[directory unreadable]"
        return
    # Show top-level files ahead of subtrees that could fill the output budget.
    children.sort(key=lambda child: child[1])
    for path, is_directory in children:
        if is_directory:
            yield f"{indent}|- {path.name}/"
            if depth > 1:
                yield from tree_lines(path, depth - 1, indent + "   ")
        else:
            yield f"{indent}|- {path.name}"
            if path.suffix.casefold() == ".py":
                for line in python_lines(path):
                    yield f"{indent}   {line}"


def compact_line(line):
    escaped = "".join(char if char.isprintable() else ascii(char)[1:-1] for char in line)
    if len(escaped) > MAX_LINE_LENGTH:
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
    lines = list(itertools.islice(tree_lines(root, args.depth), MAX_LINES - 1))
    for line in lines[:MAX_LINES - 2]:
        print(compact_line(line))
    if len(lines) > MAX_LINES - 2:
        print("... output limit reached. Scan a subfolder for more detail.")
    elif not lines:
        print("(no visible entries)")


if __name__ == "__main__":
    main()
