"""
Deterministic file importance ranking and structural summarization.

When a project exceeds the token limit, this module:
1. Ranks files by importance using fast heuristics (entry points, tests, etc.)
2. Summarizes the least important files on demand to reduce tokens
3. Caches summaries in ~/.catenator/summaries/ for reuse, keyed by file hash
   and summary backend

Summaries are generated lazily, only when needed to fit within the token
limit.
By default, extracts bounded declarations, imports, documentation outlines,
and configuration keys instead of copying arbitrary file prefixes. With --llm,
uses the OpenAI Python client for richer summaries. The
target project's .env is loaded before each LLM summary so
CATENATOR_SUMMARIZER_MODEL, CATENATOR_SUMMARIZER_API_KEY, and
CATENATOR_SUMMARIZER_BASE_URL can configure the default backend.
"""

import ast
import copy
import os
import hashlib
import json
import re
from pathlib import Path
from typing import Optional

SUMMARY_CACHE_DIR = Path.home() / ".catenator" / "summaries"
IMPORTANCE_CACHE_FILENAME = ".importance_cache.json"
DEFAULT_LLM_MODEL = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
LLM_MODEL_ENV = "CATENATOR_SUMMARIZER_MODEL"
LLM_API_KEY_ENV = "CATENATOR_SUMMARIZER_API_KEY"
LLM_BASE_URL_ENV = "CATENATOR_SUMMARIZER_BASE_URL"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_BASE_URL_ENV = "OPENAI_BASE_URL"
DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"
DEEPSEEK_BASE_URL_ENV = "DEEPSEEK_BASE_URL"
STRUCTURAL_CACHE_CONTEXT = "structural:v2"


def is_test_file(rel_path: str) -> bool:
    """Check if a file is a test file."""
    parts = rel_path.lower().replace("\\", "/").split("/")
    filename = parts[-1]
    return (
        any(part in {"test", "tests", "__tests__"} for part in parts)
        or filename.startswith("test_")
        or "_test." in filename
        or ".test." in filename
        or ".spec." in filename
        or filename == "conftest.py"
    )


def extract_docstring(content: str) -> str:
    """
    Extract just the module-level docstring from Python code.
    Returns empty string if no docstring or not valid Python.
    """
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return ""

    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        doc = tree.body[0].value.value
        return doc[:500] + "..." if len(doc) > 500 else doc
    return ""


def _bounded(
    entries: list[str], line_limit: int = 80, char_limit: int = 12000
) -> str:
    """Bound by physical lines and characters while keeping entries whole."""
    output: list[str] = []
    characters = 0
    for entry in entries:
        lines = entry.splitlines() or [""]
        required = len(entry) + (1 if output else 0)
        if (
            len(output) + len(lines) >= line_limit
            or characters + required > char_limit - 25
        ):
            output.append("... (outline truncated)")
            break
        output.extend(lines)
        characters += required
    return "\n".join(output).rstrip()


def _extract_python_structure(content: str) -> Optional[str]:
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return None
    source_lines = content.splitlines()
    result = ["Python structure:"]
    module_doc = ast.get_docstring(tree, clean=False)
    if module_doc:
        result.append(
            f'  Module: """{module_doc.strip().splitlines()[0][:200]}"""'
        )

    def declaration(node, indent="  "):
        decorators = getattr(node, "decorator_list", [])
        start = min([node.lineno] + [item.lineno for item in decorators])
        first = node.body[0]
        body_start = min(
            [first.lineno]
            + [item.lineno for item in getattr(first, "decorator_list", [])]
        )
        end = max(node.lineno, body_start - 1)
        span = str(start) if start == end else f"{start}-{end}"
        outline_node = copy.copy(node)
        outline_node.body = [ast.Pass()]
        rendered = ast.unparse(ast.fix_missing_locations(outline_node))
        rendered_lines = rendered.splitlines()
        if rendered_lines and rendered_lines[-1].strip() == "pass":
            rendered_lines.pop()
        rendered = "\n".join(rendered_lines).rstrip()
        if len(rendered_lines) > 30 or len(rendered) > 2000:
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            rendered = (
                f"{kind} {node.name}: ... (declaration omitted: too large)"
            )
        result.append(
            f"{indent}[lines {span}] " + rendered.replace("\n", "\n" + indent)
        )
        doc = ast.get_docstring(node, clean=False)
        if doc:
            result.append(
                f'{indent}  """{doc.strip().splitlines()[0][:160]}"""'
            )

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            text = (
                ast.get_source_segment(content, node)
                or source_lines[node.lineno - 1]
            )
            if len(text) > 500 or text.count("\n") > 8:
                text = f"{type(node).__name__} (import omitted: too large)"
            result.append(f"  [line {node.lineno}] {text.strip()}")
        elif isinstance(
            node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            declaration(node)
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(
                        child, (ast.FunctionDef, ast.AsyncFunctionDef)
                    ):
                        declaration(child, "    ")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            names = [
                target.id for target in targets if isinstance(target, ast.Name)
            ]
            public = [name for name in names if not name.startswith("_")]
            annotation = (
                ast.unparse(node.annotation)
                if isinstance(node, ast.AnnAssign)
                else ""
            )
            selected = (
                "__all__" in names
                or any(
                    name.isupper()
                    or name.endswith(("Type", "Protocol", "Alias"))
                    for name in public
                )
                or annotation.endswith("TypeAlias")
            )
            if selected:
                text = (
                    ast.get_source_segment(content, node)
                    or source_lines[node.lineno - 1]
                )
                if len(text) <= 300 and "\n" not in text:
                    result.append(f"  [line {node.lineno}] {text.strip()}")
    return (
        _bounded(result)
        if len(result) > 1
        else "Python module (no public structure detected)."
    )


def _extract_non_python_structure(content: str, relative_path: str) -> str:
    suffix = Path(relative_path).suffix.lower()
    name = Path(relative_path).name.lower()
    lines = content.splitlines()
    result: list[str] = []
    if suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
        result.append("JavaScript/TypeScript structure:")
        pattern = re.compile(
            r"^\s*(?:export\s+(?:default\s+)?)?(?:declare\s+)?"
            r"(?:async\s+)?"
            r"(?:function|class|interface|type|enum|const|let|var)"
            r"\b|^\s*(?:import|export)\b"
        )
        for number, line in enumerate(lines, 1):
            if pattern.search(line):
                result.append(f"  [line {number}] {line.strip()[:240]}")
        if len(result) > 1:
            return _bounded(result)
        return "JavaScript/TypeScript module (no exports detected)."
    if suffix in {".md", ".mdx", ".rst"} or name.startswith("readme"):
        result.append("Document outline:")
        fence = None
        for number, line in enumerate(lines, 1):
            marker = re.match(r"^\s*(`{3,}|~{3,})", line)
            if marker:
                if fence is None:
                    fence = marker[0].strip()
                elif marker[0].strip()[0] == fence[0] and len(
                    marker[0].strip()
                ) >= len(fence):
                    fence = None
                continue
            if fence:
                continue
            if re.match(r"^#{1,6}\s+\S", line):
                result.append(f"  [line {number}] {line.strip()[:240]}")
        return (
            _bounded(result)
            if len(result) > 1
            else "Document (no headings detected)."
        )
    if suffix == ".json":
        try:
            value = json.loads(content)
        except (ValueError, TypeError):
            return "JSON file (invalid or incomplete; structure unavailable)."
        if isinstance(value, dict):
            keys = list(value)[:40]
            return (
                "JSON object keys:\n  "
                + "\n  ".join(map(str, keys))
                + ("\n  ..." if len(value) > 40 else "")
            )
        count = len(value) if isinstance(value, list) else 1
        return f"JSON {type(value).__name__} with {count} value(s)."
    if suffix in {".toml", ".ini", ".cfg", ".yaml", ".yml"} or name in {
        "dockerfile",
        "makefile",
    }:
        result.append("Configuration structure:")
        for number, line in enumerate(lines, 1):
            stripped = line.strip()
            if re.match(r"^\[[^]]+\]$", stripped) or re.match(
                r"^[A-Za-z0-9_.-]+\s*[:=]", stripped
            ):
                key = re.split("[:=]", stripped, 1)[0][:200]
                result.append(f"  [line {number}] {key}")
        return (
            _bounded(result)
            if len(result) > 1
            else "Configuration file (no sections or keys detected)."
        )
    file_type = suffix.lstrip(".").upper() or "Text"
    return f"{file_type} file; no structural extractor available."


def extract_signatures(
    content: str, relative_path: Optional[str] = None
) -> str:
    """
    Extract a bounded structural summary, using the path to select a parser.
    """
    path = relative_path or ""
    if Path(path).suffix.lower() == ".py" or not path:
        python = _extract_python_structure(content)
        if python is not None:
            return python
        if path.endswith(".py"):
            return (
                "Python file (syntax invalid or incomplete; "
                "structure unavailable)."
            )
    return _extract_non_python_structure(content, path)


def get_project_cache_dir(project_path: str) -> Path:
    """Get the cache directory for a project's summaries."""
    abs_path = os.path.abspath(project_path)
    # Convert /home/sam/project to the safe name _home_sam_project.
    safe_name = abs_path.replace("/", "_").lstrip("_")
    return SUMMARY_CACHE_DIR / safe_name


def get_file_hash(file_path: str) -> str:
    """Get hash of file contents for cache invalidation."""
    with open(file_path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def get_summary_path(project_path: str, relative_path: str) -> Path:
    """Get the path where a file's summary should be cached."""
    cache_dir = get_project_cache_dir(project_path)
    dir_part = os.path.dirname(relative_path)
    filename = os.path.basename(relative_path)
    summary_name = f"{filename}.cat"
    return cache_dir / dir_part / summary_name


def load_cached_summary(
    project_path: str,
    relative_path: str,
    file_path: str,
    summary_context: Optional[str] = None,
) -> Optional[str]:
    """Load a cached summary if it exists and is still valid."""
    summary_path = get_summary_path(project_path, relative_path)
    if not summary_path.exists():
        return None

    # Check if the summary metadata matches current file
    meta_path = summary_path.with_suffix(".cat.meta")
    if not meta_path.exists():
        return None

    try:
        with open(meta_path, "r") as f:
            meta = json.load(f)
        current_hash = get_file_hash(file_path)
        if meta.get("hash") != current_hash:
            return None
        if (
            summary_context is not None
            and meta.get("context") != summary_context
        ):
            return None
        with open(summary_path, "r") as f:
            return f.read()
    except (json.JSONDecodeError, IOError):
        return None


def save_summary(
    project_path: str,
    relative_path: str,
    file_path: str,
    summary: str,
    summary_context: str = "structural",
) -> None:
    """Save a summary to the cache."""
    summary_path = get_summary_path(project_path, relative_path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    with open(summary_path, "w") as f:
        f.write(summary)

    meta_path = summary_path.with_suffix(".cat.meta")
    with open(meta_path, "w") as f:
        json.dump(
            {"hash": get_file_hash(file_path), "context": summary_context}, f
        )


def is_env_key(key: str) -> bool:
    """Check whether a string is a valid shell-style environment key."""
    return (
        bool(key)
        and (key[0].isalpha() or key[0] == "_")
        and all(c.isalnum() or c == "_" for c in key)
    )


def load_project_env(project_path: str) -> None:
    """Load simple KEY=value pairs from the target project's .env file."""
    env_path = Path(project_path) / ".env"
    if not env_path.is_file():
        return

    with open(env_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()

            key, sep, value = line.partition("=")
            key = key.strip()
            if not sep or not is_env_key(key):
                continue

            value = value.strip()
            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in ("'", '"')
            ):
                value = value[1:-1]
            os.environ.setdefault(key, value)


def get_env_value(*env_names: str) -> Optional[str]:
    """Return the first configured, non-empty environment value."""
    for env_name in env_names:
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    return None


def is_deepseek_model(model_name: str) -> bool:
    """Check whether a model name should use DeepSeek defaults."""
    model_lower = model_name.lower()
    return model_lower.startswith("deepseek") or "deepseek/" in model_lower


def get_llm_settings(
    project_path: Optional[str] = None,
) -> tuple[str, Optional[str], Optional[str]]:
    """Get the configured OpenAI-compatible model, API key, and base URL."""
    if project_path:
        load_project_env(project_path)

    model = os.getenv(LLM_MODEL_ENV, DEFAULT_LLM_MODEL).strip()
    model = model or DEFAULT_LLM_MODEL

    if is_deepseek_model(model):
        api_key = get_env_value(
            LLM_API_KEY_ENV, DEEPSEEK_API_KEY_ENV, OPENAI_API_KEY_ENV
        )
        base_url = get_env_value(
            LLM_BASE_URL_ENV, DEEPSEEK_BASE_URL_ENV, OPENAI_BASE_URL_ENV
        )
        base_url = base_url or DEFAULT_DEEPSEEK_BASE_URL
    else:
        api_key = get_env_value(
            LLM_API_KEY_ENV, OPENAI_API_KEY_ENV, DEEPSEEK_API_KEY_ENV
        )
        base_url = get_env_value(
            LLM_BASE_URL_ENV, OPENAI_BASE_URL_ENV, DEEPSEEK_BASE_URL_ENV
        )

    return model, api_key, base_url


def get_summary_context(model_name: str, base_url: Optional[str]) -> str:
    """Build the cache context for an OpenAI-compatible summary backend."""
    return f"llm:openai:{base_url or 'default'}:{model_name}"


def create_openai_client(api_key: str, base_url: Optional[str]):
    """Create an OpenAI-compatible client for LLM summaries."""
    from openai import OpenAI

    kwargs = {"api_key": api_key, "timeout": 60}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def summarize_with_openai(
    relative_path: str,
    content: str,
    model_name: str,
    api_key: str,
    base_url: Optional[str],
) -> Optional[str]:
    """Generate an LLM summary with the OpenAI Python client."""
    prompt = f"""Summarize this source file concisely for a developer who needs
to understand the codebase.
Focus on its purpose, key functions/classes, dependencies, and project role.
Keep it under 200 words.

File: {relative_path}
```
{content[:8000]}
```

Respond with ONLY the summary, no preamble."""

    client = create_openai_client(api_key, base_url)
    response = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=300,
    )
    if not response.choices:
        return None

    summary = response.choices[0].message.content
    return summary.strip() if summary else None


def estimate_importance(rel_path: str, content: str) -> float:
    """
    Estimate file importance using heuristics. Higher = more important.

    Project guides and manifests lead, followed by production entry points.
    Tests remain represented without receiving entry-point boosts. Public
    package wiring and ordinary source modules receive middle-range scores.
    """
    path_lower = rel_path.lower()
    filename = os.path.basename(path_lower)

    if filename in {
        "readme",
        "readme.md",
        "agents.md",
        "package.json",
        "pyproject.toml",
        "cargo.toml",
        "go.mod",
    }:
        return 0.98

    # Tests are classified before filename and main-guard entry heuristics.
    if is_test_file(rel_path):
        return 0.3

    # High importance: conventional or proven entry points
    if filename in ("main.py", "__main__.py", "cli.py", "app.py", "server.py"):
        return 0.95
    if filename.endswith(".py"):
        try:
            tree = ast.parse(content)
            for node in tree.body:
                if not isinstance(node, ast.If) or not isinstance(
                    node.test, ast.Compare
                ):
                    continue
                comparison = node.test
                if len(comparison.ops) != 1 or not isinstance(
                    comparison.ops[0], ast.Eq
                ):
                    continue
                operands = (comparison.left, comparison.comparators[0])
                has_name = any(
                    isinstance(value, ast.Name) and value.id == "__name__"
                    for value in operands
                )
                has_main = any(
                    isinstance(value, ast.Constant)
                    and value.value == "__main__"
                    for value in operands
                )
                if has_name and has_main:
                    return 0.9
        except (SyntaxError, ValueError):
            pass

    # Examples and fixtures usually explain less of the architecture.
    if "/example" in path_lower or "/fixture" in path_lower:
        return 0.15

    # Package wiring and configuration describe public surface and setup.
    if filename in ("setup.py", "conftest.py", "config.py"):
        return 0.45
    if filename == "__init__.py":
        has_public_wiring = "import " in content or "__all__" in content
        return 0.55 if has_public_wiring else 0.35
    if filename.endswith((".json", ".yaml", ".yml", ".toml", ".cfg", ".ini")):
        return 0.5

    # Medium-low: utilities, helpers
    if "/util" in path_lower or "/helper" in path_lower or "util" in filename:
        return 0.35

    # Default: moderate importance, favoring files closer to the project root.
    depth = rel_path.count("/")
    depth_penalty = min(depth * 0.05, 0.2)
    return 0.6 - depth_penalty


def rank_files_by_importance(
    project_path: str, files: list[tuple[str, str, str]]
) -> list[tuple[str, str, str, float]]:
    """
    Rank files by importance using deterministic project heuristics.

    Args:
        project_path: Root path of the project
        files: List of (relative_path, file_path, content) tuples

    Returns:
        List of (relative_path, file_path, content, importance_score) tuples,
        sorted by importance (highest first)
    """
    if not files:
        return []

    result = []
    modules: dict[str, str] = {}
    module_context: dict[str, tuple[str, bool]] = {}
    for rel_path, _, _ in files:
        if rel_path.endswith(".py"):
            raw_module = rel_path[:-3].replace("/", ".")
            is_package = raw_module.endswith(".__init__")
            module = raw_module.removesuffix(".__init__")
            aliases = {module}
            if module.startswith("src."):
                aliases.add(module[4:])
            for alias in aliases:
                modules[alias] = rel_path
            canonical = module[4:] if module.startswith("src.") else module
            module_context[rel_path] = (canonical, is_package)

    dependency_edges: set[tuple[str, str]] = set()
    for rel_path, _, content in files:
        if not rel_path.endswith(".py"):
            continue
        try:
            tree = ast.parse(content)
        except (SyntaxError, ValueError):
            continue
        current, is_package = module_context[rel_path]
        package = (
            current
            if is_package
            else (current.rsplit(".", 1)[0] if "." in current else "")
        )
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = package.split(".") if package else []
                    prefix = ".".join(
                        parts[: max(0, len(parts) - node.level + 1)]
                    )
                    base = ".".join(filter(None, (prefix, base)))
                names = [base] + [
                    f"{base}.{alias.name}" for alias in node.names
                ]
            for name in names:
                target = modules.get(name)
                if target and target != rel_path:
                    dependency_edges.add((rel_path, target))

    incoming: dict[str, int] = {}
    for _, target in dependency_edges:
        incoming[target] = incoming.get(target, 0) + 1

    for rel_path, file_path, content in files:
        score = estimate_importance(rel_path, content)
        score = min(1.0, score + min(incoming.get(rel_path, 0) * 0.06, 0.24))
        result.append((rel_path, file_path, content, score))

    # Path tie-breaking makes output independent of the caller's input order.
    result.sort(key=lambda item: (-item[3], item[0]))
    return result


def summarize_file(
    project_path: str,
    relative_path: str,
    file_path: str,
    content: str,
    use_llm: bool = False,
) -> str:
    """
    Generate a concise summary of a source file.

    Checks cache first, generates new summary if needed.
    By default extracts signatures/docstrings. With use_llm=True, uses AI
    for richer summaries through an OpenAI-compatible API.
    """
    if use_llm:
        model_name, api_key, base_url = get_llm_settings(project_path)
        summary_context = get_summary_context(model_name, base_url)
    else:
        model_name = None
        api_key = None
        base_url = None
        summary_context = STRUCTURAL_CACHE_CONTEXT

    # Check cache
    cached = load_cached_summary(
        project_path, relative_path, file_path, summary_context
    )
    if cached is not None:
        return cached

    if use_llm and api_key:
        try:
            llm_summary = summarize_with_openai(
                relative_path, content, model_name, api_key, base_url
            )
        except Exception:
            summary = extract_signatures(content, relative_path)
            summary_context = STRUCTURAL_CACHE_CONTEXT
        else:
            if llm_summary:
                summary = llm_summary
            else:
                summary = extract_signatures(content, relative_path)
                summary_context = STRUCTURAL_CACHE_CONTEXT
    else:
        summary = extract_signatures(content, relative_path)
        if use_llm:
            summary_context = STRUCTURAL_CACHE_CONTEXT

    save_summary(
        project_path, relative_path, file_path, summary, summary_context
    )
    return summary
