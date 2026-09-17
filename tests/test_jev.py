"""Verify Jev source coverage, prompt reranking, caching, budgets, and CLI use.

An in-memory scorer returns controlled per-file answers while retaining each
request for inspection. Tests exercise real discovery and rendering without
network access or writes to the user's score cache.
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
from src.catenator.jev_client import JevError


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


def test_scores_control_inclusion_and_strict_output_budgets(project):
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
    output = cat.catenate(use_jev=True)
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
    assert sent == {
        path: (project.root / path).read_text() for path in project.general
    }
    for limit in (1, 100, 250):
        limited = cat.catenate(use_jev=True, token_limit=limit)
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
    output = cat.catenate(use_jev=True)
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
    for i in range(16):
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


def test_prompt_promotes_ignored_files_and_reuses_general_cache(project):
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
    output = cat.catenate(use_jev=True, prompt=query, token_limit=1000)
    assert "RARE_BODY" in output and "CORE_BODY" not in output
    assert "query" not in project.calls[0]["state"]
    assert project.calls[1]["state"]["query"] == query
    assert (
        "# rare.py" not in project.calls[1]["state"]["general_project_context"]
    )
    assert len(project.calls[1]["questions"]) == 2
    for report in cat.last_jev_report:
        assert report["input_tokens"] == 1234
        assert report["estimated_cost_usd"] == pytest.approx(
            1234 * 0.042 / 1_000_000
        )
    assert cat.catenate(use_jev=True, prompt=query, token_limit=1000) == output
    assert len(project.calls) == 2
    assert all(
        report["cache_hit"]
        and report["input_tokens"] == 0
        and report["estimated_cost_usd"] == 0
        for report in cat.last_jev_report
    )
    cat.catenate(use_jev=True, prompt="Explain the core")
    assert len(project.calls) == 3
    assert [report["cache_hit"] for report in cat.last_jev_report] == [
        True,
        False,
    ]


@pytest.mark.parametrize("change", ["edit", "rename", "delete"])
def test_source_changes_refresh_prompt_but_only_new_paths_need_general_scores(
    project, change
):
    write(project, "main.py", "def main():\n    return 1\n")
    target = write(
        project, "unused.py", 'def feature():\n    return "old"\n', 0.0
    )
    cat = catenator(project)
    cat.catenate(use_jev=True, prompt="Describe the entry point")
    if change == "edit":
        target.write_text('def feature():\n    return "new"\n')
    elif change == "rename":
        target.rename(target.with_name("renamed.py"))
        project.general["renamed.py"] = project.general.pop("unused.py")
    else:
        target.unlink()
        del project.general["unused.py"]
    cat.catenate(use_jev=True, prompt="Describe the entry point")
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


@pytest.mark.parametrize(
    "args",
    [["--prompt", "hello"], ["--jev", "--prompt", "  "], ["--refresh-scores"]],
)
def test_cli_rejects_invalid_jev_options_before_requests(
    project, monkeypatch, args
):
    monkeypatch.setattr(sys, "argv", ["catenator", str(project.root), *args])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert project.calls == []


def test_watch_retains_prompt_and_preserves_output_on_failure(
    project, monkeypatch, capsys
):
    source = write(project, "main.py", 'def main():\n    return "original"\n')
    output = project.root / "context.md"
    cat = catenator(project)
    handler = CatenatorEventHandler(
        cat,
        str(output),
        cooldown=0,
        token_limit=300,
        use_jev=True,
        prompt="Explain main",
    )
    handler.update_output()
    original = output.read_text()
    assert cat.count_tokens(original) <= 300
    assert project.calls[-1]["state"]["query"] == "Explain main"
    assert "context.md" not in json.dumps(project.calls)
    source.write_text('def main():\n    return "changed"\n')

    def fail(*args, **kwargs):
        raise JevError("test failure")

    monkeypatch.setattr(jev.JevClient, "evaluate", fail)
    handler.update_output()
    assert output.read_text() == original
    assert "Catenator update failed" in capsys.readouterr().err
