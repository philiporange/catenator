"""Tests for OpenAI-compatible LLM summarizer configuration."""

from src.catenator import summarizer


def test_get_llm_settings_reads_environment(monkeypatch):
    monkeypatch.setenv("CATENATOR_SUMMARIZER_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("CATENATOR_SUMMARIZER_API_KEY", "test-key")
    monkeypatch.setenv(
        "CATENATOR_SUMMARIZER_BASE_URL", "https://api.deepseek.com"
    )

    assert summarizer.get_llm_settings() == (
        "deepseek-v4-flash",
        "test-key",
        "https://api.deepseek.com",
    )


def test_get_llm_settings_loads_project_env(monkeypatch, tmp_path):
    monkeypatch.delenv("CATENATOR_SUMMARIZER_MODEL", raising=False)
    monkeypatch.delenv("CATENATOR_SUMMARIZER_API_KEY", raising=False)
    monkeypatch.delenv("CATENATOR_SUMMARIZER_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    env_path = tmp_path / ".env"
    env_path.write_text(
        "CATENATOR_SUMMARIZER_MODEL=deepseek-v4-flash\n"
        "CATENATOR_SUMMARIZER_API_KEY=test-key\n"
        "CATENATOR_SUMMARIZER_BASE_URL=https://api.deepseek.com\n"
    )

    assert summarizer.get_llm_settings(str(tmp_path)) == (
        "deepseek-v4-flash",
        "test-key",
        "https://api.deepseek.com",
    )


def test_summarize_file_uses_openai_client(monkeypatch, tmp_path):
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
    monkeypatch.setenv("CATENATOR_SUMMARIZER_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("CATENATOR_SUMMARIZER_API_KEY", "test-key")
    monkeypatch.setenv(
        "CATENATOR_SUMMARIZER_BASE_URL", "https://api.deepseek.com"
    )

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
    assert captured["api_key"] == "test-key"
    assert captured["base_url"] == "https://api.deepseek.com"
    assert captured["request"]["model"] == "deepseek-v4-flash"
    assert captured["request"]["temperature"] == 0
    assert "module.py" in captured["request"]["messages"][0]["content"]
