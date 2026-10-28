"""Verify Jev inputs, adaptive context recovery, caching, budgets, and activation.

An in-memory scorer returns controlled per-file answers while retaining each
request for inspection. Tests exercise shared-rubric packing, incremental prompt
caches, run-wide spending limits, real discovery, and rendering without
network access or writes to the user's score cache, including server rejections
that require question or evidence subdivision.
"""

import copy
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from src.catenator import Catenator, jev
from src.catenator.catenator import CatenatorEventHandler, main
from src.catenator.config import JevSettings
from src.catenator.jev_client import JevContextError, JevError


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    env = SimpleNamespace(
        root=root,
        calls=[],
        general={},
        queries={},
        score_part=None,
    )
    monkeypatch.setattr(jev, "JEV_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(
        jev, "get_jev_settings", lambda _: JevSettings(api_key="test-key")
    )

    def evaluate(client, state, questions):
        env.calls.append(
            copy.deepcopy(
                {
                    "model": client.settings.model,
                    "state": state,
                    "questions": questions,
                }
            )
        )
        ratings = env.queries.get(state.get("query"), env.general)
        answers = {}
        for key, question in questions.items():
            path = next(
                path
                for path in env.general
                if f"file {json.dumps(path)}," in question["instructions"]
            )
            score = ratings[path]
            if env.score_part:
                score = env.score_part(state, path, score)
            answers[key] = {"type": "score", "score": score, "confidence": 0.9}
        return {
            "model": client.settings.model,
            "usage": {"input_tokens": 1234},
            "answers": answers,
        }

    monkeypatch.setattr(jev.JevClient, "evaluate", evaluate)
    return env


def write(project, path, content, score=2.0):
    target = project.root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    project.general[path] = score
    return target


def catenator(project):
    return Catenator(
        str(project.root), include_tree=False, include_overview=False
    )


@pytest.mark.parametrize("full_source", [False, True])
def test_scores_control_inclusion_and_strict_output_budgets(project, full_source):
    write(project, "main.py", 'def main():\n    return "VERBATIM_BODY"\n', 1.9)
    write(
        project,
        "support.py",
        'def helper():\n    return "HIDDEN_IMPLEMENTATION"\n',
        1.0,
    )
    write(
        project,
        "unused.py",
        'def unused():\n    return "UNRELATED_BODY"\n',
        0.1,
    )
    cat = catenator(project)
    output = cat.catenate(use_jev=True, jev_full_source=full_source)
    assert "VERBATIM_BODY" in output
    assert "# support.py (summary)" in output
    assert "HIDDEN_IMPLEMENTATION" not in output
    assert "# unused.py" not in output
    assert cat.last_report == dict(
        unreadable=0,
        minified=0,
        eligible=3,
        full=1,
        summary=1,
        outline=0,
        omitted=1,
    )
    sent = {
        entry["path"]: entry["content"]
        for entry in project.calls[0]["state"]["source_files"]
    }
    assert set(sent) == set(project.general)
    if full_source:
        assert sent == {
            path: (project.root / path).read_text() for path in project.general
        }
    else:
        assert "def main():" in sent["main.py"]
        assert "VERBATIM_BODY" not in json.dumps(project.calls)
        assert "HIDDEN_IMPLEMENTATION" not in json.dumps(project.calls)
        assert "UNRELATED_BODY" not in json.dumps(project.calls)
    for limit in (1, 100, 250):
        limited = cat.catenate(
            use_jev=True, token_limit=limit, jev_full_source=full_source
        )
        assert cat.count_tokens(limited) <= limit
        assert limited.count("```") % 2 == 0
    assert len(project.calls) == 1


def test_default_mode_never_calls_jev(project):
    write(project, "main.py", "def main():\n    return 1\n")
    cat = catenator(project)
    cat.catenate(token_limit=100)
    assert project.calls == []
    assert cat.last_jev_report == []


def test_source_batches_preserve_large_unicode_files_and_use_highest_score(
    project, monkeypatch
):
    monkeypatch.setattr(jev, "JEV_STATE_TOKEN_LIMIT", 3000)
    content = "def catalogue():\n" + "".join(
        f'    entry_{i} = "漢字🌍 {i}"\n' for i in range(900)
    )
    write(project, "large.py", content)
    project.score_part = lambda state, path, score: (
        2.0 if state["source_files"][0]["start_character"] else 0.0
    )
    cat = catenator(project)
    output = cat.catenate(jev_full_source=True)
    assert len(project.calls) > 1
    parts = [
        entry
        for call in project.calls
        for entry in call["state"]["source_files"]
    ]
    assert (
        "".join(
            part["content"]
            for part in sorted(parts, key=lambda part: part["start_character"])
        )
        == content
    )
    assert cat.last_jev_scores["general"]["large.py"]["score"] == 2.0
    assert 'entry_899 = "漢字🌍 899"' in output
    for call in project.calls:
        assert (
            cat.count_tokens(
                json.dumps(
                    call["state"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            <= 3000
        )


def test_question_batches_fit_complete_requests_and_score_every_file(
    project, monkeypatch
):
    monkeypatch.setattr(jev, "JEV_REQUEST_TOKEN_LIMIT", 2500)
    for i in range(48):
        write(
            project, f"module_{i}.py", f"def action_{i}():\n    return {i}\n"
        )
    cat = catenator(project)
    cat.catenate(use_jev=True)
    assert len(project.calls) > 1
    assert set(cat.last_jev_scores["general"]) == set(project.general)
    for call in project.calls:
        assert (
            cat.count_tokens(
                json.dumps(
                    call,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            <= 2500
        )


@pytest.mark.parametrize("full_source", [False, True])
def test_prompt_promotes_ignored_files_and_reuses_general_cache(project, full_source):
    write(project, "core.py", 'def core():\n    return "CORE_BODY"\n')
    write(
        project,
        "rare.py",
        'def rare_feature():\n    return "RARE_BODY"\n',
        0.0,
    )
    query = "Explain the rare feature"
    project.queries[query] = {"core.py": 0.0, "rare.py": 2.0}
    cat = catenator(project)
    output = cat.catenate(
        use_jev=True, prompt=query, token_limit=1000, jev_full_source=full_source
    )
    assert "RARE_BODY" in output and "CORE_BODY" not in output
    query_call = project.calls[-1]
    assert query_call["state"]["query"] == query
    if full_source:
        assert "query" not in project.calls[0]["state"]
        assert (
            "# rare.py" not in query_call["state"]["general_project_context"]
        )
    else:
        assert "general" not in cat.last_jev_scores
        assert "general_project_context" not in query_call["state"]
        assert {e["path"] for e in query_call["state"]["source_files"]} == {
            "core.py", "rare.py"
        }
        assert "CORE_BODY" not in json.dumps(project.calls)
        assert "RARE_BODY" not in json.dumps(project.calls)
    assert len(query_call["questions"]) == 2
    for report in cat.last_jev_report:
        assert report["input_tokens"] == 1234
        assert report["estimated_cost_usd"] == pytest.approx(
            1234 * 0.042 / 1_000_000
        )
    assert cat.catenate(
        use_jev=True, prompt=query, token_limit=1000, jev_full_source=full_source
    ) == output
    passes = 2 if full_source else 1
    assert len(project.calls) == passes
    assert all(
        report["cache_hit"]
        and report["input_tokens"] == 0
        and report["estimated_cost_usd"] == 0
        for report in cat.last_jev_report
    )
    cat.catenate(
        use_jev=True, prompt="Explain the core", jev_full_source=full_source
    )
    assert len(project.calls) == passes + 1
    assert [report["cache_hit"] for report in cat.last_jev_report] == (
        [True, False] if full_source else [False]
    )


@pytest.mark.parametrize("change", ["edit", "rename", "delete"])
def test_source_changes_refresh_prompt_but_only_new_paths_need_general_scores(
    project, change
):
    write(project, "main.py", "def main():\n    return 1\n")
    target = write(
        project, "unused.py", 'def feature():\n    return "old"\n', 0.0
    )
    cat = catenator(project)
    cat.catenate(jev_full_source=True, prompt="Describe the entry point")
    if change == "edit":
        target.write_text('def feature():\n    return "new"\n')
    elif change == "rename":
        target.rename(target.with_name("renamed.py"))
        project.general["renamed.py"] = project.general.pop("unused.py")
    else:
        target.unlink()
        del project.general["unused.py"]
    cat.catenate(jev_full_source=True, prompt="Describe the entry point")
    assert len(project.calls) == (4 if change == "rename" else 3)
    assert cat.last_jev_report[0]["cache_hit"] == (change != "rename")
    assert not cat.last_jev_report[1]["cache_hit"]
    assert set(cat.last_jev_scores["prompt"]) == set(project.general)


def test_general_cache_uses_short_hashes_and_only_scores_missing_files(
    project,
):
    content = 'def main():\n    return "original"\n'
    source = write(project, "main.py", content)
    cat = catenator(project)
    cat.catenate(use_jev=True)
    cache = next(jev.JEV_CACHE_DIR.glob("*/general-ratings-*.json"))
    digest = json.loads(cache.read_text())["files"]["main.py"]["hash"]
    assert digest == hashlib.sha256(content.encode()).hexdigest()[:16]
    source.write_text('def main():\n    return "current"\n')
    project.general["main.py"] = 0.0
    output = cat.catenate(use_jev=True)
    assert 'return "current"' in output
    assert len(project.calls) == 1
    assert json.loads(cache.read_text())["files"]["main.py"]["hash"] == digest
    write(project, "new.py", "def new():\n    return 2\n")
    cat.catenate(use_jev=True)
    assert len(project.calls) == 2
    assert len(project.calls[-1]["questions"]) == 1
    assert (
        'file "new.py",'
        in next(iter(project.calls[-1]["questions"].values()))["instructions"]
    )
    assert cat.last_jev_scores["general"]["main.py"]["score"] == 2.0
    assert cat.last_jev_report[0]["cached_files"] == 1
    assert cat.last_jev_report[0]["scored_files"] == 1
    assert "main.py" in json.dumps(project.calls[-1]["state"])
    cat.catenate(use_jev=True, refresh_scores=True)
    assert len(project.calls) == 3
    assert cat.last_jev_scores["general"]["main.py"]["score"] == 0.0
    assert json.loads(cache.read_text())["files"]["main.py"]["hash"] != digest


def test_skipped_overrides_never_reach_jev_or_final_output(project):
    write(project, "main.py", "def main():\n    return 1\n")
    write(project, "skip.py", 'VALUE = "DO_NOT_TRANSMIT"\n')
    cat = catenator(project)
    output = cat.catenate(
        use_jev=True, file_overrides={"skip.py": None}, prompt="Explain this"
    )
    assert "DO_NOT_TRANSMIT" not in json.dumps(project.calls)
    assert "skip.py" not in json.dumps(project.calls)
    assert "# skip.py" not in output


def test_refresh_and_corrupt_cache_trigger_fresh_scores(project):
    write(project, "main.py", "def main():\n    return 1\n")
    cat = catenator(project)
    cat.catenate(use_jev=True)
    for cache in jev.JEV_CACHE_DIR.glob("*/*.json"):
        cache.write_text("[]")
    cat.catenate(use_jev=True)
    cat.catenate(use_jev=True, refresh_scores=True)
    assert len(project.calls) == 3


def test_changing_filters_retains_general_ratings(project):
    write(project, "main.py", "def main():\n    return 1\n")
    write(project, "guide.md", "# Guide\nProject notes.\n")
    cat = catenator(project)
    cat.catenate(use_jev=True)
    cat.ignore_extensions = ["md"]
    cat.catenate(use_jev=True)
    assert set(cat.last_jev_scores["general"]) == {"main.py"}
    cat.ignore_extensions = []
    cat.catenate(use_jev=True)
    assert len(project.calls) == 1
    assert set(cat.last_jev_scores["general"]) == {"main.py", "guide.md"}


def test_outline_prompt_skips_unused_general_scoring(project):
    write(project, "core.py", 'def core():\n    return "CORE_BODY"\n')
    write(
        project,
        "rare.py",
        'def rare_feature():\n    return "RARE_BODY"\n',
        0.0,
    )
    query = "Explain the rare feature"
    project.queries[query] = {"core.py": 0.0, "rare.py": 2.0}
    cat = catenator(project)
    output = cat.catenate(prompt=query, token_limit=1000)
    assert "RARE_BODY" in output and "CORE_BODY" not in output
    assert len(project.calls) == 1
    assert project.calls[0]["state"]["query"] == query
    assert set(cat.last_jev_scores) == {"prompt"}
    assert not list(jev.JEV_CACHE_DIR.glob("*/general*.json"))


def test_cli_prompt_alone_and_refresh_accepted(project, monkeypatch, capsys):
    write(project, "core.py", 'def core():\n    return "CORE_BODY"\n')
    write(
        project,
        "rare.py",
        'def rare_feature():\n    return "RARE_BODY"\n',
        0.0,
    )
    query = "Explain the rare feature"
    project.queries[query] = {"core.py": 0.0, "rare.py": 2.0}
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "catenator",
            str(project.root),
            "--prompt",
            query,
            "--token-limit",
            "1000",
        ],
    )
    main()
    out = capsys.readouterr().out
    assert "RARE_BODY" in out and "CORE_BODY" not in out
    assert len(project.calls) == 1
    assert project.calls[0]["state"]["query"] == query

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "catenator",
            str(project.root),
            "--prompt",
            query,
            "--refresh-scores",
            "--token-limit",
            "1000",
        ],
    )
    main()
    assert len(project.calls) == 2

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "catenator",
            str(project.root),
            "--jev",
            "--prompt",
            query,
            "--token-limit",
            "1000",
        ],
    )
    main()
    assert len(project.calls) == 2


@pytest.mark.parametrize(
    "args",
    [
        ["--prompt", ""],
        ["--prompt", "  "],
        ["--jev", "--prompt", "  "],
        ["--refresh-scores"],
        ["--jev-token-limit", "-1"],
    ],
)
def test_cli_rejects_invalid_jev_options_before_requests(
    project, monkeypatch, args
):
    monkeypatch.setattr(sys, "argv", ["catenator", str(project.root), *args])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert project.calls == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"prompt": ""},
        {"prompt": "  "},
        {"use_jev": True, "prompt": "  "},
        {"refresh_scores": True},
        {"jev_token_limit": -1},
        {"jev_token_limit": 1.5},
        {"jev_token_limit": True},
    ],
)
def test_catenate_rejects_invalid_jev_options_before_requests(project, kwargs):
    cat = catenator(project)
    with pytest.raises(ValueError):
        cat.catenate(**kwargs)
    assert project.calls == []


@pytest.mark.parametrize("full_source", [False, True])
def test_watch_retains_prompt_and_preserves_output_on_failure(
    project, monkeypatch, capsys, full_source
):
    source = write(project, "main.py", 'def main():\n    return "original"\n')
    output = project.root / "context.md"
    cat = catenator(project)
    handler = CatenatorEventHandler(
        cat,
        str(output),
        cooldown=0,
        token_limit=300,
        prompt="Explain main",
        jev_full_source=full_source,
    )
    handler.update_output()
    original = output.read_text()
    assert cat.count_tokens(original) <= 300
    assert project.calls[-1]["state"]["query"] == "Explain main"
    assert cat.last_jev_report[-1]["input_mode"] == (
        "full" if full_source else "outlines"
    )
    assert "context.md" not in json.dumps(project.calls)
    source.write_text('def main():\n    return "changed"\n')

    def fail(*args, **kwargs):
        raise JevError("test failure")

    monkeypatch.setattr(jev.JevClient, "evaluate", fail)
    handler.update_output()
    assert output.read_text() == original
    assert "Catenator update failed" in capsys.readouterr().err


@pytest.mark.parametrize("prompt", [None, "Explain the service interface"])
def test_outline_inputs_keep_module_class_and_method_docstrings(project, prompt):
    write(project, "service.py", '''"""Module purpose retained."""
class Service:
    """Class purpose retained."""
    def run(self, value: str) -> str:
        """Method purpose retained."""
        return "BODY_ONLY_SENTINEL" + value
''')
    cat = catenator(project)
    output = cat.catenate(use_jev=True, prompt=prompt)
    assert "BODY_ONLY_SENTINEL" in output
    for call in project.calls:
        payload = json.dumps(call)
        assert "BODY_ONLY_SENTINEL" not in payload
        for text in ("Module purpose retained.", "Class purpose retained.",
                     "Method purpose retained.", "class Service:",
                     "def run(self, value: str) -> str:"):
            assert text in payload
    assert all(r["input_mode"] == "outlines" for r in cat.last_jev_report)


def test_outline_query_batches_reserve_space_and_never_include_bodies(project, monkeypatch):
    monkeypatch.setattr(jev, "JEV_STATE_TOKEN_LIMIT", 3000)
    monkeypatch.setattr(jev, "JEV_PAIR_TOKEN_LIMIT", 3500)
    monkeypatch.setattr(jev, "JEV_REQUEST_TOKEN_LIMIT", 4500)
    for i in range(12):
        content = "\n\n".join(
            f'def action_{i}_{j}(value: str) -> str:\n'
            f'    """Preserve the documented purpose of action {i} {j}."""\n'
            f'    return "BODY_ONLY_{i}_{j}"\n'
            for j in range(15)
        )
        write(project, f"module_{i}.py", content)
    query = "Explain these documented actions. " * 100
    cat = catenator(project)
    output = cat.catenate(prompt=query, token_limit=1000)
    assert cat.count_tokens(output) <= 1000
    query_calls = [c for c in project.calls if "query" in c["state"]]
    assert len(query_calls) > 1
    assert set(cat.last_jev_scores["prompt"]) == set(project.general)
    for call in project.calls:
        assert "BODY_ONLY_" not in json.dumps(call)
        assert cat.count_tokens(jev._json(call["state"])) <= 3000
        assert cat.count_tokens(jev._json(call)) <= 4500
        for key, question in call["questions"].items():
            pair = {**call, "questions": {key: question}}
            assert cat.count_tokens(jev._json(pair)) <= 3500
    assert all(c["state"]["query"] == query for c in query_calls)


def test_input_modes_keep_separate_general_and_prompt_caches(project):
    write(project, "main.py", 'def main():\n    return "SOURCE_BODY"\n')
    cat = catenator(project)
    query = "Explain main"
    outlines = cat.catenate(prompt=query)
    project.general["main.py"] = 0.0
    full = cat.catenate(prompt=query, jev_full_source=True)
    assert len(project.calls) == 3
    assert "SOURCE_BODY" in outlines and "SOURCE_BODY" not in full
    assert cat.catenate(prompt=query) == outlines
    assert cat.catenate(prompt=query, jev_full_source=True) == full
    assert len(project.calls) == 3
    project.general["main.py"] = 2.0
    cat.catenate(prompt=query, jev_full_source=True, refresh_scores=True)
    assert len(project.calls) == 5
    assert cat.catenate(prompt=query) == outlines
    assert len(project.calls) == 5
    assert all(r["cache_hit"] for r in cat.last_jev_report)


def test_full_source_reuses_existing_general_cache_identity(project):
    content = 'def main():\n    return "SOURCE_BODY"\n'
    write(project, "main.py", content, score=0.0)
    path = jev._cache_path(
        project.root, JevSettings(api_key="test-key"), "general-ratings",
        jev._question("", ""), None,
    )
    jev._save_scores(path, {"main.py": {
        "hash": hashlib.sha256(content.encode()).hexdigest()[:16],
        "answer": {"score": 2.0},
    }}, field="files")
    cat = catenator(project)
    assert "SOURCE_BODY" in cat.catenate(jev_full_source=True)
    assert project.calls == []
    assert cat.last_jev_report[0]["cache_hit"]
    assert cat.last_jev_report[0]["input_mode"] == "full"


@pytest.mark.parametrize("prompt", [None, "Explain main"])
def test_cli_full_source_implicitly_enables_jev_and_allows_refresh(project, monkeypatch, capsys, prompt):
    content = 'def main():\n    return "SOURCE_BODY"\n'
    write(project, "main.py", content)
    args = ["catenator", str(project.root), "--full-source", "--refresh-scores"]
    if prompt is not None:
        args.extend(["--prompt", prompt])
    monkeypatch.setattr(sys, "argv", args)
    main()
    assert "SOURCE_BODY" in capsys.readouterr().out
    assert project.calls[0]["state"]["source_files"][0]["content"] == content
    assert len(project.calls) == (2 if prompt else 1)
    if prompt:
        assert "general_project_context" in project.calls[1]["state"]


def test_context_rejection_splits_questions_once_and_caches_complete_scores(
    project, monkeypatch
):
    for index in range(7):
        write(project, f"module_{index}.py", f"VALUE = {index}\n")
    successful_evaluate = jev.JevClient.evaluate
    attempts = []

    def reject_multiple(client, state, questions):
        attempts.append(copy.deepcopy((state, questions)))
        if len(questions) > 1:
            raise JevContextError("context limit")
        return successful_evaluate(client, state, questions)

    monkeypatch.setattr(jev.JevClient, "evaluate", reject_multiple)
    cat = catenator(project)
    cat.catenate(use_jev=True)

    assert set(cat.last_jev_scores["general"]) == set(project.general)
    assert len({jev._json(attempt) for attempt in attempts}) == len(attempts)
    report = cat.last_jev_report[0]
    assert report["requests"] == len(project.general)
    assert report["input_tokens"] == 1234 * len(project.general)
    assert report["context_splits"] == len(project.general) - 1

    attempt_count = len(attempts)
    cat.catenate(use_jev=True)
    assert len(attempts) == attempt_count
    assert cat.last_jev_report[0]["cache_hit"]


def test_runtime_context_rejection_splits_unicode_source_without_loss(
    project, monkeypatch
):
    content = "prefix🌍漢字\n" + "".join(f"row {index}: café 🚀\n" for index in range(80))
    write(project, "large.py", content)
    successful_evaluate = jev.JevClient.evaluate
    attempts = []

    def reject_large_source(client, state, questions):
        attempts.append(copy.deepcopy((state, questions)))
        source = state["source_files"][0]
        if len(source["content"]) > 180:
            raise JevContextError("context limit")
        return successful_evaluate(client, state, questions)

    project.score_part = lambda state, path, score: (
        2.0 if state["source_files"][0]["start_character"] else 0.0
    )
    monkeypatch.setattr(jev.JevClient, "evaluate", reject_large_source)
    cat = catenator(project)
    cat.catenate(jev_full_source=True)

    successful_parts = [
        entry
        for call in project.calls
        for entry in call["state"]["source_files"]
    ]
    assert "".join(
        part["content"]
        for part in sorted(successful_parts, key=lambda part: part["start_character"])
    ) == content
    assert any(part["start_character"] > 0 for part in successful_parts)
    assert cat.last_jev_scores["general"]["large.py"]["score"] == 2.0
    assert len(attempts) > len(project.calls)


def test_full_source_prompt_rejection_splits_context_and_preserves_question(
    project, monkeypatch
):
    for index in range(4):
        write(
            project,
            f"module_{index}.py",
            f'def value_{index}():\n    return "BODY_{index}"\n',
        )
    query = "Explain every value function"
    project.queries[query] = dict(project.general)
    successful_evaluate = jev.JevClient.evaluate
    rejected = []

    def reject_large_prompt_context(client, state, questions):
        context = state.get("general_project_context")
        if context is not None and len(context) > 120:
            rejected.append(copy.deepcopy((state, questions)))
            raise JevContextError("context limit")
        return successful_evaluate(client, state, questions)

    monkeypatch.setattr(jev.JevClient, "evaluate", reject_large_prompt_context)
    cat = catenator(project)
    cat.catenate(prompt=query, jev_full_source=True)

    prompt_calls = [call for call in project.calls if call["state"].get("query")]
    assert rejected and prompt_calls
    expected_questions = rejected[0][1]
    assert all(call["state"]["query"] == query for call in prompt_calls)
    assert {
        question_id
        for call in prompt_calls
        for question_id in call["questions"]
    } == set(expected_questions)
    assert all(
        question == expected_questions[question_id]
        for call in prompt_calls
        for question_id, question in call["questions"].items()
    )
    for question_id in expected_questions:
        assert "".join(
            call["state"]["general_project_context"]
            for call in prompt_calls
            if question_id in call["questions"]
        ) == rejected[0][0]["general_project_context"]
    assert set(cat.last_jev_scores["prompt"]) == set(project.general)


def test_persistent_context_rejection_is_bounded_and_does_not_cache(
    project, monkeypatch
):
    write(project, "main.py", "def main():\n    return 1\n")
    monkeypatch.setattr(jev, "JEV_MAX_CONTEXT_SPLITS", 2)
    successful_evaluate = jev.JevClient.evaluate
    attempts = []

    def reject_all(client, state, questions):
        attempts.append(copy.deepcopy((state, questions)))
        raise JevContextError("context limit")

    monkeypatch.setattr(jev.JevClient, "evaluate", reject_all)
    cat = catenator(project)
    with pytest.raises(JevError, match="after splitting"):
        cat.catenate(use_jev=True)
    assert len(attempts) == 3
    assert not list(jev.JEV_CACHE_DIR.glob("**/*.json"))

    monkeypatch.setattr(jev.JevClient, "evaluate", successful_evaluate)
    cat.catenate(use_jev=True)
    assert project.calls


def test_generic_jev_error_propagates_without_subdivision(project, monkeypatch):
    write(project, "main.py", "def main():\n    return 1\n")
    attempts = []

    def fail(client, state, questions):
        attempts.append((state, questions))
        raise JevError("invalid request")

    monkeypatch.setattr(jev.JevClient, "evaluate", fail)
    with pytest.raises(JevError, match="invalid request"):
        catenator(project).catenate(use_jev=True)
    assert len(attempts) == 1
    assert not list(jev.JEV_CACHE_DIR.glob("**/*.json"))


@pytest.mark.parametrize("limiting_field", ["JEV_PAIR_TOKEN_LIMIT", "JEV_REQUEST_TOKEN_LIMIT"])
def test_single_request_budget_splits_file_evidence_and_scores_it(
    project, monkeypatch, limiting_field
):
    content = "".join(f'line_{index} = "漢字 {index}"\n' for index in range(300))
    write(project, "large.py", content)
    monkeypatch.setattr(jev, "JEV_STATE_TOKEN_LIMIT", 20_000)
    monkeypatch.setattr(jev, "JEV_REQUEST_TOKEN_LIMIT", 20_000)
    monkeypatch.setattr(jev, "JEV_PAIR_TOKEN_LIMIT", 20_000)
    monkeypatch.setattr(jev, limiting_field, 900)
    cat = catenator(project)
    cat.catenate(jev_full_source=True)

    parts = [
        entry
        for call in project.calls
        for entry in call["state"]["source_files"]
    ]
    assert len(parts) > 1
    assert "".join(
        part["content"]
        for part in sorted(parts, key=lambda part: part["start_character"])
    ) == content
    assert set(cat.last_jev_scores["general"]) == {"large.py"}
    assert all(cat.count_tokens(jev._json(call)) <= 900 for call in project.calls)


def test_outline_prompt_recovery_preserves_query_and_all_outline_characters(
    project, monkeypatch
):
    for file_index in range(2):
        content = "\n\n".join(
            f"def action_{file_index}_{item}(value: str) -> str:\n"
            f'    """Document action {file_index} {item}."""\n'
            f'    return "BODY_{file_index}_{item}"\n'
            for item in range(12)
        )
        write(project, f"module_{file_index}.py", content)
    query = "Explain the documented actions"
    project.queries[query] = dict(project.general)
    successful_evaluate = jev.JevClient.evaluate
    initial_query_state = []

    def reject_large_outlines(client, state, questions):
        if state.get("query") == query:
            if not initial_query_state:
                initial_query_state.append(copy.deepcopy(state))
            if len(questions) > 1 or any(
                len(entry["content"]) > 140
                for entry in state["source_files"]
            ):
                raise JevContextError("context limit")
        return successful_evaluate(client, state, questions)

    monkeypatch.setattr(jev.JevClient, "evaluate", reject_large_outlines)
    cat = catenator(project)
    cat.catenate(prompt=query)

    prompt_calls = [call for call in project.calls if call["state"].get("query")]
    expected = {
        entry["path"]: entry["content"]
        for entry in initial_query_state[0]["source_files"]
    }
    actual = {path: "" for path in expected}
    for call in prompt_calls:
        assert call["state"]["query"] == query
        entry = call["state"]["source_files"][0]
        actual[entry["path"]] += entry["content"]
    assert actual == expected
    assert set(cat.last_jev_scores["prompt"]) == set(project.general)


def test_joint_packing_sends_each_outline_once(project, monkeypatch):
    monkeypatch.setattr(jev, "JEV_REQUEST_TOKEN_LIMIT", 1200)
    for index in range(40):
        write(project, f"module_{index:02}.py", f"def action_{index}():\n    return 1\n")
    cat = catenator(project)
    cat.catenate(prompt="Explain these actions")
    sent = [
        entry["path"] for call in project.calls
        for entry in call["state"]["source_files"]
    ]
    assert len(project.calls) > 1
    assert sorted(sent) == sorted(project.general)
    for call in project.calls:
        assert cat.count_tokens(jev._json(call)) <= 1200
        assert call["state"]["scoring_rubric"] == jev.SCORING_RUBRIC
        assert all("scoring_rubric" in q["instructions"] for q in call["questions"].values())


def test_prompt_edit_reuses_other_batches_but_hashes_actual_source(project, monkeypatch):
    monkeypatch.setattr(jev, "JEV_REQUEST_TOKEN_LIMIT", 1200)
    for index in range(30):
        write(project, f"module_{index:02}.py", f"def action_{index}():\n    return 1\n")
    cat = catenator(project)
    query = "Explain these actions"
    cat.catenate(prompt=query)
    initial = list(project.calls)
    assert len(initial) > 1
    target = initial[-1]["state"]["source_files"][-1]["path"]
    path = project.root / target
    path.write_text(path.read_text().replace("return 1", "return 2"))
    project.calls.clear()
    output = cat.catenate(prompt=query)
    assert "return 2" in output
    assert len(project.calls) == 1
    assert project.calls[0] == initial[-1]
    report = cat.last_jev_report[0]
    assert report["scored_files"] == len(initial[-1]["questions"])
    assert report["cached_files"] + report["scored_files"] == len(project.general)
    assert set(cat.last_jev_scores["prompt"]) == set(project.general)
    cat.catenate(prompt=query)
    assert len(project.calls) == 1
    assert cat.last_jev_report[0]["cache_hit"]
    cat.catenate(prompt=query, refresh_scores=True)
    assert len(project.calls) == 1 + len(initial)


def test_completed_prompt_batches_survive_later_failure(project, monkeypatch):
    monkeypatch.setattr(jev, "JEV_REQUEST_TOKEN_LIMIT", 1200)
    for index in range(30):
        write(project, f"module_{index:02}.py", f"def action_{index}():\n    return 1\n")
    successful_evaluate = jev.JevClient.evaluate

    def fail_second(client, state, questions):
        if project.calls:
            raise JevError("network failed")
        return successful_evaluate(client, state, questions)

    monkeypatch.setattr(jev.JevClient, "evaluate", fail_second)
    cat = catenator(project)
    with pytest.raises(JevError, match="network failed"):
        cat.catenate(prompt="Explain actions")
    completed = project.calls[0]
    monkeypatch.setattr(jev.JevClient, "evaluate", successful_evaluate)
    cat.catenate(prompt="Explain actions")
    assert all(call != completed for call in project.calls[1:])
    assert cat.last_jev_report[0]["cached_files"] == len(completed["questions"])
    assert set(cat.last_jev_scores["prompt"]) == set(project.general)


@pytest.mark.parametrize("kwargs", [{"use_jev": True}, {"prompt": "Explain main"}])
def test_input_budget_preflight_prevents_all_api_calls(project, kwargs):
    write(project, "main.py", "def main():\n    return 1\n")
    cat = catenator(project)
    with pytest.raises(JevError, match="--token-limit only limits the output"):
        cat.catenate(jev_token_limit=1, **kwargs)
    assert project.calls == []
    assert not list(jev.JEV_CACHE_DIR.glob("**/*.json"))
    cat.catenate(jev_token_limit=0, **kwargs)
    assert project.calls
    cat.catenate(jev_token_limit=1, **kwargs)
    assert cat.last_jev_report[0]["cache_hit"]
    assert cat.last_jev_report[0]["input_tokens"] == 0


def test_full_source_budget_is_shared_between_stages(project):
    write(project, "main.py", "def main():\n    return 1\n")
    with pytest.raises(JevError, match="1,234 tokens already accounted"):
        catenator(project).catenate(
            prompt="Explain main", jev_full_source=True, jev_token_limit=1400,
        )
    assert len(project.calls) == 1
    assert "query" not in project.calls[0]["state"]


def test_context_recovery_consumes_input_budget(project, monkeypatch):
    write(project, "main.py", "def main():\n    return 1\n")
    attempts = []

    def reject(client, state, questions):
        attempts.append((state, questions))
        raise JevContextError("too big")

    monkeypatch.setattr(jev.JevClient, "evaluate", reject)
    cat = catenator(project)
    with pytest.raises(JevError, match="input budget exceeded"):
        cat.catenate(prompt="Explain main", jev_token_limit=450)
    assert 1 <= len(attempts) <= 2
    assert not list(jev.JEV_CACHE_DIR.glob("**/*.json"))


def test_cli_budget_guard_preserves_output_file(project, monkeypatch, capsys):
    write(project, "main.py", "def main():\n    return 1\n")
    output = project.root / "context.md"
    output.write_text("previous output")
    monkeypatch.setattr(sys, "argv", [
        "catenator", str(project.root), "--prompt", "Explain main",
        "--jev-token-limit", "1", "--output", str(output),
    ])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    assert "input budget exceeded" in capsys.readouterr().err
    assert project.calls == []
    assert output.read_text() == "previous output"


def test_watch_retains_input_budget(project, capsys):
    write(project, "main.py", "def main():\n    return 1\n")
    output = project.root / "context.md"
    output.write_text("previous output")
    handler = CatenatorEventHandler(
        catenator(project), str(output), prompt="Explain main", jev_token_limit=1,
    )
    handler.update_output()
    assert project.calls == []
    assert output.read_text() == "previous output"
    assert "input budget exceeded" in capsys.readouterr().err
