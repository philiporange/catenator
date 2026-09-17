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
- Jev file importance scoring, prompt-specific ranking, and API cost reporting

## Installation

Requires Python 3.9 or later. Install with token counting for budgeted output:
   ```
   pip install 'catenator[token_counting]'
   ```

## Usage

### As a Command-Line Tool

Basic usage:
```
catenator /path/to/your/project
```

Options:
- `--output FILE`: Write output to a file instead of stdout
- `--clipboard`: Copy output to clipboard
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
- `--token-limit N`: Keep output under N tokens by summarizing least important files
- `--llm`: Use AI for richer summaries when using --token-limit (requires openai module)
- `--jev`: Use Jev to score whether each file should be verbatim, summarized, or ignored
- `--prompt TEXT`: Rerank Jev scores for an instruction or query (requires `--jev`)
- `--refresh-scores`: Recompute Jev scores instead of using the cache (requires `--jev`)

Example:
```
python catenator.py /path/to/your/project --output concatenated.md --include py,js,ts
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
catenator /path/to/project --token-limit 10000 --llm
```

By default, `--llm` uses `muse-code/muse-spark-1.3` through
`https://llm.ph1l.uk/v1`. Set `LLM_API_KEY` in the target project's `.env` or
in your environment; `LLM_BASE_URL` is optional. Existing
`CATENATOR_SUMMARIZER_MODEL`, `CATENATOR_SUMMARIZER_API_KEY`, and
`CATENATOR_SUMMARIZER_BASE_URL` overrides take precedence. Cached summaries are
keyed by endpoint and model, so old summaries are naturally regenerated for
the new backend and model.

Files are labeled `(summary)` or `(outline)` when reduced. The automatic CLI
extracts structure from its in-memory source snapshot; optional AI file
summaries are cached in `~/.catenator/summaries/`.

## Jev file scoring

Install token counting and set `TYPESAFE_API_KEY` in your environment,
`~/.env`, or the target project's `.env`:

```sh
pip install -e '.[jev]'
catenator /path/to/project --jev --token-limit 12000
catenator /path/to/project --jev --token-limit 6000 \
  --prompt "Fix the parser's handling of escaped quotes"
```

The general pass sends every eligible file's full contents to Jev and asks a
separate scoring question for each file. Files share request context, and
questions are batched to avoid repeating the project for every file. This
uses the same discovery snapshot and ignore rules as normal Catenator.
Selected source is sent to the configured TypeSafe endpoint.

Scores use three ordered levels: 0 means ignore, 1 means summarize, and 2
means verbatim. Fractional scores below 0.5 omit the file body; scores from
0.5 to below 1.5 cap it at a structural summary; scores of 1.5 or higher
allow full source. The overview and directory tree can still mention omitted
files. `--token-limit` can further reduce or omit files to fit the output.
Without a token limit, the score's inclusion decision still applies.

For larger projects, source is packed into roughly 28,000-token states with
a shared structural project overview. Individual files exceeding a state
are split without dropping source characters; their highest part score
determines inclusion. Every file is evaluated, but each judgment sees only
its source batch plus shared project context. Request packing also budgets
question text: up to 30,000 estimated tokens for state plus one question and
60,000 for state plus all questions. These counts use `cl100k_base` and leave
headroom for Jev's different tokenizer. API size errors are reported clearly.

With `--prompt`, Catenator first obtains general scores, then builds a general
Jev document with a high context budget (up to approximately 27,000 tokens,
with space reserved for the query and request formatting). Jev scores every
candidate again for the query. Each question includes the candidate's path
and a bounded structural description, allowing the prompt to promote files
omitted from the general document. Final output uses the original source and
the prompt scores, under your requested output budget. Jev does not generate
summaries; `--llm` optionally enables the existing text-summary backend for
final output.

Scores are cached outside the project in `~/.catenator/jev/`. General ratings
are stored per relative file path with a **16-character SHA-256 prefix** of
the contents that were rated. Existing files keep their general rating when
edited; only paths missing from the cache are automatically scored. Adding a
file asks Jev only about the new file, with project source as context. Removing
or excluding a file needs no API call; its rating remains available if the
path returns. Previously unseen renamed paths are new candidates.
Scoring instructions and backend settings identify separate general caches.

The short hash records which contents were rated; it does not force a new
general judgment after routine edits. Use `--refresh-scores` to reassess every
file, for example after a change of architectural role. Final output always
uses current source. Prompt caches track current contents, paths, and query,
so edits can refresh prompt scores while retaining general ratings. Repeating
a query on unchanged inputs requires no API requests; a different query also
reuses the general ratings. Watch mode retains the Jev options and keeps the
previous output if scoring fails.

Configuration precedence is process environment, target-project `.env`,
then `~/.env`, without changing the process environment. `TYPESAFE_URL`
defaults to `https://api.typesafe.ai/v1/systemone`, and `TYPESAFE_MODEL`
defaults to the pinned `jev-1.13.0`. Set `CATENATOR_JEV_INPUT_PRICE` to change
the estimated USD price per million input tokens.

### Costs and inspecting scores

Each fresh pass prints request count, actual API-reported input tokens, and
estimated USD cost to stderr. The context document remains on stdout. Cache
hits report no new API cost. Failed requests are not retried automatically.
Only successfully validated responses contribute to the usage report;
provider billing may include a request whose response could not be read.

At Jev 1.13's published **$0.042 per million input tokens**, with output
tokens free, these are example costs across all batches of a pass:

| Billed input tokens | Estimated USD cost |
| ---: | ---: |
| 10,000 | $0.000420 |
| 30,000 | $0.001260 |
| 100,000 | $0.004200 |
| 1,000,000 | $0.042000 |

A cold general-plus-query run uses both passes. For example, 30,000 billed
tokens per pass would total $0.00252; another query with cached general
scores would cost $0.00126. Actual costs include question text and shared
context repeated across batches. These estimates cover Jev only; `--llm`
uses its separately priced summary backend. See [TypeSafe's model pricing](https://docs.typesafe.ai/models).

The Python API exposes both raw per-file answers (including confidence and
probabilities) and per-pass usage:

```python
cat = Catenator('/path/to/project')
context = cat.catenate(use_jev=True, prompt='Explain authentication', token_limit=6000)
print(cat.last_jev_scores['general'])
print(cat.last_jev_scores['prompt'])
print(cat.last_jev_report)
```

## Development

```sh
pip install -e '.[dev]'
python -m pytest
```
