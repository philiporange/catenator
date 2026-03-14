"""
File importance ranking and summarization for catenator.

When a project exceeds the token limit, this module:
1. Ranks files by importance using fast heuristics (entry points, tests, etc.)
2. Summarizes the least important files on-demand to reduce tokens
3. Caches summaries in ~/.catenator/summaries/ for reuse

Summaries are generated lazily - only when needed to fit within the token limit.
By default, extracts function/class signatures and docstrings as a structural
summary. With --llm flag, uses AI (robot module) for richer summaries.
"""

import ast
import os
import hashlib
import json
from pathlib import Path
from typing import Optional

try:
    from robot import Robot
    from robot.base import AgentConfig
    HAS_ROBOT = True
except ImportError:
    HAS_ROBOT = False


SUMMARY_CACHE_DIR = Path.home() / ".catenator" / "summaries"
IMPORTANCE_CACHE_FILENAME = ".importance_cache.json"


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
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
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
    project_path: str, relative_path: str, file_path: str
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
        with open(summary_path, "r") as f:
            return f.read()
    except (json.JSONDecodeError, IOError):
        return None


def save_summary(
    project_path: str, relative_path: str, file_path: str, summary: str
) -> None:
    """Save a summary to the cache."""
    summary_path = get_summary_path(project_path, relative_path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    with open(summary_path, "w") as f:
        f.write(summary)

    meta_path = summary_path.with_suffix(".cat.meta")
    with open(meta_path, "w") as f:
        json.dump({"hash": get_file_hash(file_path)}, f)


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
    project_path: str, relative_path: str, file_path: str, content: str,
    use_llm: bool = False
) -> str:
    """
    Generate a concise summary of a source file.

    Checks cache first, generates new summary if needed.
    By default extracts signatures/docstrings. With use_llm=True, uses AI
    for richer summaries (requires robot module).
    """
    # Check cache
    cached = load_cached_summary(project_path, relative_path, file_path)
    if cached is not None:
        return cached

    if use_llm and HAS_ROBOT:
        prompt = f"""Summarize this source file concisely for a developer who needs to understand the codebase.
Focus on: purpose, key functions/classes, dependencies, and how it fits the project.
Keep it under 200 words.

File: {relative_path}
```
{content[:8000]}
```

Respond with ONLY the summary, no preamble."""

        config = AgentConfig(model="haiku", timeout=60)
        agent = Robot.get("claude", config=config)
        response = agent.run(prompt)

        if response.success:
            summary = response.content.strip()
        else:
            summary = extract_signatures(content)
    else:
        summary = extract_signatures(content)

    save_summary(project_path, relative_path, file_path, summary)
    return summary
