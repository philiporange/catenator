"""Tests for deterministic project overview extraction."""

from src.catenator.project_summary import build_project_overview


def fixture(path, content):
    return (path, "/project/" + path, content)


def test_python_and_javascript_package_facts_have_sources():
    files = [
        fixture(
            "package.json",
            '{"name":"web","description":"UI","scripts":{"build":"vite build"},"bin":{"web":"bin/web.js"},"dependencies":{"react":"1"}}',
        ),
        fixture(
            "pyproject.toml",
            '[project]\nname="api"\ndescription="service"\ndependencies=["httpx>=1"]\n[project.scripts]\nserve="api:main"\n[project.entry-points."api.plugins"]\njson="api.json:Plugin"\n',
        ),
        fixture(
            "src/api.py",
            '"""HTTP API."""\nimport api.helpers\ndef main():\n    pass\n',
        ),
    ]
    result = build_project_overview(files)
    text = "\n".join(result)
    assert result[0] == "- Languages by file count: Python (1)."
    assert "Package `web` — UI (`package.json:1`)" in text
    assert "`build` → `vite build`" in text
    assert "`web → bin/web.js`" in text
    assert "Runtime dependencies: `react`" in text
    assert "Package `api` — service (`pyproject.toml:2`)" in text
    assert "`serve` → `api:main`" in text
    assert "`api.plugins.json` → `api.json:Plugin`" in text
    assert (
        "Python module `src/api.py`: HTTP API.; defines `main`; imports `api.helpers`"
        in text
    )


def test_malformed_manifests_and_python_are_ignored():
    files = [
        fixture("package.json", "{"),
        fixture("pyproject.toml", "[project"),
        fixture("bad.py", "def nope("),
    ]
    assert build_project_overview(files) == [
        "- Languages by file count: Python (1)."
    ]


def test_valid_toml_with_non_table_project_and_tool_is_ignored():
    files = [
        fixture("a/pyproject.toml", 'project="not a table"\n'),
        fixture("b/pyproject.toml", 'tool="not a table"\n'),
    ]
    assert build_project_overview(files) == []


def test_package_json_string_bin_preserves_target():
    result = build_project_overview(
        [fixture("package.json", '{"name":"web","bin":"bin/cli.js"}')]
    )
    assert "Command entry points: `bin/cli.js` (`package.json:1`)" in result[1]


def test_setup_py_is_static_and_does_not_resolve_dynamic_values():
    content = """\nraise RuntimeError("must never execute")\nname = dangerous()\nsetup(name=name, description="tool", install_requires=["safe>=1"], entry_points={"console_scripts": ["safe=safe:main"]})\n"""
    text = "\n".join(build_project_overview([fixture("setup.py", content)]))
    assert "Package" not in text
    assert "Runtime dependencies: `safe>=1` (`setup.py:4`)" in text
    assert "Command entry points: `safe=safe:main` (`setup.py:4`)" in text


def test_output_is_deterministic_bounded_and_mentions_locations():
    files = [
        fixture("tests/test_z.py", ""),
        fixture("AGENTS.md", "rules"),
        fixture("z.py", "def z(): pass"),
        fixture("a.py", "def a(): pass"),
    ]
    assert build_project_overview(files) == build_project_overview(
        reversed(files)
    )
    result = build_project_overview(files * 30)
    assert len(result) <= 60
    assert any(
        "`AGENTS.md`" in line and "`tests/test_z.py`" in line
        for line in result
    )


def test_language_variants_and_relative_imports_are_described():
    files = [
        fixture(
            "src/pkg/main.py",
            '"""Main module."""\nfrom . import summarizer\ndef main():\n    pass\n',
        ),
        fixture("src/pkg/summarizer.py", "def summarize():\n    pass\n"),
        fixture("ui.jsx", "export default 1\n"),
        fixture("view.tsx", "export default 1\n"),
        fixture("worker.mjs", "export default 1\n"),
    ]
    text = "\n".join(build_project_overview(files))
    assert "JavaScript (2)" in text
    assert "TypeScript (1)" in text
    assert "defines `main`; imports `summarizer`" in text


def test_go_and_cargo_dependencies():
    files = [
        fixture(
            "go.mod",
            "module example.test/tool\nrequire (\n example.test/lib v1.2.3\n)\n",
        ),
        fixture(
            "Cargo.toml", '[package]\nname="tool"\n[dependencies]\nserde="1"\n'
        ),
    ]
    text = "\n".join(build_project_overview(files))
    assert "Go module `example.test/tool` (`go.mod:1`)" in text
    assert "Go dependencies: `example.test/lib`" in text
    assert "Rust package `tool` (`Cargo.toml:2`)" in text
    assert "Rust dependencies: `serde` (`Cargo.toml:3`)" in text


def test_readme_uses_first_prose_outside_code_with_source_line():
    content = """# Example

```text
This fenced text is not the project description.
```

The first real paragraph explains the project.
It continues on the next line.

Later details are omitted.
"""
    text = "\n".join(build_project_overview([fixture("README.md", content)]))
    assert (
        "README description: The first real paragraph explains the project. "
        "It continues on the next line. (`README.md:7`)" in text
    )
    assert "fenced text" not in text
    assert "Later details" not in text


def test_manifest_names_are_case_insensitive():
    files = [
        fixture("PACKAGE.JSON", '{"name":"web"}'),
        fixture("PYPROJECT.TOML", '[project]\nname="api"\n'),
        fixture("SETUP.PY", 'setup(name="legacy")\n'),
        fixture("REQUIREMENTS.DEV.TXT", "httpx==1\n"),
    ]
    text = "\n".join(build_project_overview(files))
    assert "Package `web` (`PACKAGE.JSON:1`)" in text
    assert "Package `api` (`PYPROJECT.TOML:2`)" in text
    assert "Package `legacy` (`SETUP.PY:1`)" in text
    assert "Declared requirements: `httpx==1`" in text


def test_null_byte_python_source_is_safely_ignored():
    result = build_project_overview(
        [fixture("src/broken.py", "def valid():\n    pass\n\x00")]
    )
    assert result == ["- Languages by file count: Python (1)."]
