"""Build factual project overviews without executing source or calling AI.

Manifests supply package metadata and declared commands; README prose supplies
the project's own description. Python ASTs identify definitions and internal
imports. Each bounded bullet carries its source location where available.
"""

import ast
import json
import os
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import tomli

ProjectFile = Tuple[str, str, str]
MAX_BULLETS = 60
MAX_TEXT = 160


def _line(content: str, needle: str) -> int:
    for number, text in enumerate(content.splitlines(), 1):
        if needle in text:
            return number
    return 1


def _cite(path: str, line: int) -> str:
    return f"`{path}:{line}`"


def _short(value: Any, limit: int = MAX_TEXT) -> str:
    text = " ".join(str(value).split()).replace("`", "'")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _quoted(values: Sequence[Any], limit: int) -> str:
    return ", ".join(f"`{_short(item, limit)}`" for item in values[:20])


def _readme_description(path: str, content: str) -> Optional[str]:
    """Extract the first prose paragraph outside headings and code fences."""
    paragraph = []
    start = 1
    fence = None
    for number, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        marker = re.match(r"^(`{3,}|~{3,})", stripped)
        if marker:
            fence = None if fence and marker[0][0] == fence else marker[0][0]
            continue
        if fence:
            continue
        if not stripped:
            if paragraph:
                break
            continue
        if re.match(r"^(?:#|!|\[|>|[-*=]{3,}$|[-*]\s|\d+[.)]\s)", stripped):
            if paragraph:
                break
            continue
        if not paragraph:
            start = number
        paragraph.append(stripped)
    if not paragraph:
        return None
    description = _short(" ".join(paragraph), 240)
    return f"- README description: {description} ({_cite(path, start)})"


def _items(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str)]
    if isinstance(value, dict):
        return sorted(str(item) for item in value)
    return []


def _package_json(path: str, content: str) -> List[str]:
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    out = []
    name = data.get("name")
    description = data.get("description")
    if isinstance(name, str):
        detail = (
            f" — {_short(description)}" if isinstance(description, str) else ""
        )
        name_line = _line(content, '"name"')
        out.append(
            f"- Package `{_short(name)}`{detail} ({_cite(path, name_line)})"
        )
    scripts = data.get("scripts")
    if isinstance(scripts, dict):
        pairs = [
            f"`{key}` → `{_short(value, 90)}`"
            for key, value in sorted(scripts.items())
            if isinstance(value, str)
        ]
        if pairs:
            scripts_line = _line(content, '"scripts"')
            commands = ", ".join(pairs[:12])
            out.append(
                f"- Declared package commands: {commands} "
                f"({_cite(path, scripts_line)})"
            )
    for key, label in (
        ("bin", "Command entry points"),
        ("dependencies", "Runtime dependencies"),
        ("devDependencies", "Development dependencies"),
    ):
        raw = data.get(key)
        if key == "bin" and isinstance(raw, dict):
            values = [
                f"{name} → {target}"
                for name, target in sorted(raw.items())
                if isinstance(target, str)
            ]
        elif key == "bin" and isinstance(raw, str):
            values = [raw]
        else:
            values = _items(raw)
        if values:
            items = _quoted(values, 60)
            source_line = _line(content, repr(key)[1:-1])
            out.append(f"- {label}: {items} ({_cite(path, source_line)})")
    return out


def _pyproject(path: str, content: str) -> List[str]:
    try:
        data = tomli.loads(content)
    except (tomli.TOMLDecodeError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    project = data.get("project", {})
    tool = data.get("tool", {})
    poetry = tool.get("poetry", {}) if isinstance(tool, dict) else {}
    meta = project if isinstance(project, dict) and project else poetry
    if not isinstance(meta, dict):
        return []
    out = []
    name = meta.get("name")
    description = meta.get("description")
    if isinstance(name, str):
        detail = (
            f" — {_short(description)}" if isinstance(description, str) else ""
        )
        name_line = _line(content, "name")
        out.append(
            f"- Package `{_short(name)}`{detail} "
            f"({_cite(path, name_line)})"
        )
    dependencies = _items(meta.get("dependencies"))
    if dependencies:
        items = _quoted(dependencies, 60)
        source_line = _line(content, "dependencies")
        out.append(
            f"- Runtime dependencies: {items} ({_cite(path, source_line)})"
        )
    for key, label in (
        ("scripts", "Command entry points"),
        ("entry-points", "Declared entry points"),
    ):
        values = meta.get(key)
        if isinstance(values, dict) and values:
            pairs = []
            for name, target in sorted(values.items()):
                if isinstance(target, str):
                    pairs.append(f"`{name}` → `{_short(target, 80)}`")
                elif key == "entry-points" and isinstance(target, dict):
                    pairs.extend(
                        f"`{name}.{entry}` → `{_short(value, 80)}`"
                        for entry, value in sorted(target.items())
                        if isinstance(value, str)
                    )
            if pairs:
                entries = ", ".join(pairs[:15])
                source_line = _line(content, key)
                out.append(
                    f"- {label}: {entries} ({_cite(path, source_line)})"
                )
    return out


def _setup_py(path: str, content: str) -> List[str]:
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return []
    call = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "setup")
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setup"
                )
            )
        ),
        None,
    )
    if call is None:
        return []
    values: Dict[str, Tuple[Any, int]] = {}
    for keyword in call.keywords:
        if keyword.arg:
            try:
                values[keyword.arg] = (
                    ast.literal_eval(keyword.value),
                    keyword.value.lineno,
                )
            except (ValueError, TypeError, SyntaxError):
                pass
    out = []
    if "name" in values:
        name, lineno = values["name"]
        desc = values.get("description", (None, 0))[0]
        detail = f" — {_short(desc)}" if isinstance(desc, str) else ""
        out.append(
            f"- Package `{_short(name)}`{detail} ({_cite(path, lineno)})"
        )
    for key, label in (
        ("install_requires", "Runtime dependencies"),
        ("entry_points", "Command entry points"),
    ):
        if key not in values:
            continue
        value, lineno = values[key]
        if key == "entry_points" and isinstance(value, dict):
            value = [
                item
                for group in value.values()
                if isinstance(group, list)
                for item in group
            ]
        items = _items(value)
        if items:
            rendered = _quoted(items, 70)
            out.append(f"- {label}: {rendered} ({_cite(path, lineno)})")
    return out


def _other_manifest(path: str, content: str) -> List[str]:
    base = os.path.basename(path).lower()
    if base.startswith("requirements") and base.endswith((".txt", ".in")):
        deps = []
        for raw in content.splitlines():
            item = raw.strip()
            if item and not item.startswith(("#", "-")):
                deps.append(item.split(" #", 1)[0])
        return (
            [
                f"- Declared requirements: {_quoted(deps, 60)} "
                f"({_cite(path, 1)})"
            ]
            if deps
            else []
        )
    if base == "go.mod":
        match = re.search(r"(?m)^module\s+(\S+)", content)
        out = []
        if match:
            module = _short(match.group(1))
            source_line = _line(content, match.group(0))
            out.append(f"- Go module `{module}` ({_cite(path, source_line)})")
        dependencies = re.findall(
            r"(?m)^\s*([\w./-]+)\s+v[^\s]+(?:\s+//.*)?$", content
        )
        if dependencies:
            items = _quoted(dependencies, 60)
            source_line = _line(content, "require")
            out.append(
                f"- Go dependencies: {items} ({_cite(path, source_line)})"
            )
        return out
    if base == "cargo.toml":
        try:
            data = tomli.loads(content)
        except (tomli.TOMLDecodeError, TypeError):
            return []
        package = data.get("package", {})
        out = []
        if isinstance(package, dict) and isinstance(package.get("name"), str):
            desc = package.get("description")
            detail = f" — {_short(desc)}" if isinstance(desc, str) else ""
            package_name = _short(package["name"])
            source_line = _line(content, "name")
            out.append(
                f"- Rust package `{package_name}`{detail} "
                f"({_cite(path, source_line)})"
            )
        dependencies = data.get("dependencies", {})
        if isinstance(dependencies, dict) and dependencies:
            items = _quoted(sorted(dependencies), 60)
            source_line = _line(content, "[dependencies]")
            out.append(
                f"- Rust dependencies: {items} ({_cite(path, source_line)})"
            )
        return out
    return []


def _python_module(
    path: str, content: str, module_names: Sequence[str]
) -> Optional[str]:
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return None
    doc = ast.get_docstring(tree, clean=True)
    symbols = [
        node.name
        for node in tree.body
        if isinstance(
            node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        )
        and not node.name.startswith("_")
    ]
    imports = []
    known = set(module_names)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            names = (
                [node.module]
                if node.module
                else [alias.name for alias in node.names]
            )
        else:
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else []
            )
        for name in names:
            if name and (
                name.split(".")[0] in known
                or (isinstance(node, ast.ImportFrom) and node.level)
            ):
                imports.append(name)
    parts = []
    if doc:
        parts.append(_short(doc, 110))
    if symbols:
        parts.append(
            "defines " + ", ".join(f"`{name}`" for name in symbols[:10])
        )
    if imports:
        parts.append(
            "imports "
            + ", ".join(f"`{name}`" for name in sorted(set(imports))[:8])
        )
    return (
        f"- Python module `{path}`: {'; '.join(parts)} ({_cite(path, 1)})"
        if parts
        else None
    )


def build_project_overview(files: Iterable[ProjectFile]) -> List[str]:
    """Return prioritized standalone Markdown bullets describing *files*."""
    sorted_files = sorted(
        files, key=lambda item: (item[0].replace("\\", "/"), item[1], item[2])
    )
    ordered = []
    seen_paths = set()
    for item in sorted_files:
        normalized = item[0].replace("\\", "/")
        if normalized not in seen_paths:
            ordered.append(item)
            seen_paths.add(normalized)
    counts = Counter()
    language = {
        ".py": "Python",
        ".pyi": "Python",
        ".js": "JavaScript",
        ".jsx": "JavaScript",
        ".mjs": "JavaScript",
        ".cjs": "JavaScript",
        ".ts": "TypeScript",
        ".tsx": "TypeScript",
        ".mts": "TypeScript",
        ".cts": "TypeScript",
        ".go": "Go",
        ".rs": "Rust",
        ".java": "Java",
        ".rb": "Ruby",
        ".php": "PHP",
        ".cs": "C#",
        ".cpp": "C++",
        ".c": "C",
        ".sh": "Shell",
    }
    for path, _absolute, _content in ordered:
        label = language.get(os.path.splitext(path)[1].lower())
        if label:
            counts[label] += 1
    bullets = []
    if counts:
        summary = ", ".join(
            f"{name} ({count})"
            for name, count in sorted(
                counts.items(), key=lambda item: (-item[1], item[0])
            )
        )
        bullets.append(f"- Languages by file count: {summary}.")

    root_readme = next(
        (
            (path, content)
            for path, _, content in ordered
            if path.lower() in {"readme", "readme.md", "readme.txt"}
        ),
        None,
    )
    if root_readme:
        description = _readme_description(*root_readme)
        if description:
            bullets.append(description)

    for path, _absolute, content in ordered:
        base = os.path.basename(path).lower()
        if base == "package.json":
            bullets.extend(_package_json(path, content))
        elif base == "pyproject.toml":
            bullets.extend(_pyproject(path, content))
        elif base == "setup.py":
            bullets.extend(_setup_py(path, content))
        else:
            bullets.extend(_other_manifest(path, content))

    locations = []
    for path, _absolute, _content in ordered:
        low = path.lower()
        if (
            os.path.basename(path) == "AGENTS.md"
            or low.startswith(("docs/", "test/", "tests/"))
            or os.path.basename(low)
            in {
                "tox.ini",
                "pytest.ini",
                "mypy.ini",
                ".eslintrc",
                ".pre-commit-config.yaml",
            }
        ):
            locations.append(f"`{path}`")
    if locations:
        useful_locations = ", ".join(locations[:20])
        bullets.append(
            "- Useful project guidance, tests, docs, or configuration: "
            f"{useful_locations}."
        )

    roots = set()
    for path, _, _ in ordered:
        normalized = path.replace("\\", "/")
        if not normalized.endswith(".py"):
            continue
        module_path = (
            normalized[4:] if normalized.startswith("src/") else normalized
        )
        first = module_path.split("/", 1)[0]
        roots.add(os.path.splitext(first)[0])
    roots = sorted(roots)
    python_files = [
        (path, content)
        for path, _, content in ordered
        if path.endswith(".py")
        and not path.replace("\\", "/").startswith(("test/", "tests/"))
    ]
    for path, content in python_files[:30]:
        bullet = _python_module(path, content, roots)
        if bullet:
            bullets.append(bullet)
    return bullets[:MAX_BULLETS]
