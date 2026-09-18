# Jev ranking benchmark — 18 September 2026

Catenator now uses **outlines with first-line Python docstrings** as Jev's
default input. Use `--full-source` for full-source ranking. `--prompt`
automatically enables Jev with either input mode:

```sh
catenator . --prompt "Explain authentication" --token-limit 12000
catenator . --prompt "Explain authentication" --full-source --token-limit 12000
```

Ordinary Catenator remains local and requires no API credentials. Jev setup,
cache behavior, and cost reporting are documented in the
[README](../README.md#jev-file-scoring).

The measurements below record the original full-source benchmark and two
compact-input comparisons at their stated commits. In the original
comparison, Jev with a task prompt retained more required source evidence
overall at every tested budget. Neither Jev mode was strictly better than
ordinary Catenator: both lost useful information in individual cases, and
general Jev performed worse at the two larger budgets.

These results support keeping general Jev optional. Prompted ranking helps
with a known task, but file allocation and handling of large files still limit
the result.

The [additional three-project comparison](#three-additional-projects) also
qualifies the first compact-input screen: docstrings improved the outlines,
but full-source ranking still gave the best 12k answers on those new projects.

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

Both Jev variants in this original comparison used full-source ranking input,
now selected with `--full-source`.

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
tasks passed at 24,000 tokens. Splitting files for Jev scoring did not
provide partial source inclusion in the final output.

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

Cached general scoring took about 0.002 seconds for each project. In the
full-source pipeline, cached prompt scoring still builds and identifies its
general context, which accounted for substantial local work on the larger
archive project.

## Limits and audit trail

This is one run on three selected projects and nine hand-authored tasks.
It measures source retention and factual comprehension. It does not measure
successful code changes, variation between repeated fresh Jev ratings, or
the quality of AI-generated summaries. Whole-project orientation may value
information outside these task questions.

The original comparison above used full eligible source in batches for
general ranking. A follow-up screen of compact ranking inputs is reported
below.

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

## Follow-up: compact ranking inputs

The same frozen snapshots and 36 checks were used to screen three cheaper
inputs at commit `5fe1e7c`: paths only, existing structural outlines without
Python docstrings, and those outlines with docstrings. Final rendering still
used the original source files and the same inclusion thresholds and budgets.

General importance was scored directly from each compact representation.
Task relevance was also scored directly from that representation plus the
query, without first constructing a general Jev document containing source.
Thus the prompt comparison tests a simpler scoring pipeline as well as a
smaller input. The prior full-source measurements were reused.

**On these original cases, outlines without docstrings were the strongest
quick-win candidate.**
They matched every task-evidence check retained by full-source ranking at
all three budgets, gained four general-evidence checks at 12,000 and 24,000
tokens, and reduced combined estimated Jev cost by **66.9%**.

| Ranking input | General evidence at 12k | Task evidence at 6k / 12k / 24k | Cost: three general + nine task passes |
|---|---:|---:|---:|
| Full source | 10/36 | 18 / 24 / 36 | $0.018880 |
| Paths only | 10/36 | 10 / 24 / 32 | $0.002317 |
| Outlines without docstrings | **14/36** | **18 / 24 / 36** | **$0.006251** |
| Outlines with docstrings | 10/36 | 18 / 24 / 36 | $0.007098 |

For outlines without docstrings, general-ranking input usage fell from
207,240 to 36,676 tokens, an **82.3% reduction**. Task-ranking usage fell
from 242,278 to 112,159 tokens, a **53.7% reduction**. The archive's general
ranking fit all 44 file outlines in one request, compared with eight requests
for its full source.

Paths alone were cheaper still, but had meaningful regressions. At 12,000
tokens they recovered four audiobook-metadata checks while losing four
episode-parsing checks, so the equal total concealed a tradeoff. At 6,000
they lost eight checks relative to full-source task ranking, and at 24,000
they lost four. General path-only ranking matched the full-source general
checks at all three budgets, though both remained worse than ordinary mode
at the larger budgets.

The predeclared screen selected by 12,000-token task total and then cost,
which selected paths only. Inspection of the paired losses and the other
budgets led to choosing outlines as the more promising implementation
candidate. Both paths and outlines were checked with the same blind Muse
QA protocol at 12,000 tokens: each scored **24/36**, matching the prior
full-source task score. Paths answered a different set of questions;
outlines answered the same set as full source. There were 17 new QA calls
and one exact context/question reuse, with all responses completing normally.

Median task-scoring times, including compact extraction and request
preparation but excluding discovery and final rendering, were:

| Project | Full source | Outlines without docstrings |
|---|---:|---:|
| `archive_manager` | 4.35 s | 2.96 s |
| `bottom_audio_player` | 0.91 s | 0.92 s |
| `bunny_cdn` | 1.38 s | 0.93 s |

Docstrings added cost without improving the measured task results in this
first screen. It tested first-line Python docstrings rather than exhaustive
documentation. The extractor also retains some JavaScript variable
declarations and lacks Rust/CSS structural support, so these are practical
existing outlines rather than complete language-independent skeletons.

The screen made 32 new Jev requests for an estimated **$0.015090264**.
Four identical outline/docstring payloads for the audio player reused their
responses. The comparison table charges each variant its standalone token
usage, so reuse does not artificially lower the docstring option's cost.
Muse QA costs are separate. All 108 compact outputs respected their budgets.

This was a quick screen on the original cases, with no fresh-rating variance
or held-out projects measured. The screen ran before outlines became the
default. [Compact-input results and usage](jev-compact-inputs-2026-09-18.json)
are preserved alongside the original benchmark data; full local receipts
remain under `/tmp/catenator-jev-inputs-20260918/`.

## Three additional projects

A second comparison at commit `c259c9d` used three projects that were not in
the first screen. Their larger Python codebases give docstrings more scope
to affect the results.

| Project | Eligible files | Source tokens | Tasks |
|---|---:|---:|---|
| `supervisor` | 49 | 91,554 | Service lifecycle; incremental logs; error sluice |
| `books_server` | 70 | 160,403 | Authentication; cover refresh; generated fallback covers |
| `bell` | 34 | 65,380 | Monitoring limits; error callbacks; progress capture |

Nine task prompts and 36 source-backed facts were frozen before scoring.
Each variant received fresh general ratings and three prompt-specific
ratings per project. Final contexts always used the original source, the
same renderer and thresholds, structural summaries, and 6k/12k/24k budgets.

The three inputs were full source, existing outlines with first-line Python
docstrings, and those outlines with docstring lines removed. The existing
80-line/12,000-character outline bounds were retained. Removing docstrings
happened after extraction, so it did not recover declarations already lost
to that bound. Non-Python files used the same existing extractors in both
outline variants. `books_server` needed two compact request batches with
docstrings and one without them; the other projects needed one each.

As in the first screen, full-source prompt scoring used the general Jev
source document, while compact prompt scoring used outlines plus the query
directly. This compares input representation and prompt pipeline together.
Compact packing reserved space for the query before filling its source
state; the 28k state, 30k state/question, and 60k request limits stayed fixed.

**The earlier quality-equivalence result did not hold on these projects.**
Full source gave the best task evidence and factual answers at 12k.
Docstrings recovered useful information compared with stripped outlines,
and their variant retained the most task evidence at 6k and 24k.

All evidence and QA counts below are out of 36. Cost is estimated Jev usage
for three general plus nine prompt passes, at $0.042/M returned input tokens.

| Ranking input | General evidence at 12k | Task evidence at 6k / 12k / 24k | Correct answers at 12k | Estimated Jev cost |
|---|---:|---:|---:|---:|
| Full source | 7 | 26 / **34** / 34 | **35** | $0.036695 |
| Outlines with docstrings | 7 | **28** / 32 / **36** | 32 | $0.016671 |
| Outlines without docstrings | 3 | 26 / 29 / 33 | 30 | **$0.014328** |

Per-project correct answers at 12k show where the differences occurred:

| Project | Full source | With docstrings | Without docstrings |
|---|---:|---:|---:|
| `supervisor` | **12/12** | 8/12 | 8/12 |
| `books_server` | 11/12 | **12/12** | 11/12 |
| `bell` | **12/12** | **12/12** | 11/12 |

In Supervisor's lifecycle task, both outline variants ranked `process.py`
above the 8,623-token `main.py`. The 12k allocation kept the process
implementation and summarized the API file, losing four exact endpoint and
polling facts. Full-source ranking put `main.py` first and capped
`process.py` at a summary. Both outline variants recovered all four facts at
24k. At 12k, Muse answered endpoint questions with the lower-level process
methods' tuple/bool results; the stripped-outline context also produced an
incorrect 10-second polling interval instead of 30 seconds.

Docstrings helped in two other places. For Books Server authentication,
`routes/auth.py` scored 1.65 with docstrings, crossing the verbatim threshold
of 1.5; full-source scoring gave it 1.14 and stripped outlines 1.48. That
recovered two literal checks and one additional correct answer. Bell's
callback task similarly raised `db.py` from 1.33 without docstrings to 1.58
with them, retaining the implementation of shared callback deduplication.
These examples depend on score thresholds; repeated fresh ratings would be
needed to measure their stability.

The equal general-context total of 7/36 at 12k also concealed a tradeoff:
docstring outlines gained four Supervisor sluice facts and lost four Bell
progress-capture facts. General evidence at 24k was 17/36 for full source,
11/36 with docstrings, and 9/36 without. These are task-fact checks on a
general context, not an independent measure of architectural understanding.

The answer checks used `muse-code/muse-spark-1.3` at
`https://llm.ph1l.uk/v1`, with the same context-only questions, no mode labels,
temperature 0, low reasoning effort, and a 4,096-token output cap. All 30
requests completed normally: 27 task contexts plus three complete-source
controls. The controls answered all 36 facts correctly. Null, incorrect,
and incomplete answers scored zero; all 144 answers were reviewed against
the frozen expected answers and supplied source.

Factual grading is separate from strict literal-quotation validation.
Correct answers with automatically matching quotes were 29/36 for full
source, 30/36 with docstrings, and 29/36 without; controls scored 23/36 on
that stricter check. Several correct responses wrapped excerpts in extra
quotes, omitted source comment markers, or mistyped a parenthesis. Those
receipts were preserved without repair or another model call. Literal
evidence retention is also a lower bound: for example, the middleware's
comment explained user reuse even when the route implementation was absent.

### Cost and timing on the new projects

| Ranking input | General input tokens | Prompt input tokens | General cost | Prompt cost |
|---|---:|---:|---:|---:|
| Full source | 419,918 | 453,764 | $0.017637 | $0.019058 |
| With docstrings | 97,969 | 298,952 | $0.004115 | $0.012556 |
| Without docstrings | 84,053 | 257,086 | $0.003530 | $0.010798 |

Combined Jev cost fell **54.6% with docstrings** and **61.0% without**.
Once general ratings are cached, the relevant prompt-only savings are
smaller: **34.1%** and **43.3%**, respectively. Keeping docstrings cost
16.4% more than stripping them across this workload, and improved both
evidence retention and answer accuracy.

Median fresh task-scoring times include local input preparation and API
calls, excluding discovery and final rendering:

| Project | Full source | With docstrings | Without docstrings |
|---|---:|---:|---:|
| `supervisor` | 3.91 s | 2.84 s | 2.52 s |
| `books_server` | 5.39 s | 4.95 s | 4.47 s |
| `bell` | 2.52 s | 1.78 s | 1.63 s |

The complete comparison made 54 new Jev requests, using 1,611,742 returned
input tokens for an estimated **$0.067693164**. All 12 full-source warm
repeats made zero scoring requests and preserved scores and 12k contexts.
QA used 592,582 input and 33,487 output tokens; its gateway receipts did not
report dollar charges, so QA cost is excluded from the Jev figures.

All 108 comparative contexts passed independent token-budget and hash
checks, and all 153 eligible source files still matched the frozen
snapshot. The run completed in 5m24s with Bell job `58f8e037c785`; Bell
made no monitoring-model requests. This comparison also preceded the default
change. [The durable results](jev-outline-holdout-2026-09-18.json) preserve
the cases, source hashes, usage, timing, output checks, relevant scores,
paired differences, and reviewed QA answers. Complete local receipts and
the harness are under `/tmp/catenator-jev-holdout-20260918/`.

These additional projects favored docstring outlines among the compact
inputs, while full source gave the best answer accuracy at the 12k budget
in this run. Docstring outlines are now the default Jev input, with
`--full-source` available for the full-source pipeline. This is one rating
per project/task on selected Python-heavy projects, with no fresh-rating
variance or code-change success measurement. The reversal between 12k and
24k makes a universal superiority claim premature.
