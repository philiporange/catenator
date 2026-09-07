"""Regression tests for project metadata discovery and traversal boundaries."""

import os

from src.catenator import Catenator


def write_files(root, files):
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_default_discovery_includes_metadata_and_nested_readmes(tmp_path):
    files = {
        "package.json": '{"name": "demo"}',
        "pyproject.toml": '[project]\nname = "demo"',
        "requirements-dev.txt": "pytest",
        "requirements.in": "httpx",
        "Makefile": "test:\n\tpytest",
        "Dockerfile": "FROM python",
        "go.mod": "module demo",
        "frontend/App.tsx": "export default function App() {}",
        "frontend/README.md": "Nested package guide",
        "README.rst": "Root guide",
        ".github/workflows/tests.yml": "name: Tests",
        ".env": "SECRET=do-not-include",
        ".private/config.yaml": "secret: do-not-include",
    }
    write_files(tmp_path, files)
    cat = Catenator(str(tmp_path))
    selected = {path for path, _, _ in cat.collect_files()}
    assert selected == set(files) - {".env", ".private/config.yaml"}
    output = cat.catenate()
    assert "Nested package guide" in output
    assert "Root guide" in output
    assert "do-not-include" not in output


def test_explicit_filters_and_no_readme_apply_to_metadata(tmp_path):
    write_files(
        tmp_path,
        {
            "package.json": "{}",
            "Makefile": "test:\n\tpytest",
            "src/main.py": "print('main')",
            "src/README.md": "package guide",
            "README.md": "root guide",
            ".github/workflows/tests.yml": "name: CI",
        },
    )
    cat = Catenator(
        str(tmp_path), include_extensions=["py"], include_readme=False
    )
    assert [path for path, _, _ in cat.collect_files()] == ["src/main.py"]
    assert "package guide" not in cat.catenate()
    cat = Catenator(str(tmp_path), ignore_extensions=["json", "yml"])
    selected = {path for path, _, _ in cat.collect_files()}
    assert "package.json" not in selected
    assert ".github/workflows/tests.yml" not in selected


def test_catignore_can_exclude_automatically_discovered_ci(tmp_path):
    write_files(
        tmp_path,
        {
            ".catignore": ".github/\npackage.json\n",
            "package.json": "{}",
            ".github/workflows/ci.yml": "name: CI",
            "main.py": "print('main')",
        },
    )
    assert [
        path for path, _, _ in Catenator(str(tmp_path)).collect_files()
    ] == ["main.py"]


def test_ignored_subtrees_are_pruned_before_descent(tmp_path, monkeypatch):
    write_files(
        tmp_path,
        {
            "src/main.py": "print('main')",
            "src/node_modules/pkg/index.js": "vendored",
            "dist/bundle.js": "generated",
        },
    )
    original = os.scandir

    def guarded(path):
        assert "node_modules" not in str(path)
        assert not str(path).endswith("/dist")
        return original(path)

    monkeypatch.setattr(os, "scandir", guarded)
    cat = Catenator(str(tmp_path))
    assert [path for path, _, _ in cat.collect_files()] == ["src/main.py"]
    assert "node_modules" not in cat.generate_directory_tree()


def test_nested_build_whitelist_keeps_ancestors_and_nonstandard_files(
    tmp_path,
):
    write_files(
        tmp_path,
        {
            "src/frontend/README.md": "frontend guide",
            "src/frontend/data.custom": "custom format",
            "src/backend/app.py": "backend",
            "src/frontend/node_modules/pkg/a.js": "vendor",
        },
    )
    cat = Catenator(
        str(tmp_path), build_config={"whitelist": ["src/frontend/"]}
    )
    assert {path for path, _, _ in cat.collect_files()} == {
        "src/frontend/README.md",
        "src/frontend/data.custom",
    }


def test_build_glob_prunes_unrelated_directories(tmp_path, monkeypatch):
    write_files(
        tmp_path,
        {
            "src/frontend/main.py": "print('main')",
            "unrelated/large/tree/blob.txt": "unrelated",
        },
    )
    original = os.scandir

    def guarded(path):
        assert "unrelated" not in str(path)
        return original(path)

    monkeypatch.setattr(os, "scandir", guarded)
    cat = Catenator(str(tmp_path), build_config={"whitelist": ["src/**/*.py"]})
    assert [path for path, _, _ in cat.collect_files()] == [
        "src/frontend/main.py"
    ]


def test_overview_can_be_disabled(tmp_path):
    write_files(tmp_path, {"main.py": '"""Entrypoint."""\nprint("main")'})
    assert "# Project Overview" in Catenator(str(tmp_path)).catenate()
    assert (
        "# Project Overview"
        not in Catenator(str(tmp_path), include_overview=False).catenate()
    )
