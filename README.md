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

Files are labeled `(summary)` or `(outline)` when reduced. The automatic CLI
extracts structure from its in-memory source snapshot; optional AI file
summaries are cached in `~/.catenator/summaries/`.

## Development

```sh
pip install -e '.[dev]'
python -m pytest
```
