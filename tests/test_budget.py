"""End-to-end guarantees for token-budgeted project rendering."""

import builtins
import sys
from collections import Counter
from pathlib import Path

import pytest

tiktoken = pytest.importorskip("tiktoken")

from src.catenator import Catenator
from src.catenator import summarizer
from src.catenator.catenator import CatenatorEventHandler
from src.catenator.catenator import main

ENCODING = tiktoken.get_encoding(Catenator.TOKENIZER)


def tokens(text):
    return len(ENCODING.encode(text, disallowed_special=()))


def write_project(root, entries):
    for relative, content in entries:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def assert_complete_file_blocks(output):
    lines = output.splitlines()
    fences = Counter(
        line for line in lines if len(line) >= 3 and set(line) == {"`"}
    )
    assert all(count % 2 == 0 for count in fences.values())
    for index, line in enumerate(lines[:-1]):
        if line.startswith("# ") and lines[index + 1] in fences:
            assert (
                lines[index + 1] in lines[index + 2 :]
            ), f"unclosed file block: {line}"


@pytest.mark.parametrize("limit", [1, 96, 2000])
def test_actual_token_count_never_exceeds_tiny_or_normal_budget(
    tmp_path, limit
):
    write_project(
        tmp_path,
        [
            ("README.md", "# Guide\n" + "Long project prose. " * 4000),
            (
                "src/main.py",
                '"""Application entry point."""\ndef main():\n    return "ok"\n',
            ),
            ("tests/test_main.py", "def test_main():\n    assert True\n"),
        ],
    )
    output = Catenator(str(tmp_path), title="Budget Project").catenate(
        token_limit=limit
    )
    assert tokens(output) <= limit
    assert_complete_file_blocks(output)


def test_skipped_override_never_reappears_as_summary_or_outline(tmp_path):
    secret = "NEVER_RESTORE_THIS_CONTENT"
    write_project(
        tmp_path,
        [
            ("main.py", "def main():\n    return 1\n"),
            ("skip.py", f'def secret():\n    return "{secret}"\n'),
        ],
    )
    output = Catenator(str(tmp_path), include_tree=False).catenate(
        file_overrides={"skip.py": None}, token_limit=220
    )
    assert secret not in output
    assert "# skip.py" not in output
    assert tokens(output) <= 220


def test_budget_keeps_core_entry_point_and_a_representative_test(tmp_path):
    write_project(
        tmp_path,
        [
            (
                "src/main.py",
                '"""Run the service."""\ndef main():\n    return "CORE_MARKER"\n',
            ),
            (
                "src/features/accounts.py",
                "def create_account():\n    return 'account'\n" * 30,
            ),
            (
                "tests/test_main.py",
                "def test_main_contract():\n    assert True\n",
            ),
            ("docs/notes.md", "background notes\n" * 100),
        ],
    )
    output = Catenator(
        str(tmp_path), title="Priority", include_tree=False
    ).catenate(token_limit=400)
    assert "# src/main.py" in output
    assert "# tests/test_main.py" in output
    assert tokens(output) <= 400
    assert_complete_file_blocks(output)


def test_default_budgeting_never_calls_llm(tmp_path, monkeypatch):
    write_project(
        tmp_path, [("large.py", "def useful():\n    return 1\n" * 200)]
    )

    def unexpected_llm(*args, **kwargs):
        raise AssertionError("LLM summarization requires explicit opt-in")

    monkeypatch.setattr(summarizer, "summarize_file", unexpected_llm)
    output = Catenator(str(tmp_path)).catenate(token_limit=150)
    assert tokens(output) <= 150


def test_output_is_independent_of_file_creation_order(tmp_path):
    entries = [
        ("src/zeta.py", "def zeta():\n    return 2\n"),
        ("src/alpha.py", "def alpha():\n    return 1\n"),
        ("tests/test_alpha.py", "def test_alpha():\n    assert True\n"),
    ]
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    write_project(first, entries)
    write_project(second, reversed(entries))
    kwargs = dict(
        title="Same Project", include_tree=False, include_overview=False
    )
    assert Catenator(str(first), **kwargs).catenate(
        token_limit=260
    ) == Catenator(str(second), **kwargs).catenate(token_limit=260)


def test_budgeted_render_reads_each_source_only_once(tmp_path, monkeypatch):
    write_project(
        tmp_path,
        [
            ("main.py", "def main():\n    return 1\n"),
            ("helper.py", "def help():\n    return 2\n"),
        ],
    )
    cat = Catenator(str(tmp_path), include_tree=False)
    source_paths = {
        (tmp_path / name).resolve() for name in ("main.py", "helper.py")
    }
    reads = {path: 0 for path in source_paths}
    real_open = builtins.open

    def tracking_open(file, *args, **kwargs):
        try:
            path = Path(file).resolve()
        except TypeError:
            path = None
        if path in reads:
            reads[path] += 1
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", tracking_open)
    cat.catenate(token_limit=180)
    assert reads == {path: 1 for path in source_paths}


def test_watch_update_preserves_budget_and_excludes_its_output(tmp_path):
    output_path = tmp_path / "context.md"
    output_path.write_text("GENERATED_OUTPUT_SENTINEL", encoding="utf-8")
    write_project(
        tmp_path,
        [
            ("main.py", "def main():\n    return 'fresh'\n"),
            ("README.md", "details " * 1000),
        ],
    )
    cat = Catenator(str(tmp_path), title="Watched", include_tree=False)
    handler = CatenatorEventHandler(
        cat, str(output_path), cooldown=0, token_limit=160
    )
    handler.update_output()
    rendered = output_path.read_text(encoding="utf-8")
    assert tokens(rendered) <= 160
    assert "GENERATED_OUTPUT_SENTINEL" not in rendered
    assert "# context.md" not in rendered
    assert_complete_file_blocks(rendered)


def test_cli_stdout_is_only_the_budgeted_document(
    tmp_path, monkeypatch, capsys
):
    write_project(
        tmp_path,
        [
            ("main.py", "def main():\n    return 1\n"),
            (".catconfig.yaml", "builds:\n  app:\n    whitelist: [main.py]\n"),
        ],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "catenator",
            str(tmp_path),
            "--build",
            "app",
            "--token-limit",
            "180",
            "--count-tokens",
        ],
    )
    main()
    captured = capsys.readouterr()
    assert tokens(captured.out) <= 180
    assert "Using build" not in captured.out
    assert "Token count:" not in captured.out
    assert "Using build" in captured.err
    assert f"Token count: {tokens(captured.out)}" in captured.err


@pytest.mark.parametrize("limit", ["0", "-1"])
def test_cli_rejects_nonpositive_budgets(tmp_path, monkeypatch, limit):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "catenator",
            str(tmp_path),
            "--token-limit",
            limit,
        ],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
