import os
import argparse
import fnmatch
import time
from threading import Timer

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
    ]
    README_FILES = ["README", "README.md", "README.txt"]
    TOKENIZER = "cl100k_base"
    CATIGNORE_FILENAME = ".catignore"

    def __init__(
        self,
        directory,
        include_extensions=None,
        ignore_extensions=None,
        include_tree=True,
        include_readme=True,
        title=None,
        ignore_tests=False,
    ):
        self.directory = directory
        self.include_extensions = (
            include_extensions or self.DEFAULT_CODE_EXTENSIONS
        )
        self.ignore_extensions = ignore_extensions or []
        self.include_tree = include_tree
        self.include_readme = include_readme
        self.title = title or os.path.basename(os.path.abspath(directory))
        self.ignore_patterns = self.load_cat_ignore()
        self.ignore_tests = ignore_tests

    def load_cat_ignore(self):
        ignore_file = os.path.join(self.directory, self.CATIGNORE_FILENAME)
        if os.path.exists(ignore_file):
            with open(ignore_file, "r") as f:
                return [
                    line.strip()
                    for line in f
                    if line.strip() and not line.startswith("#")
                ]
        return []

    def should_ignore(self, path):
        rel_path = os.path.relpath(path, self.directory)

        # Check if we should ignore test files/directories
        if self.ignore_tests:
            if rel_path.startswith("tests/") or os.path.basename(
                rel_path
            ).startswith("test_"):
                return True

        for pattern in self.ignore_patterns:
            if pattern.endswith("/"):
                if fnmatch.fnmatch(
                    rel_path + "/", pattern
                ) or rel_path.startswith(pattern):
                    return True
            elif fnmatch.fnmatch(rel_path, pattern):
                return True
        return False

    def generate_directory_tree(self):
        tree = []
        for root, dirs, files in os.walk(self.directory):
            dirs[:] = [
                d
                for d in dirs
                if not self.should_ignore(os.path.join(root, d))
            ]
            level = root.replace(self.directory, "").count(os.sep)
            indent = "│   " * (level - 1) + "├── " if level > 0 else ""
            if not self.should_ignore(root):
                tree.append(f"{indent}{os.path.basename(root)}/")
                for file in files:
                    if not self.should_ignore(os.path.join(root, file)):
                        tree.append(f"{indent}│   {file}")
        return "\n".join(tree)

    def catenate(self):
        result = []

        result.append(f"### {self.title}\n\n")

        if self.include_tree:
            result.append("# Project Directory Structure\n")
            result.append("```\n")
            result.append(self.generate_directory_tree())
            result.append("```\n\n")

        if self.include_readme:
            for readme_file in self.README_FILES:
                readme_path = os.path.join(self.directory, readme_file)
                if os.path.exists(readme_path) and not self.should_ignore(
                    readme_path
                ):
                    with open(readme_path, "r", encoding="utf-8") as f:
                        readme_content = f.read()
                    result.append(f"# {readme_file}\n\n{readme_content}\n\n")
                    break

        for root, _, files in os.walk(self.directory):
            if self.should_ignore(root):
                continue
            for file in files:
                file_path = os.path.join(root, file)
                if self.should_ignore(file_path):
                    continue
                if file in self.README_FILES and self.include_readme:
                    continue
                file_extension = os.path.splitext(file)[1][
                    1:
                ]  # Get extension without dot
                if (
                    file_extension in self.include_extensions
                    and file_extension not in self.ignore_extensions
                ):
                    relative_path = os.path.relpath(file_path, self.directory)

                    result.append(f"# {relative_path}\n")

                    with open(file_path, "r", encoding="utf-8") as f:
                        result.append(f.read())

                    result.append("\n\n")  # Add some space between files

        return "".join(result)

    def count_tokens(self, s):
        try:
            import tiktoken
        except ImportError:
            raise ImportError(
                "Please install the `tiktoken` package to count tokens"
            )

        encoding = tiktoken.get_encoding(self.TOKENIZER)
        tokens = encoding.encode(s)
        return len(tokens)

    @classmethod
    def from_cli_args(cls, args):
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
        )


class CatenatorEventHandler(FileSystemEventHandler):
    def __init__(self, catenator, output_file, cooldown=15):
        self.catenator = catenator
        self.output_file = os.path.abspath(output_file)
        self.cooldown = cooldown
        self.last_update = 0
        self.update_timer = None

    def on_created(self, event):
        if not event.is_directory:
            self.handle_write_event(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self.handle_write_event(event.src_path)

    def handle_write_event(self, file_path):
        if os.path.abspath(file_path) == self.output_file:
            return  # Ignore changes to the output file
        if self.catenator.should_ignore(file_path):
            return
        print(f"Change detected: {file_path}")
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
        catenated_content = self.catenator.catenate()
        with open(self.output_file, "w", encoding="utf-8") as f:
            f.write(catenated_content)
        print(f"Updated catenated content written to {self.output_file}")
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

    args = parser.parse_args()

    catenator = Catenator.from_cli_args(args)
    catenated_content = catenator.catenate()

    if args.output:
        output_path = os.path.abspath(args.output)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(catenated_content)
        print(f"Catenated content written to {output_path}")

        if args.watch:
            print(f"Watching for changes in {args.directory}...")
            event_handler = CatenatorEventHandler(
                catenator, output_path, cooldown=15
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
        print("Catenated content copied to clipboard")
    else:
        print(catenated_content)

    if args.count_tokens:
        token_count = catenator.count_tokens(catenated_content)
        print(f"Token count: {token_count}")


if __name__ == "__main__":
    main()
