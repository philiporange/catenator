"""Tests for LLM summarizer configuration, defaults, and client integration.

This module tests gateway model and endpoint defaults, environment variable
precedence for generic and dedicated overrides, target project .env loading,
and client invocation with fallback behavior.
"""

from src.catenator import summarizer


def _isolate_env(monkeypatch):
    for key in (
        "CATENATOR_SUMMARIZER_MODEL",
        "CATENATOR_SUMMARIZER_API_KEY",
        "CATENATOR_SUMMARIZER_BASE_URL",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_get_llm_settings_reads_environment(monkeypatch):
    _isolate_env(monkeypatch)

    # Gateway defaults with no environment configured
    assert summarizer.get_llm_settings() == (
        "muse-code/muse-spark-1.3",
        None,
        "https://llm.ph1l.uk/v1",
    )

    # Generic environment variable fallback
    monkeypatch.setenv("LLM_API_KEY", "generic-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.com/v1")
    assert summarizer.get_llm_settings() == (
        "muse-code/muse-spark-1.3",
        "generic-key",
        "https://llm.example.com/v1",
    )

    # Dedicated overrides take precedence over generic variables
    monkeypatch.setenv("CATENATOR_SUMMARIZER_MODEL", "custom-model")
    monkeypatch.setenv("CATENATOR_SUMMARIZER_API_KEY", "dedicated-key")
    monkeypatch.setenv(
        "CATENATOR_SUMMARIZER_BASE_URL", "https://dedicated.example.com/v1"
    )
    assert summarizer.get_llm_settings() == (
        "custom-model",
        "dedicated-key",
        "https://dedicated.example.com/v1",
    )

    # Vendor keys are ignored when generic LLM_API_KEY is unset
    monkeypatch.delenv("CATENATOR_SUMMARIZER_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "vendor-openai-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "vendor-deepseek-key")
    _, api_key, _ = summarizer.get_llm_settings()
    assert api_key is None


def test_get_llm_settings_loads_project_env(monkeypatch, tmp_path):
    _isolate_env(monkeypatch)

    # Generic settings in project .env
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_API_KEY=project-generic-key\n"
        "LLM_BASE_URL=https://project-gw.example.com/v1\n"
    )
    assert summarizer.get_llm_settings(str(tmp_path)) == (
        "muse-code/muse-spark-1.3",
        "project-generic-key",
        "https://project-gw.example.com/v1",
    )

    # Dedicated overrides in project .env take precedence
    env_path.write_text(
        "CATENATOR_SUMMARIZER_MODEL=override-model\n"
        "CATENATOR_SUMMARIZER_API_KEY=override-key\n"
        "CATENATOR_SUMMARIZER_BASE_URL=https://override.example.com/v1\n"
        "LLM_API_KEY=fallback-key\n"
    )
    assert summarizer.get_llm_settings(str(tmp_path)) == (
        "override-model",
        "override-key",
        "https://override.example.com/v1",
    )


def test_summarize_file_uses_openai_client(monkeypatch, tmp_path):
    _isolate_env(monkeypatch)
    captured = {}

    class FakeMessage:
        content = "AI summary"

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    def fake_create_openai_client(api_key, base_url):
        captured["api_key"] = api_key
        captured["base_url"] = base_url
        return FakeClient()

    monkeypatch.setattr(summarizer, "SUMMARY_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(
        summarizer, "create_openai_client", fake_create_openai_client
    )

    # Test generic key with default model and gateway base URL
    monkeypatch.setenv("LLM_API_KEY", "generic-key")

    source_path = tmp_path / "module.py"
    source_path.write_text("def hello():\n    return 'world'\n")

    summary = summarizer.summarize_file(
        str(tmp_path),
        "module.py",
        str(source_path),
        source_path.read_text(),
        use_llm=True,
    )

    assert summary == "AI summary"
    assert captured["api_key"] == "generic-key"
    assert captured["base_url"] == "https://llm.ph1l.uk/v1"
    assert captured["request"]["model"] == "muse-code/muse-spark-1.3"
    assert captured["request"]["temperature"] == 0
    assert captured["request"]["max_tokens"] == 2048
    assert "module.py" in captured["request"]["messages"][0]["content"]

    # Key selection: dedicated override takes precedence over generic key
    monkeypatch.setenv("CATENATOR_SUMMARIZER_API_KEY", "dedicated-key")
    monkeypatch.setattr(summarizer, "SUMMARY_CACHE_DIR", tmp_path / "cache2")

    summary2 = summarizer.summarize_file(
        str(tmp_path),
        "module.py",
        str(source_path),
        source_path.read_text(),
        use_llm=True,
    )

    assert summary2 == "AI summary"
    assert captured["api_key"] == "dedicated-key"
    assert captured["base_url"] == "https://llm.ph1l.uk/v1"
    assert captured["request"]["model"] == "muse-code/muse-spark-1.3"


def test_python_structure_preserves_multiline_decorated_signatures():
    source = '''from typing import Optional

PUBLIC_LIMIT = 20

@register(
    "thing",
)
def build(
    name: str,
    enabled: bool = True,
) -> Optional[str]:
    """Build one thing."""
    if enabled:
        return name
    return None
'''

    summary = summarizer.extract_signatures(source, "src/module.py")

    assert "[line 1] from typing import Optional" in summary
    assert "[line 3] PUBLIC_LIMIT = 20" in summary
    assert "@register(" in summary
    assert "enabled: bool=True" in summary
    assert ") -> Optional[str]:" in summary
    assert "[lines 5-11]" in summary
    assert "return name" not in summary


def test_javascript_and_document_structures_are_labelled():
    javascript = "import x from 'x';\nexport interface Item {\n  id: string\n}\nexport const make = () => 1;\n"
    js_summary = summarizer.extract_signatures(javascript, "src/items.ts")
    md_summary = summarizer.extract_signatures(
        "intro\n# Install\ntext\n## Run\n", "README.md"
    )

    assert js_summary.startswith("JavaScript/TypeScript structure:")
    assert "[line 1] import x" in js_summary
    assert "[line 2] export interface Item" in js_summary
    assert "[line 5] export const make" in js_summary
    assert (
        md_summary
        == "Document outline:\n  [line 2] # Install\n  [line 4] ## Run"
    )


def test_json_and_unknown_files_do_not_copy_arbitrary_content():
    summary = summarizer.extract_signatures(
        '{"name": "demo", "scripts": {}}', "package.json"
    )
    fallback = summarizer.extract_signatures(
        "a secret-looking blob", "asset.bin"
    )

    assert summary == "JSON object keys:\n  name\n  scripts"
    assert fallback == "BIN file; no structural extractor available."
    assert "secret-looking" not in fallback


def test_ranking_prioritizes_guides_entrypoints_and_import_centrality(
    tmp_path,
):
    files = [
        ("README.md", str(tmp_path / "README.md"), "# Project"),
        ("src/core.py", str(tmp_path / "core.py"), "def api():\n    pass\n"),
        ("src/use_a.py", str(tmp_path / "use_a.py"), "from src import core\n"),
        ("src/use_b.py", str(tmp_path / "use_b.py"), "import src.core\n"),
        (
            "test_main.py",
            str(tmp_path / "test_main.py"),
            "def test_main():\n    pass\n",
        ),
        (
            "runner.py",
            str(tmp_path / "runner.py"),
            'if __name__ == "__main__":\n    print("go")\n',
        ),
    ]

    ranked = summarizer.rank_files_by_importance(str(tmp_path), files)
    scores = {path: score for path, _, _, score in ranked}

    assert ranked[0][0] == "README.md"
    assert scores["runner.py"] > scores["test_main.py"]
    assert scores["src/core.py"] > scores["src/use_a.py"]


def test_structural_cache_context_is_versioned(monkeypatch, tmp_path):
    monkeypatch.setattr(summarizer, "SUMMARY_CACHE_DIR", tmp_path / "cache")
    source = tmp_path / "module.py"
    source.write_text("def hello():\n    pass\n")

    summarizer.summarize_file(
        str(tmp_path), "module.py", str(source), source.read_text()
    )
    meta = summarizer.get_summary_path(str(tmp_path), "module.py").with_suffix(
        ".cat.meta"
    )

    assert '"context": "structural:v2"' in meta.read_text()


def test_class_header_does_not_absorb_method_decorator_or_body():
    source = """class Service(
    Base,
):
    @cached
    def value(self): return expensive_call()
"""

    summary = summarizer.extract_signatures(source, "service.py")

    class_entry, method_entry = summary.split("    [lines", 1)
    assert "class Service(Base):" in class_entry
    assert "@cached" not in class_entry
    assert "@cached" in method_entry
    assert "def value(self):" in method_entry
    assert "expensive_call" not in summary


def test_malformed_null_byte_source_is_handled_conservatively(tmp_path):
    content = "def valid():\n    pass\n\x00"

    assert summarizer.extract_signatures(content, "broken.py") == (
        "Python file (syntax invalid or incomplete; structure unavailable)."
    )
    ranked = summarizer.rank_files_by_importance(
        str(tmp_path), [("broken.py", str(tmp_path / "broken.py"), content)]
    )
    assert ranked[0][0] == "broken.py"


def test_structural_summary_is_bounded_by_physical_lines():
    parameters = ",\n".join(
        f"    argument_{number}: str = 'value'" for number in range(100)
    )
    source = f"def enormous(\n{parameters},\n):\n    pass\n"

    summary = summarizer.extract_signatures(source, "large.py")

    assert "declaration omitted: too large" in summary
    assert len(summary.splitlines()) <= 80


def test_ranking_ties_are_ordered_by_path_independent_of_input(tmp_path):
    files = [
        ("zeta.py", str(tmp_path / "zeta.py"), "VALUE = 1\n"),
        ("alpha.py", str(tmp_path / "alpha.py"), "VALUE = 1\n"),
    ]

    forward = summarizer.rank_files_by_importance(str(tmp_path), files)
    reverse = summarizer.rank_files_by_importance(
        str(tmp_path), list(reversed(files))
    )

    assert [item[0] for item in forward] == ["alpha.py", "zeta.py"]
    assert forward == reverse


def test_src_layout_and_package_relative_imports_add_centrality(tmp_path):
    files = [
        (
            "src/pkg/__init__.py",
            "init",
            "from . import core\nfrom . import core\n",
        ),
        ("src/pkg/core.py", "core", "def api():\n    pass\n"),
        (
            "src/pkg/consumer.py",
            "consumer",
            "import pkg.core\nimport pkg.core\n",
        ),
        ("src/pkg/peer.py", "peer", "VALUE = 1\n"),
    ]

    ranked = summarizer.rank_files_by_importance(str(tmp_path), files)
    scores = {path: score for path, _, _, score in ranked}

    # The two unique importers add 0.12; repeated imports do not add more.
    assert scores["src/pkg/core.py"] == 0.62
    assert scores["src/pkg/core.py"] > scores["src/pkg/peer.py"]


def test_test_main_guard_does_not_outrank_production_entrypoint(tmp_path):
    guard = 'if __name__ == "__main__":\n    raise SystemExit()\n'
    files = [
        ("tests/main.py", "test-entry", guard),
        ("cli.py", "production-entry", "def main():\n    pass\n"),
    ]

    ranked = summarizer.rank_files_by_importance(str(tmp_path), files)
    scores = {path: score for path, _, _, score in ranked}

    assert scores["tests/main.py"] == 0.3
    assert ranked[0][0] == "cli.py"


def test_markdown_ignores_headings_inside_code_fences():
    content = """# Visible
```markdown
# Hidden
```
~~~
## Also hidden
~~~
## Visible too
"""

    summary = summarizer.extract_signatures(content, "README.md")

    assert "# Visible" in summary
    assert "## Visible too" in summary
    assert "Hidden" not in summary


def test_multiline_declaration_span_includes_closing_header_line():
    source = """def render(
    value: str,
    width: int = 79,
) -> str:
    return value
"""

    summary = summarizer.extract_signatures(source, "render.py")

    assert "[lines 1-4]" in summary
    assert "def render(value: str, width: int=79) -> str:" in summary


def test_test_file_detection_handles_js_conventions_without_prefix_guessing():
    assert summarizer.is_test_file("web/widget.spec.ts")
    assert summarizer.is_test_file("web/widget.test.js")
    assert not summarizer.is_test_file("src/testimonials.py")


def test_bounding_keeps_declaration_entries_whole():
    entries = ["Python structure:"]
    entries.extend(
        f"  [line {number}] import module_{number}" for number in range(78)
    )
    entries.append(
        "  [lines 80-82] def complete(\n      value: str,\n  ) -> str:"
    )

    summary = summarizer._bounded(entries)

    assert "def complete" not in summary
    assert summary.endswith("... (outline truncated)")
    assert len(summary.splitlines()) == 80
