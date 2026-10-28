# Catenator

Catenator prepares a codebase for an agent's first read. It combines an
automatic project overview, a directory tree, and source files in one document.
With a token limit, it fits structural summaries and selected source into the
budget. The default mode runs locally without AI or API credentials.

## Features

- Concatenate code files from a specified directory
- Include or exclude specific file extensions
- Include a directory tree structure
- Include README files in the output
- Output to file, clipboard, or stdout
- gitignore-style .catignore files
- Automatic project facts from READMEs, manifests, and Python source
- Source locations on structural summaries and project facts
- Strict token budgets with coverage across source directories
- Jev ranking from outlines with docstrings, with full source available as an option
- Task-specific ranking with `--prompt`, cached ratings, and API cost reporting

## Installation

Requires Python 3.9 or later. From this checkout, install with token counting
for budgeted output:

```sh
pip install -e '.[token_counting]'
```

Use the `jev` extra for Jev ranking, the `summarize` extra for AI summaries,
or both: `pip install -e '.[jev,summarize]'`. Both extras include token
counting. API credentials are only needed for the corresponding AI feature.

## Usage

### As a Command-Line Tool

Basic usage, with a budget for the complete output:

```sh
catenator /path/to/your/project --token-limit 6000
```

Omit `--token-limit` to include all selected source without a budget. For a
known task, use `--prompt "Explain authentication" --token-limit 12000`.
This enables Jev ranking from outlines with docstrings; add `--full-source`
to use full source for ranking. See [Jev file scoring](#jev-file-scoring) for
credentials, caching, and costs.

Options:

- `--output FILE`: Write output to a file instead of stdout
- `--clipboard`: Copy output to clipboard
- `--title TEXT`: Set the output document's title
- `--no-tree`: Disable directory tree generation
- `--no-readme`: Exclude README files from the output
- `--no-overview`: Exclude the automatic project overview
- `--include EXTENSIONS`: Comma-separated list of file extensions to include (replaces defaults)
- `--ignore EXTENSIONS`: Comma-separated list of file extensions to ignore
- `--count-tokens`: Output approximation of how many tokens in output (tiktoken cl100k_base)
- `--watch`: Watch for changes and update output file automatically (requires --output)
- `--ignore-tests`: Leave out tests from the concatenated output
- `--include-minified`: Include minified/generated files (skipped by default)
- `--include-hidden`: Include hidden files and directories, subject to ignore rules
- `--build NAME`: Select a named build from `.catconfig.yaml`
- `--token-limit N`: Keep output under N tokens by summarizing least important files
- `--jev-token-limit N`: Limit estimated Jev input tokens across a run (default 500,000; `0` disables the limit); separate from the output budget
- `--llm`: Use AI for richer summaries when using --token-limit (requires the `summarize` extra)
- `--jev`: Use Jev with outlines and docstrings to score whether each file should be verbatim, summarized, or ignored
- `--full-source`: Send full source to Jev instead of outlines; enables Jev automatically
- `--prompt TEXT`: Rank files for an instruction or query; enables Jev automatically
- `--refresh-scores`: Recompute scores for the selected Jev input mode (requires `--jev`, `--prompt`, or `--full-source`)

Example:

```sh
catenator /path/to/your/project --output concatenated.md --include py,js,ts
```

### As a Python Module

You can also use Catenator in your Python scripts:

```python
from catenator import Catenator

catenator = Catenator(
    directory='/path/to/your/project',
    include_extensions=['py', 'js', 'ts'],
)
result = catenator.catenate(token_limit=6000)
print(result)
```

### Automatic Project Overview

The overview appears by default. It reports language counts, the README's
description, declared package commands and entry points, dependencies, and a
Python module map with definitions and internal imports. Facts include source
paths and line numbers where available. Manifests and source are inspected
statically; Catenator never executes project code or the commands it discovers.

Default discovery includes JSON, YAML, TOML, JSX/TSX and other source formats,
plus files such as `requirements*.txt`, `Makefile`, `Dockerfile`, `go.mod`,
and nested READMEs. GitHub workflow YAML and `.gitlab-ci.yml` are included
without enabling all hidden files. `.catignore` still applies, and secret
`.env` files remain excluded. An explicit `--include` replaces the default
source and manifest selection; README inclusion is controlled separately.

Structural summaries preserve complete Python signatures, decorators,
imports, selected constants and docstrings, with source line references.
JavaScript/TypeScript declarations, document headings, JSON keys, and
configuration sections have lightweight extractors. Unsupported formats are
identified explicitly when full source does not fit.

## .catignore File

The .catignore file allows you to specify files and directories that should be excluded from the concatenation process. The syntax is like .gitignore files.

Catenator ships with a comprehensive `default.catignore` covering build
artifacts, vendored libraries, lockfiles, coverage reports, generated docs,
media files, and ML artifacts. In normal mode, a project's `.catignore` adds
patterns on top of these defaults. Named builds supply their own filters.

### Syntax

Lines starting with # are treated as comments.
Blank lines are ignored.
Patterns can include filenames, directories, or wildcard characters.
Patterns without a slash match at any depth, so `node_modules/` excludes
`frontend/node_modules/` too. Patterns containing a slash are anchored to
the project root.

### Examples

```
# Ignore all JavaScript files
*.js

# Ignore specific file
ignored_file.txt

# Ignore entire directory (at any depth)
ignored_dir/
```

### Always-ignored directories

Vendored and generated directories (`node_modules`, `__pycache__`, `venv`,
`.venv`, `.git`, and common tool caches) are always excluded at any depth —
in every mode, including `--build` and `--include-hidden` — so they can
never swamp the output.

### Minified and generated files

Files containing any line longer than 2000 characters are treated as
minified or generated (webpack bundles, vendored frameworks, embedded data
blobs, scraped pages) and skipped. Prose formats (`md`, `txt`, `rst`) are
exempt since they may contain unwrapped paragraphs. Use `--include-minified`
to disable this check.

## .catconfig.yaml for Custom Builds

For more complex configurations, you can define custom "builds" in a `.catconfig.yaml` file in your project's root directory. This allows you to specify multiple sets of whitelisted and blacklisted files.

### `--build` Option

To use a build, use the `--build` command-line option:
```
catenator /path/to/your/project --build <build_name>
```

Builds select files using their `whitelist` and `blacklist`, including formats
outside the default extension list. `.catignore` and the hidden-file filter are
bypassed in build mode. Always-ignored directories, explicit `--ignore`
extensions, `--no-readme`, and the minified-file check still apply.

### Example `.catconfig.yaml`

Here is an example of a `.catconfig.yaml` file with two builds, `frontend` and `backend`:
```yaml
builds:
  frontend:
    whitelist:
      - "src/frontend/"
      - "README.md"
    blacklist:
      - "src/frontend/node_modules/"
  backend:
    whitelist:
      - "src/backend/"
      - "requirements.txt"
    blacklist:
      - "*.log"
```

In this example:

- `catenator . --build frontend` will concatenate all files in `src/frontend/` (except `node_modules`) and the `README.md` file.
- `catenator . --build backend` will concatenate all files in `src/backend/` and the `requirements.txt` file, excluding any `.log` files.

## Token Limit and Summarization

For a single onboarding document with a strict token budget:

```
catenator /path/to/project --token-limit 10000
```

This will:

1. Discover and read selected files once, pruning ignored directories.
2. Build a bounded overview and tree, reserving room for a coverage report.
3. Rank files using project metadata, entry points, public package wiring,
   and incoming Python imports, with stable path ordering for ties.
4. Include compact outlines across directories, then expand important files
   into structural summaries or full source as space permits.
5. Fit complete sections within the limit, including headers and fences.

The coverage report counts full files, summaries, outlines, and omitted files,
along with unreadable and minified files. Extremely small budgets may only fit
a title, or no text. Counts use `cl100k_base`; other tokenizers may differ.
Status messages and `--count-tokens` go to stderr so redirected stdout contains
only the document. Watch updates use the same budget and exclude the output
file from subsequent discovery.

The default summaries are automatic structural extracts. The optional AI
backend remains available with the `summarize` extra and `--llm`:

```
pip install -e '.[summarize]'
catenator /path/to/project --token-limit 10000 --llm
```

By default, `--llm` uses `muse-code/muse-spark-1.3` through
`https://llm.ph1l.uk/v1`. Set `LLM_API_KEY` in the target project's `.env` or
in your environment; `LLM_BASE_URL` is optional. Dedicated
`CATENATOR_SUMMARIZER_MODEL`, `CATENATOR_SUMMARIZER_API_KEY`, and
`CATENATOR_SUMMARIZER_BASE_URL` overrides take precedence. Summary cache
validation checks the file contents, endpoint, and model; changing any of
these regenerates the summary.

Files are labeled `(summary)` or `(outline)` when reduced. The automatic CLI
extracts structure from its in-memory source snapshot; optional AI file
summaries are cached in `~/.catenator/summaries/`.

## Jev file scoring

Install token counting and set `TYPESAFE_API_KEY` in your environment,
`~/.env`, or the target project's `.env`:

```sh
pip install -e '.[jev]'
catenator /path/to/project --jev --token-limit 12000
catenator /path/to/project --token-limit 6000 \
  --prompt "Fix the parser's handling of escaped quotes"
catenator /path/to/project --full-source --token-limit 12000
catenator /path/to/project --full-source --token-limit 6000 \
  --prompt "Fix the parser's handling of escaped quotes"
```

By default, Jev receives each eligible file's path and bounded structural
outline, including first-line Python module, class, and function docstrings.
Outlines preserve signatures, imports, and selected constants; they are
limited to 80 lines and 12,000 characters per file. Other formats use the
existing lightweight extractors described above. Jev asks a compact scoring
question for each file, with one shared rubric per batch. Evidence and questions
are packed together, avoiding repeated source when question text fills a request.
The discovery snapshot and ignore rules are the same as normal Catenator.

Use `--full-source` to send every eligible file's full contents instead.
It enables Jev automatically, so `--jev` is optional alongside it. The selected
ranking input is sent to the configured TypeSafe endpoint; final rendering
always uses the original source, regardless of the ranking input.

Scores use three ordered levels: 0 means ignore, 1 means summarize, and 2
means verbatim. Fractional scores below 0.5 omit the file body; scores from
0.5 to below 1.5 cap it at a summary; scores of 1.5 or higher
allow full source. The overview and directory tree can still mention omitted
files. `--token-limit` can further reduce or omit files to fit the output.
Without a token limit, the score's inclusion decision still applies.

For larger projects, scoring input is packed into at most 24,000-token states
with a shared overview built from that same input representation. A query's
space is reserved in each outline batch before packing. Inputs exceeding a
state are split without dropping characters; their highest part score
determines inclusion. Every file is evaluated, but each judgment sees only
its batch plus shared project context. Request packing also budgets
question text: up to 26,000 estimated tokens for state plus one question and
48,000 for state plus all questions. These counts use `cl100k_base` and reserve
headroom for Jev's different tokenizer and question framing. If Jev still
rejects the size, Catenator splits the questions and retains only their candidate
evidence, then reduces shared context
and splits candidate text as needed. Candidate text keeps every character,
the query remains intact, and the highest part score determines inclusion.
Recovery stops after 12 subdivisions along a failing branch and reports an
actionable error if the query or metadata cannot fit. Other API failures are
reported separately and do not trigger size recovery.

`--prompt` enables Jev automatically and scores all candidate outlines directly
against the query. Outline prompts skip general scoring entirely: those ratings
do not contribute to query judgments. Query ratings are independent of general
importance, so a generally ignored file can still be selected for a specific task.
Use `--jev` without a prompt when you want general architecture ratings.

With `--full-source`, prompt scoring first obtains or reuses general ratings,
then builds a general Jev source document
with a high context budget (up to approximately 23,000 tokens, reserving
space for the query and formatting). Each query question also includes a
bounded structural description of its candidate, including candidates absent
from the general document. Final output uses the original source and prompt
scores under your requested output budget. Jev does not generate summaries;
`--llm` optionally enables the text-summary backend for final output.

Scores are cached outside the project in `~/.catenator/jev/`. General ratings
are stored per relative file path with a **16-character SHA-256 prefix** of
the contents that were rated. Existing files keep their general rating when
edited; only paths missing from the cache are automatically scored. Adding a
file asks Jev only about the new file, with the selected project representation
as context. Removing or excluding a file needs no API call; its rating
remains available if the path returns. Previously unseen renamed paths are
new candidates.
Scoring instructions and backend settings identify separate general caches.
Outlines and full source have separate general and prompt caches. Switching
between them reuses the matching cache. Both modes record hashes of actual
source, not hashes of outlines.

The short hash records which contents were rated; it does not force a new
general judgment after routine edits. Use `--refresh-scores` to reassess the
selected scoring passes, for example after a change of architectural role.
Final output always uses current source. Prompt caches track contents, paths,
query, rubric, and backend settings. Each completed prompt batch is also cached:
an edit can reuse other batches when their evidence and shared context stay
unchanged. Even edits that leave outlines identical invalidate the affected
batch, because its key includes actual source. Changes to batch boundaries or
shared context can invalidate more batches. Full-source prompt batches depend
on all source because they use a general document. Repeating a query on unchanged
inputs requires no API requests. Watch mode retains both token budgets and keeps
the previous output if scoring fails.

Configuration precedence is process environment, target-project `.env`,
then `~/.env`, without changing the process environment. `TYPESAFE_URL`
defaults to `https://api.typesafe.ai/v1/systemone`, and `TYPESAFE_MODEL`
defaults to the pinned `jev-1.13.0`. Set `CATENATOR_JEV_INPUT_PRICE` to change
the estimated USD price per million input tokens.

### Costs and inspecting scores

`--token-limit` bounds the returned document, **not** the Jev work needed to
rank files. A 12,000-token output can otherwise require millions of API tokens
when thousands of files are eligible.

`--jev-token-limit` defaults to **500,000 estimated input tokens per run**.
Catenator counts uncached payloads before each pass and stops before dispatching
that pass if it cannot fit the remaining allowance. Full-source mode shares
the allowance between its general and prompt passes; a later pass can be refused
after earlier work has completed. Recovery attempts consume the same allowance,
including rejected payloads. The running total accounts for the greater of each
request's local estimate and reported usage. Estimates use `cl100k_base`, so
this is not a guaranteed provider billing ceiling; a response can report more
tokens than estimated. Cache hits spend no allowance.

For an oversized project, narrow the directory, select extensions with
`--include`, or exclude irrelevant assets in `.catignore`. Downloaded ESP-IDF
`managed_components/` trees are excluded by default, like other vendored
dependencies; a named build can explicitly select them. Bundled documentation
and marketing files remain eligible unless you exclude them. To intentionally
allow a larger Jev run, use `--jev-token-limit 1000000`; use `0` for unlimited.
The Python API accepts `jev_token_limit=N` (`0` or `None` for unlimited).

Each fresh pass first prints eligible files, uncached requests, and estimated
input tokens. Afterward it prints its input mode, request count, API-reported input
tokens, and estimated USD cost to stderr. The context document remains on
stdout. Cache hits report no new API cost. Confirmed context-limit failures
are subdivided into smaller requests; identical failed requests and unrelated
errors are not retried automatically. `context_splits` records these recovery
steps in each pass's report. Only successfully validated responses contribute
to the usage report; provider billing may include a request whose response
could not be read.

At the configured default estimate of **$0.042 per million input tokens**,
these are example costs across all batches of a pass. The estimate uses
API-reported input tokens and adds no output-token charge:

| API-reported input tokens | Estimated USD cost |
| ---: | ---: |
| 10,000 | $0.000420 |
| 30,000 | $0.001260 |
| 100,000 | $0.004200 |
| 1,000,000 | $0.042000 |

A cold outline query uses one pass. Full-source queries can use two passes;
for example, 30,000 reported tokens per pass would total $0.00252. Actual costs
include question text and shared context repeated across batches. These estimates cover Jev only; `--llm`
uses its separately priced summary backend. See [TypeSafe's model pricing](https://docs.typesafe.ai/models).

The Python API exposes both raw per-file answers (including confidence and
probabilities) and per-pass usage:

```python
from catenator import Catenator

cat = Catenator('/path/to/project')
context = cat.catenate(prompt='Explain authentication', token_limit=6000)
print(cat.last_jev_scores['prompt'])
print(cat.last_jev_report)

# Use full source for ranking instead of outlines with docstrings.
context = cat.catenate(
    prompt='Explain authentication', token_limit=6000, jev_full_source=True,
)
```

See the [Jev benchmark comparisons](docs/jev-benchmark-2026-09-18.md) for
measured quality, costs, cache behavior, and failure cases.

## Development

```sh
pip install -e '.[dev]'
python -m pytest
```
