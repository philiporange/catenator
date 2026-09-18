# Jev ranking benchmark — 18 September 2026

Jev with a task prompt retained more of the required source evidence overall
at every tested budget. Neither Jev mode was strictly better than ordinary
Catenator: both lost useful information in individual cases, and general Jev
performed worse at the two larger budgets.

These results support keeping general Jev optional. Prompted ranking helps
with a known task, but file allocation and handling of large files still limit
the result.

## Scope and method

The benchmark tested Catenator commit `f7c5cd9` on three projects, using a
frozen snapshot of the files eligible under normal discovery rules:

| Project | Main languages | Eligible files | Source tokens |
|---|---|---:|---:|
| `archive_manager` | Python, JavaScript | 44 | 128,269 |
| `bottom_audio_player` | JavaScript, CSS | 8 | 10,342 |
| `bunny_cdn` | Rust, Python | 9 | 21,404 |

Token counts use `cl100k_base`. Source counts exclude Catenator's output
framing, overview, and directory tree.

Three tasks were defined for each project before any Jev ranking requests:

| Project | Tasks |
|---|---|
| `archive_manager` | Episode parsing and ordering; English subtitle discovery; audiobook identity and metadata layout |
| `bottom_audio_player` | Download caching and failure handling; playback speed persistence; initial behavior and mobile layout |
| `bunny_cdn` | Download approval checks; upload reservations and commits; analytics log ingestion |

Each task had four questions with answers checked against actual source,
giving 36 facts in total. Sufficient source excerpts were also recorded and
validated before ranking. The questions and expected answers are preserved
in the [benchmark data](jev-benchmark-2026-09-18.json).

Each snapshot was rendered at 6,000, 12,000, and 24,000 tokens in three modes:

- **Ordinary:** deterministic file selection, without Jev.
- **General Jev:** file scores from `jev-1.13.0`, without a task prompt.
- **Jev + prompt:** general ratings followed by ratings for each task's
  instruction, using the general Jev document as context.

All modes used the same structural summaries, with `use_llm=False`. This
isolates the effect of ranking and allocation. AI-generated file summaries
were not part of this comparison.

An isolated cache ensured fresh initial ratings. General ratings were reused
across task prompts, and each set of ratings was reused across the three
output budgets. There were 48 saved contexts: nine ordinary, nine general
Jev, 27 prompted Jev, and three full-source controls. All budgeted outputs
stayed within their limits, including framing and overview.

## Source evidence retained

A check passes when the output contains a predeclared set of source excerpts
sufficient to answer that question. Matching ignores whitespace but retains
punctuation and identifiers. This measures literal evidence retention;
paraphrased information may survive without passing the check.

| Token budget | Ordinary | General Jev | Jev + prompt |
|---|---:|---:|---:|
| 6,000 | 3/36 | 10/36 | **18/36** |
| 12,000 | 16/36 | 10/36 | **24/36** |
| 24,000 | 28/36 | 18/36 | **36/36** |

The project breakdown shows where those changes occurred. Each row has 12
checks, four for each task:

| Project | Budget | Ordinary | General Jev | Jev + prompt |
|---|---:|---:|---:|---:|
| `archive_manager` | 6,000 | 0 | 0 | 8 |
| `archive_manager` | 12,000 | 0 | 0 | 8 |
| `archive_manager` | 24,000 | 4 | 0 | 12 |
| `bottom_audio_player` | 6,000 | 3 | 10 | 10 |
| `bottom_audio_player` | 12,000 | 12 | 10 | 12 |
| `bottom_audio_player` | 24,000 | 12 | 10 | 12 |
| `bunny_cdn` | 6,000 | 0 | 0 | 0 |
| `bunny_cdn` | 12,000 | 4 | 0 | 4 |
| `bunny_cdn` | 24,000 | 12 | 8 | 12 |

Higher totals do not imply strict dominance. Comparing each check with
ordinary mode gives these wins and losses:

| Mode | Budget | Newly retained | Newly lost | Unchanged |
|---|---:|---:|---:|---:|
| General Jev | 6,000 | 9 | 2 | 25 |
| Jev + prompt | 6,000 | 17 | 2 | 17 |
| General Jev | 12,000 | 0 | 6 | 30 |
| Jev + prompt | 12,000 | 8 | 0 | 28 |
| General Jev | 24,000 | 0 | 10 | 26 |
| Jev + prompt | 24,000 | 8 | 0 | 28 |

## Independent comprehension check

The 12,000-token contexts were also given to
`muse-code/muse-spark-1.3` through `https://llm.ph1l.uk/v1`. Each task used
the same four questions across modes. The model received no mode label,
had no tools, and was instructed to answer from the supplied context with
supporting quotations or return an unknown answer.

Requests used temperature 0, low reasoning effort, and a 4,096-token output
limit. There were 27 task calls and three full-source controls, each control
asking all 12 questions for its project. All 30 requests completed normally.
Answers were reviewed against the source-checked rubric; partial answers
and unknowns did not receive a correctness point.

| Context | Correct answers | Correct with sufficient quoted evidence |
|---|---:|---:|
| Ordinary, 12,000 tokens | 17/36 | 16/36 |
| General Jev, 12,000 tokens | 10/36 | 10/36 |
| Jev + prompt, 12,000 tokens | 24/36 | 24/36 |
| Full source | 36/36 | 36/36 |

The extra ordinary-mode answer correctly selected an audiobook's file path
when books share a directory, but its quotation did not establish that
selection rule. It counts toward answer correctness, but not grounded
correctness. Prompted Jev did not answer that question at 12,000 tokens.

Correct answers by project were:

| Project | Ordinary | General Jev | Jev + prompt | Full source |
|---|---:|---:|---:|---:|
| `archive_manager` | 1/12 | 0/12 | 8/12 | 12/12 |
| `bottom_audio_player` | 12/12 | 10/12 | 12/12 | 12/12 |
| `bunny_cdn` | 4/12 | 0/12 | 4/12 | 12/12 |

## What improved and what failed

**Task prompts recovered relevant archive code.** Episode-parsing and
subtitle prompts retained all eight associated checks even at 6,000 tokens.
Ordinary and general Jev output retained none of those eight checks at
6,000 or 12,000 tokens.

**General ratings can prevent useful expansion despite spare budget.**
The audio player's CSS scored 0.97 and Bunny's analytics service scored
1.27. Both fall below the 1.5 threshold required for verbatim inclusion.
At 12,000 tokens, general Jev emitted only 4,999 tokens for the audio player
and 2,582 for Bunny. It lost the mobile breakpoint, playlist height, and
four analytics-ingestion details that ordinary mode retained. Increasing
the budget to 24,000 did not restore these files' implementations because
their scores still capped them at summaries.

**Prompted ranking also had a counterexample.** At 6,000 tokens, the audio
player's layout task retained 2/4 checks with a prompt, compared with 3/4
in ordinary mode. The prompted output retained JavaScript behavior but
lost the CSS breakpoint and playlist-height details. At 12,000 tokens,
the prompted layout output retained all four.

**Large files remain difficult to represent.** Bunny's `src/main.rs`
contains 12,520 source tokens, so it cannot fit verbatim into either smaller
budget. Its structural summary supplies almost no implementation detail.
Prompted download and upload outputs at 12,000 tokens used only 1,389 and
2,085 tokens respectively and failed all eight associated checks. Both
tasks passed at 24,000 tokens. Splitting files for Jev scoring does not
currently provide partial source inclusion in the final output.

**A relevant file can still lose the budget competition.** The archive
metadata prompt rated `audiobook_metadata.py` at 1.91, behind
`audiobook_scanner.py` at 1.98. The metadata task retained none of its four
complete evidence checks at 6,000 or 12,000 tokens, but all four at 24,000.

The failures point toward allowing more source expansion when budget
remains and supporting useful portions of oversized files. These changes
have not been evaluated by this benchmark.

## Cost and cache behavior

Jev returned 449,518 input tokens across 19 API requests. At the configured
price of **$0.042 per million input tokens**, the estimated ranking cost was
**$0.018879756**, approximately **1.89 US cents**.

| Project | Initial general ranking | One new prompt, mean | General + three prompts |
|---|---:|---:|---:|
| `archive_manager` | $0.007063 | $0.002144 | $0.013496 |
| `bottom_audio_player` | $0.000571 | $0.000379 | $0.001709 |
| `bunny_cdn` | $0.001071 | $0.000868 | $0.003675 |

Costs are rounded in this table. They include repeated context and question
text across batches. Changing only the final output budget reused the
ratings without another API request.

Each general and prompted pass was immediately repeated against the same
snapshot. All 12 repetitions made zero API requests, incurred zero
additional Jev cost, returned identical scores, and produced identical
12,000-token outputs.

The Muse comprehension checks are separate from these Jev costs. They
reported 387,599 input tokens and 24,827 output tokens, including reasoning.
The gateway returned no monetary charge, so no dollar estimate is assigned
to the evaluation calls.

## Timing

These measurements cover scoring calls and their local preparation, using
the already-collected snapshot. Discovery and final rendering are excluded:

| Project | Fresh general scoring | New prompt, general cached | Cached prompt |
|---|---:|---:|---:|
| `archive_manager` | Not recorded | 4.29–4.45 s | 2.42–2.43 s |
| `bottom_audio_player` | 1.17 s | 0.90–0.98 s | 0.065–0.086 s |
| `bunny_cdn` | 1.53 s | 1.31–1.40 s | 0.204–0.215 s |

Cached general scoring took about 0.002 seconds for each project. Cached
prompt scoring still builds and identifies its general context, which
accounts for substantial local work on the larger archive project.

## Limits and audit trail

This is one run on three selected projects and nine hand-authored tasks.
It measures source retention and factual comprehension. It does not measure
successful code changes, variation between repeated fresh Jev ratings, or
the quality of AI-generated summaries. Whole-project orientation may value
information outside these task questions.

Cheaper general-ranking inputs could contain only the project tree and
filenames, or paths together with imports, class and function signatures,
and docstrings. Jev can score either representation. These variants have
not been measured here; the general pass in this benchmark received full
eligible source in batches. Structural extraction quality, including the
current gap for Rust, would be part of that comparison.

An initial harness assertion failed because usage reports accumulated
between direct scoring calls. The successful archive general ratings were
reused from the isolated cache after the harness was corrected. Its API
usage was recovered from the saved log; its cold duration was unavailable.
No ranking requests were repeated to recover that measurement.

All 61 eligible source files matched their frozen hashes at the end of the
run. [The durable benchmark data](jev-benchmark-2026-09-18.json) contains
aggregate results, question rubrics, source hashes, scores, timings, and
usage. Full local snapshots, rendered contexts, and raw QA receipts were
saved under `/tmp/catenator-jev-comparison-20260918/`; that temporary archive
is not part of the repository. The monitored run completed successfully
with Bell job `a5faa16a3af7`.
