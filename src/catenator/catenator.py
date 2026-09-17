"""
Catenator concatenates source code files from a project into a single output.

Each output starts with a deterministic project overview derived from source
and manifests. Files are discovered once, in stable order, and ignored trees
are pruned before descent. Budgeted output covers the main directories with
outlines before expanding important files into structural summaries or full
source. Whole sections, including the overview and tree, share a strict token
budget. The optional --llm flag enables AI file summaries. --jev evaluates
all selected source in batches to score inclusion, reuses general ratings for
known file paths, and optionally reranks them for --prompt using general project
context. API usage and estimated cost are reported separately from output.

Ignore handling: vendored/generated directories (node_modules, __pycache__,
venv, .git, etc.) are always excluded, in every mode, at any depth. The
bundled default.catignore applies in normal mode; a project's .catignore adds
patterns on top of it. Builds supply their own whitelist and blacklist.
.catignore patterns use
gitignore-style semantics: patterns without a slash match path components at
any depth, and directory patterns (trailing slash) match the directory and
everything inside it. Files containing any line longer than MAX_LINE_LENGTH
are treated as minified/generated and skipped (markdown exempt) unless
--include-minified is given.
"""

import os
import argparse
import fnmatch
import re
import sys
import time
from threading import Timer
import yaml

import pyperclip
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class Catenator:
    DEFAULT_CODE_EXTENSIONS = [
        "py",
        "js",
        "java",
        "c",
        "cpp",
        "h",
        "cs",
        "rb",
        "go",
        "php",
        "ts",
        "swift",
        "html",
        "css",
        "sql",
        "sh",
        "bash",
        "ps1",
        "R",
        "scala",
        "kt",
        "rs",
        "dart",
        "md",
        "rst",
        "jsx",
        "tsx",
        "mjs",
        "cjs",
        "mts",
        "cts",
        "vue",
        "svelte",
        "astro",
        "json",
        "yaml",
        "yml",
        "toml",
        "ini",
        "cfg",
    ]
    README_FILES = ["README", "README.md", "README.txt", "README.rst"]
    PROJECT_FILES = {
        "makefile",
        "gnumakefile",
        "dockerfile",
        "containerfile",
        "cmakelists.txt",
        "go.mod",
        "gemfile",
        "rakefile",
        "procfile",
        "justfile",
        "pipfile",
        "default.catignore",
    }
    TOKENIZER = "cl100k_base"
    CATIGNORE_FILENAME = ".catignore"
    CATCONFIG_FILENAME = ".catconfig.yaml"
    # Excluded in every mode (including --build and --include-hidden), at any
    # depth, regardless of .catignore contents
    ALWAYS_IGNORE_DIRS = {
        "__pycache__",
        "node_modules",
        ".git",
        "venv",
        ".venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
    # Any line longer than this marks a file as minified/generated
    MAX_LINE_LENGTH = 2000
    # Prose formats legitimately contain unwrapped long lines
    MINIFIED_CHECK_EXEMPT_EXTENSIONS = {"md", "txt", "rst"}

    def __init__(
        self,
        directory,
        include_extensions=None,
        ignore_extensions=None,
        include_tree=True,
        include_readme=True,
        title=None,
        ignore_tests=False,
        include_hidden=False,
        build_config=None,
        include_minified=False,
        include_overview=True,
        exclude_paths=None,
    ):
        self.directory = directory
        self.include_extensions = (
            include_extensions or self.DEFAULT_CODE_EXTENSIONS
        )
        self.custom_extensions = include_extensions is not None
        self.ignore_extensions = ignore_extensions or []
        self.include_tree = include_tree
        self.include_readme = include_readme
        self.title = title or os.path.basename(os.path.abspath(directory))
        self.ignore_patterns = self.load_cat_ignore()
        self.ignore_tests = ignore_tests
        self.include_hidden = include_hidden
        self.build_config = build_config or {}
        self.include_minified = include_minified
        self.include_overview = include_overview
        self.exclude_paths = {
            os.path.abspath(path) for path in (exclude_paths or [])
        }
        self.last_report = {}
        self.last_jev_report = []
        self.last_jev_scores = {}

    def load_cat_ignore(self):
        """
        Load ignore patterns from the bundled default.catignore, then add
        patterns from the project's .catignore. The defaults always apply;
        a local .catignore extends them rather than replacing them.
        """
        default_ignore_path = os.path.join(
            os.path.dirname(__file__), "default.catignore"
        )
        local_ignore_path = os.path.join(
            self.directory, self.CATIGNORE_FILENAME
        )

        patterns = []
        for path in (default_ignore_path, local_ignore_path):
            if not os.path.isfile(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                patterns.extend(
                    line.strip()
                    for line in f
                    if line.strip() and not line.startswith("#")
                )
        return patterns

    @staticmethod
    def matches_pattern(rel_path, pattern):
        """
        Gitignore-style pattern matching.

        Directory patterns (trailing slash) match that directory and
        everything inside it, at any depth unless the pattern contains an
        inner slash (then it is anchored to the project root). Patterns
        without a slash match any path component at any depth. Patterns
        containing a slash match against the full relative path.
        """
        parts = rel_path.split(os.sep)
        if pattern.endswith("/"):
            dir_pattern = pattern.rstrip("/")
            if "/" in dir_pattern:
                return rel_path.startswith(pattern) or fnmatch.fnmatch(
                    rel_path + "/", pattern
                )
            return any(fnmatch.fnmatch(part, dir_pattern) for part in parts)
        if "/" in pattern:
            return fnmatch.fnmatch(rel_path, pattern)
        return any(fnmatch.fnmatch(part, pattern) for part in parts)

    def is_minified(self, filename, content):
        """
        Detect minified or generated content by line length. Real source
        code essentially never has lines longer than MAX_LINE_LENGTH;
        minified bundles, embedded data blobs, and scraped pages do.
        Prose formats are exempt since they may be unwrapped.
        """
        if self.include_minified:
            return False
        extension = os.path.splitext(filename)[1][1:].lower()
        if extension in self.MINIFIED_CHECK_EXEMPT_EXTENSIONS:
            return False
        return any(
            len(line) > self.MAX_LINE_LENGTH for line in content.splitlines()
        )

    @staticmethod
    def may_contain_match(directory, pattern):
        """Keep only ancestors compatible with a whitelist's literal prefix."""
        prefix = re.split(r"[*?\[]", pattern, maxsplit=1)[0]
        directory = directory.replace(os.sep, "/") + "/"
        return prefix.startswith(directory) or (
            len(prefix) < len(pattern) and directory.startswith(prefix)
        )

    def should_ignore(self, path):
        rel_path = os.path.relpath(path, self.directory)

        # Never ignore the top-level directory itself
        if rel_path == ".":
            return False

        if os.path.abspath(path) in self.exclude_paths:
            return True

        # Vendored/generated directories are never included, in any mode
        parts = rel_path.split(os.sep)
        if any(part in self.ALWAYS_IGNORE_DIRS for part in parts):
            return True

        if self.build_config:
            whitelist = self.build_config.get("whitelist", [])
            blacklist = self.build_config.get("blacklist", [])

            # Check blacklist first
            for pattern in blacklist:
                if pattern.endswith("/"):
                    if fnmatch.fnmatch(
                        rel_path + "/", pattern
                    ) or rel_path.startswith(pattern):
                        return True
                elif fnmatch.fnmatch(rel_path, pattern):
                    return True

            # If whitelist is defined, only include paths that match
            if whitelist:
                is_whitelisted = False
                for pattern in whitelist:
                    if pattern.endswith("/"):
                        if fnmatch.fnmatch(
                            rel_path + "/", pattern
                        ) or rel_path.startswith(pattern):
                            is_whitelisted = True
                            break
                    elif fnmatch.fnmatch(rel_path, pattern):
                        is_whitelisted = True
                        break
                if not is_whitelisted:
                    # Whitelisted descendants must remain reachable.
                    if not os.path.isdir(path):
                        return True
                    if not any(
                        self.may_contain_match(rel_path, pattern)
                        for pattern in whitelist
                    ):
                        return True

            return False

        # Ignore hidden files/directories
        if not self.include_hidden:
            ci_path = rel_path.replace(os.sep, "/")
            known_ci = (
                ci_path in (".github", ".github/workflows", ".gitlab-ci.yml")
                or ci_path.startswith(".github/workflows/")
            ) and not any(part.startswith(".") for part in parts[1:])
            if any(part.startswith(".") for part in parts) and not known_ci:
                return True

        # Check if we should ignore test files/directories
        if self.ignore_tests:
            from .summarizer import is_test_file

            if is_test_file(rel_path):
                return True

        # Apply patterns from .catignore
        for pattern in self.ignore_patterns:
            if self.matches_pattern(rel_path, pattern):
                return True

        return False

    @classmethod
    def is_readme(cls, filename):
        return filename.lower() in {name.lower() for name in cls.README_FILES}

    def walk_project(self):
        """Walk eligible directories once, pruning excluded subtrees."""
        for root, dirs, files in os.walk(self.directory):
            dirs[:] = sorted(
                d
                for d in dirs
                if not self.should_ignore(os.path.join(root, d))
            )
            yield root, dirs, sorted(files)

    def select_file(self, filename):
        """Select source and project metadata, respecting explicit filters."""
        if self.is_readme(filename):
            return self.include_readme
        extension = os.path.splitext(filename)[1][1:].lower()
        if extension in {ext.lower() for ext in self.ignore_extensions}:
            return False
        if self.build_config:
            return True
        if extension in {ext.lower() for ext in self.include_extensions}:
            return True
        return not self.custom_extensions and (
            filename.lower() in self.PROJECT_FILES
            or fnmatch.fnmatch(filename.lower(), "requirements*.txt")
            or fnmatch.fnmatch(filename.lower(), "requirements*.in")
            or filename.lower().startswith("dockerfile.")
        )

    def generate_directory_tree(self, paths=None):
        """Render a stable tree; reuse a discovery snapshot when supplied."""
        if paths is None:
            paths = []
            for root, dirs, files in self.walk_project():
                for name in dirs + files:
                    path = os.path.join(root, name)
                    if self.should_ignore(path):
                        continue
                    if self.is_readme(name) and not self.include_readme:
                        continue
                    relative = os.path.relpath(path, self.directory)
                    paths.append(relative + ("/" if name in dirs else ""))
        tree = [os.path.basename(os.path.abspath(self.directory)) + "/"]
        for relative in sorted(paths):
            parts = relative.rstrip("/").replace(os.sep, "/").split("/")
            suffix = "/" if relative.endswith("/") else ""
            tree.append("    " * len(parts) + parts[-1] + suffix)
        return "\n".join(tree)

    def collect_files(self):
        """Read selected files once and retain tree and exclusion metadata."""
        files = []
        self._tree_paths = []
        self.last_report = {"unreadable": 0, "minified": 0}
        for root, dirs, filenames in self.walk_project():
            self._tree_paths.extend(
                os.path.relpath(os.path.join(root, d), self.directory) + "/"
                for d in dirs
            )
            for filename in filenames:
                file_path = os.path.join(root, filename)
                if self.should_ignore(file_path):
                    continue
                if self.is_readme(filename) and not self.include_readme:
                    continue
                relative_path = os.path.relpath(file_path, self.directory)
                self._tree_paths.append(relative_path)
                if not self.select_file(filename):
                    continue
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                except (OSError, UnicodeDecodeError):
                    self.last_report["unreadable"] += 1
                    continue
                if self.is_minified(filename, content):
                    self.last_report["minified"] += 1
                    continue
                files.append((relative_path, file_path, content))
        return files

    def catenate(
        self,
        file_overrides=None,
        token_limit=None,
        use_llm=False,
        use_jev=False,
        prompt=None,
        refresh_scores=False,
    ):
        """Render source and a factual overview within an optional budget.

        Overrides map paths to replacement text or (text, label); None skips
        a file. Each call takes a fresh filesystem snapshot, including when
        used by the watcher. Budgeting never rereads source or calls AI unless
        use_llm or use_jev is explicitly enabled. Jev scores use this same
        snapshot; prompt scoring follows a cached general scoring pass.
        """
        from .rendering import render_project

        if token_limit is not None and token_limit <= 0:
            raise ValueError("token_limit must be positive")
        if prompt is not None and (not use_jev or not prompt.strip()):
            raise ValueError("prompt requires Jev mode and non-empty text")
        if refresh_scores and not use_jev:
            raise ValueError("refresh_scores requires Jev mode")
        self.last_jev_report = []
        self.last_jev_scores = {}
        files = self.collect_files()
        file_scores = None
        if use_jev:
            from .jev import score_project

            scoring_files = []
            for path, absolute, content in files:
                replacement = (file_overrides or {}).get(path, content)
                content = (
                    replacement[0]
                    if isinstance(replacement, tuple)
                    else replacement
                )
                if content is not None:
                    scoring_files.append((path, absolute, content))
            file_scores = score_project(
                self, scoring_files, prompt=prompt, refresh=refresh_scores
            )
        return render_project(
            self,
            files,
            file_overrides,
            token_limit,
            use_llm,
            file_scores=file_scores,
        )

    def count_tokens(self, s):
        try:
            import tiktoken
        except ImportError:
            raise ImportError(
                "Please install the `tiktoken` package to count tokens"
            )

        encoding = tiktoken.get_encoding(self.TOKENIZER)
        # Treat special tokens (e.g. <|endoftext|>) as ordinary text rather
        # than letting tiktoken raise on them.
        tokens = encoding.encode(s, disallowed_special=())
        return len(tokens)

    @classmethod
    def from_cli_args(cls, args, build_config=None):
        return cls(
            directory=args.directory,
            include_extensions=(
                [ext.strip() for ext in args.include.split(",") if ext.strip()]
                if args.include
                else None
            ),
            ignore_extensions=[
                ext.strip() for ext in args.ignore.split(",") if ext.strip()
            ],
            include_tree=not args.no_tree,
            include_readme=not args.no_readme,
            title=args.title,
            ignore_tests=args.ignore_tests,
            include_hidden=args.include_hidden,
            build_config=build_config,
            include_minified=args.include_minified,
            include_overview=not args.no_overview,
            exclude_paths=[args.output] if args.output else [],
        )


class CatenatorEventHandler(FileSystemEventHandler):
    def __init__(
        self,
        catenator,
        output_file,
        cooldown=15,
        token_limit=None,
        use_llm=False,
        use_jev=False,
        prompt=None,
    ):
        self.catenator = catenator
        self.output_file = os.path.abspath(output_file)
        self.catenator.exclude_paths.add(self.output_file)
        self.token_limit = token_limit
        self.use_llm = use_llm
        self.use_jev = use_jev
        self.prompt = prompt
        self.cooldown = cooldown
        self.last_update = 0
        self.update_timer = None

    def on_created(self, event):
        if not event.is_directory:
            self.handle_write_event(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self.handle_write_event(event.src_path)

    def on_deleted(self, event):
        self.handle_write_event(event.src_path)

    def on_moved(self, event):
        self.handle_write_event(event.src_path)
        self.handle_write_event(event.dest_path)

    def handle_write_event(self, file_path):
        if os.path.abspath(file_path) == self.output_file:
            return  # Ignore changes to the output file
        if self.catenator.should_ignore(file_path):
            return
        print(f"Change detected: {file_path}", file=sys.stderr)
        self.schedule_update()

    def schedule_update(self):
        if self.update_timer:
            self.update_timer.cancel()

        current_time = time.time()
        time_since_last_update = current_time - self.last_update

        if time_since_last_update < self.cooldown:
            delay = self.cooldown - time_since_last_update
        else:
            delay = 0

        self.update_timer = Timer(delay, self.update_output)
        self.update_timer.start()

    def update_output(self):
        from .jev_client import JevError

        try:
            catenated_content = self.catenator.catenate(
                token_limit=self.token_limit,
                use_llm=self.use_llm,
                use_jev=self.use_jev,
                prompt=self.prompt,
            )
        except (JevError, ValueError) as error:
            print(f"Catenator update failed: {error}", file=sys.stderr)
            return
        with open(self.output_file, "w", encoding="utf-8") as f:
            f.write(catenated_content)
        print(
            f"Updated catenated content written to {self.output_file}",
            file=sys.stderr,
        )
        self.last_update = time.time()


def main():
    parser = argparse.ArgumentParser(
        description="Catenate code files in a directory."
    )
    parser.add_argument("directory", help="Directory to process")
    parser.add_argument("--output", help="Output file path")
    parser.add_argument(
        "--clipboard", action="store_true", help="Copy output to clipboard"
    )
    parser.add_argument(
        "--no-tree", action="store_true", help="Disable directory tree"
    )
    parser.add_argument(
        "--no-readme", action="store_true", help="Disable README inclusion"
    )
    parser.add_argument(
        "--no-overview",
        action="store_true",
        help="Disable the automatic project overview",
    )
    parser.add_argument(
        "--include",
        type=str,
        default="",
        help="Comma-separated list of extensions to include",
    )
    parser.add_argument(
        "--ignore",
        type=str,
        default="",
        help="Comma-separated list of extensions to ignore",
    )
    parser.add_argument(
        "--title", type=str, help="Title for the catenated output"
    )
    parser.add_argument(
        "--count-tokens",
        action="store_true",
        help="Count tokens in the catenated output",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch for changes and update output file",
    )
    parser.add_argument(
        "--ignore-tests",
        action="store_true",
        help="Ignore 'tests/' directory and files starting with 'test_'",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden files (starting with '.') in the output",
    )
    parser.add_argument(
        "--include-minified",
        action="store_true",
        help="Include minified/generated files (very long lines) in output",
    )
    parser.add_argument(
        "--build",
        type=str,
        help="Name of the build to use from .catconfig.yaml",
    )
    parser.add_argument(
        "--token-limit",
        type=int,
        help="Max tokens; summarize least important files to fit",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Use AI to generate rich summaries (requires openai module)",
    )
    parser.add_argument(
        "--jev",
        action="store_true",
        help="Score file inclusion with Jev (requires TYPESAFE_API_KEY)",
    )
    parser.add_argument(
        "--prompt",
        help="Rerank Jev scores for an instruction or query",
    )
    parser.add_argument(
        "--refresh-scores",
        action="store_true",
        help="Recompute cached Jev scores",
    )

    args = parser.parse_args()
    if args.token_limit is not None and args.token_limit <= 0:
        parser.error("--token-limit must be positive")
    if args.watch and not args.output:
        parser.error("--watch requires --output")
    if args.prompt is not None and (not args.jev or not args.prompt.strip()):
        parser.error("--prompt requires --jev and non-empty text")
    if args.refresh_scores and not args.jev:
        parser.error("--refresh-scores requires --jev")

    build_config = {}
    if args.build:
        config_path = os.path.join(
            args.directory, Catenator.CATCONFIG_FILENAME
        )
        if os.path.isfile(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                try:
                    config = yaml.safe_load(f)
                    if (
                        config
                        and "builds" in config
                        and args.build in config["builds"]
                    ):
                        build_config = config["builds"][args.build]
                        print(
                            f"Using build '{args.build}' from {config_path}",
                            file=sys.stderr,
                        )
                    else:
                        print(
                            f"Warning: Build '{args.build}' "
                            f"not found in {config_path}",
                            file=sys.stderr,
                        )
                except yaml.YAMLError as e:
                    print(f"Error parsing {config_path}: {e}", file=sys.stderr)
        else:
            print(
                f"Warning: Config file {config_path} not found.",
                file=sys.stderr,
            )

    catenator = Catenator.from_cli_args(args, build_config=build_config)

    from .jev_client import JevError

    try:
        catenated_content = catenator.catenate(
            token_limit=args.token_limit,
            use_llm=args.llm,
            use_jev=args.jev,
            prompt=args.prompt,
            refresh_scores=args.refresh_scores,
        )
    except (JevError, ValueError) as error:
        parser.exit(1, f"catenator: {error}\n")

    if args.output:
        output_path = os.path.abspath(args.output)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(catenated_content)
        print(f"Catenated content written to {output_path}", file=sys.stderr)

        if args.watch:
            print(
                f"Watching for changes in {args.directory}...", file=sys.stderr
            )
            event_handler = CatenatorEventHandler(
                catenator,
                output_path,
                cooldown=15,
                token_limit=args.token_limit,
                use_llm=args.llm,
                use_jev=args.jev,
                prompt=args.prompt,
            )
            observer = Observer()
            observer.schedule(event_handler, args.directory, recursive=True)
            observer.start()
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                observer.stop()
            observer.join()
    elif args.clipboard:
        pyperclip.copy(catenated_content)
        print("Catenated content copied to clipboard", file=sys.stderr)
    else:
        print(catenated_content, end="")

    if args.count_tokens:
        token_count = catenator.count_tokens(catenated_content)
        print(f"Token count: {token_count}", file=sys.stderr)


if __name__ == "__main__":
    main()
