"""
File importance ranking and summarization for catenator.

When a project exceeds the token limit, this module:
1. Ranks files by importance using fast heuristics (entry points, tests, etc.)
2. Summarizes the least important files on-demand to reduce tokens
3. Caches summaries in ~/.catenator/summaries/ for reuse, keyed by file hash
   and summary backend

Summaries are generated lazily - only when needed to fit within the token limit.
By default, extracts function/class signatures and docstrings as a structural
summary. With --llm, uses the OpenAI Python client for richer summaries. The
target project's .env is loaded before each LLM summary so
CATENATOR_SUMMARIZER_MODEL, CATENATOR_SUMMARIZER_API_KEY, and
CATENATOR_SUMMARIZER_BASE_URL can configure the default backend.
"""

import ast
import os
import hashlib
import json
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


def is_test_file(rel_path: str) -> bool:
    """Check if a file is a test file."""
    path_lower = rel_path.lower()
    filename = os.path.basename(path_lower)
    return (
        "/test" in path_lower
        or path_lower.startswith("test")
        or filename.startswith("test_")
        or "_test." in filename
        or filename == "conftest.py"
    )


def extract_docstring(content: str) -> str:
    """
    Extract just the module-level docstring from Python code.
    Returns empty string if no docstring or not valid Python.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
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


def extract_signatures(content: str) -> str:
    """
    Extract function/class signatures, docstrings, and return statements from Python code.
    Falls back to first 500 chars for non-Python or unparseable files.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        # Not valid Python, return truncated content
        return content[:500] + "\n..." if len(content) > 500 else content

    lines = content.splitlines()
    result = []

    def get_docstring(node) -> Optional[str]:
        """Extract docstring from a node if present."""
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            doc = node.body[0].value.value
            return doc[:200] + "..." if len(doc) > 200 else doc
        return None

    def process_node(node, indent=""):
        """Process a single AST node."""
        if isinstance(node, ast.ClassDef):
            result.append(f"{indent}{lines[node.lineno - 1].strip()}")
            doc = get_docstring(node)
            if doc:
                result.append(f'{indent}    """{doc}"""')
            # Process methods inside the class
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    process_node(child, indent + "    ")
            result.append("")

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result.append(f"{indent}{lines[node.lineno - 1].strip()}")
            doc = get_docstring(node)
            if doc:
                result.append(f'{indent}    """{doc}"""')
            # Return statements
            for child in ast.walk(node):
                if isinstance(child, ast.Return) and child.value is not None:
                    try:
                        return_line = lines[child.lineno - 1].strip()
                        result.append(f"{indent}    {return_line}")
                    except IndexError:
                        pass
            result.append("")

    # Get module docstring
    doc = get_docstring(tree)
    if doc:
        result.append(f'"""{doc}"""')
        result.append("")

    # Process top-level nodes in order
    for node in tree.body:
        if isinstance(
            node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            process_node(node)

    if not result:
        return content[:500] + "\n..." if len(content) > 500 else content

    return "\n".join(result)


def get_project_cache_dir(project_path: str) -> Path:
    """Get the cache directory for a project's summaries."""
    abs_path = os.path.abspath(project_path)
    # Convert path to safe directory name: /home/sam/project -> _home_sam_project
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
    prompt = f"""Summarize this source file concisely for a developer who needs to understand the codebase.
Focus on: purpose, key functions/classes, dependencies, and how it fits the project.
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

    Heuristics:
    - Entry points (main, __main__, cli) are most important
    - Test files, examples, fixtures are least important
    - Config/setup files are less important
    - Core source files (not in subdirs like utils/) are more important
    """
    path_lower = rel_path.lower()
    filename = os.path.basename(path_lower)

    # High importance: entry points
    if filename in ("main.py", "__main__.py", "cli.py", "app.py", "server.py"):
        return 0.95
    if "main" in filename or "entry" in filename:
        return 0.85

    # Low importance: tests, examples, fixtures
    if (
        "/test" in path_lower
        or path_lower.startswith("test")
        or filename.startswith("test_")
        or "_test." in filename
    ):
        return 0.1
    if "/example" in path_lower or "/fixture" in path_lower:
        return 0.15

    # Low importance: config, setup, boilerplate
    if filename in ("setup.py", "conftest.py", "config.py", "__init__.py"):
        return 0.2
    if filename.endswith((".json", ".yaml", ".yml", ".toml", ".cfg", ".ini")):
        return 0.25

    # Medium-low: utilities, helpers
    if "/util" in path_lower or "/helper" in path_lower or "util" in filename:
        return 0.35

    # Default: moderate importance, slightly favor shorter files (likely core logic)
    # and files closer to root
    depth = rel_path.count("/")
    depth_penalty = min(depth * 0.05, 0.2)
    return 0.6 - depth_penalty


def rank_files_by_importance(
    project_path: str, files: list[tuple[str, str, str]]
) -> list[tuple[str, str, str, float]]:
    """
    Rank files by their importance to understanding the project using heuristics.

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
    for rel_path, file_path, content in files:
        score = estimate_importance(rel_path, content)
        result.append((rel_path, file_path, content, score))

    # Sort by importance (highest first)
    result.sort(key=lambda x: x[3], reverse=True)
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
        summary_context = "structural"

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
            summary = extract_signatures(content)
            summary_context = "structural"
        else:
            if llm_summary:
                summary = llm_summary
            else:
                summary = extract_signatures(content)
                summary_context = "structural"
    else:
        summary = extract_signatures(content)

    save_summary(
        project_path, relative_path, file_path, summary, summary_context
    )
    return summary
