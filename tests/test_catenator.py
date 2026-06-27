import unittest
import os
import tempfile

from src.catenator import Catenator


# Check if the `tiktoken` package is installed
skip_tiktoken = False
try:
    import tiktoken
except ImportError:
    skip_tiktoken = True


class TestCatenator(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

        # Create a simple directory structure for testing
        os.mkdir(os.path.join(self.temp_dir, "subdir"))
        os.mkdir(os.path.join(self.temp_dir, "ignored_dir"))

        with open(os.path.join(self.temp_dir, "file1.py"), "w") as f:
            f.write("print('Hello from file1')")

        with open(os.path.join(self.temp_dir, "file2.js"), "w") as f:
            f.write("console.log('Hello from file2');")

        with open(os.path.join(self.temp_dir, "subdir", "file3.py"), "w") as f:
            f.write("print('Hello from file3')")

        with open(os.path.join(self.temp_dir, "ignored_file.txt"), "w") as f:
            f.write("This file should be ignored")

        with open(
            os.path.join(self.temp_dir, "ignored_dir", "file4.py"), "w"
        ) as f:
            f.write("print('This file should be ignored')")

        with open(os.path.join(self.temp_dir, "README.md"), "w") as f:
            f.write("# Test Project\nThis is a test project.")

    def tearDown(self):
        for root, dirs, files in os.walk(self.temp_dir, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(self.temp_dir)

    def test_catenate_default(self):
        catenator = Catenator(self.temp_dir)
        result = catenator.catenate()

        self.assertIn("### " + os.path.basename(self.temp_dir), result)
        self.assertIn("# Project Directory Structure", result)
        self.assertIn("# README.md", result)
        self.assertIn("# Test Project", result)
        self.assertIn("# file1.py", result)
        self.assertIn("print('Hello from file1')", result)
        self.assertIn("# file2.js", result)
        self.assertIn("console.log('Hello from file2');", result)
        self.assertIn("# subdir/file3.py", result)
        self.assertIn("print('Hello from file3')", result)

    def test_catenate_no_tree(self):
        catenator = Catenator(self.temp_dir, include_tree=False)
        result = catenator.catenate()

        self.assertNotIn("# Project Directory Structure", result)
        self.assertIn("# file1.py", result)

    def test_catenate_custom_extensions(self):
        catenator = Catenator(self.temp_dir, include_extensions=["py"])
        result = catenator.catenate()

        self.assertIn("# file1.py", result)
        self.assertIn("print('Hello from file1')", result)
        self.assertNotIn("# file2.js", result)
        self.assertNotIn("console.log('Hello from file2');", result)

    def test_catenate_ignore_extensions(self):
        catenator = Catenator(self.temp_dir, ignore_extensions=["js"])
        result = catenator.catenate()

        self.assertIn("# file1.py", result)
        self.assertIn("print('Hello from file1')", result)
        self.assertNotIn("# file2.js", result)
        self.assertNotIn("console.log('Hello from file2');", result)

    def test_catenate_custom_title(self):
        custom_title = "My Custom Project"
        catenator = Catenator(self.temp_dir, title=custom_title)
        result = catenator.catenate()

        self.assertIn(f"### {custom_title}", result)

    def test_generate_directory_tree(self):
        catenator = Catenator(self.temp_dir)
        tree = catenator.generate_directory_tree()

        self.assertIn(os.path.basename(self.temp_dir), tree)
        self.assertIn("subdir", tree)
        self.assertIn("file1.py", tree)
        self.assertIn("file2.js", tree)
        self.assertIn("file3.py", tree)
        self.assertIn("README.md", tree)

    @unittest.skipIf(skip_tiktoken, "tiktoken not installed")
    def test_count_tokens(self):
        catenator = Catenator(self.temp_dir)
        test_string = "Hello, world!"
        token_count = catenator.count_tokens(test_string)
        self.assertIsInstance(token_count, int)
        self.assertGreater(token_count, 0)

    @unittest.skipIf(skip_tiktoken, "tiktoken not installed")
    def test_count_tokens_with_special_tokens(self):
        # tiktoken raises on special tokens by default; they must be
        # treated as ordinary text instead
        catenator = Catenator(self.temp_dir)
        for marker in ("<|endoftext|>", "<|fim_prefix|>", "<|endofprompt|>"):
            token_count = catenator.count_tokens(f"before {marker} after")
            self.assertGreater(token_count, 0)

    def test_cat_ignore_file(self):
        # Create a .cat_ignore file
        with open(os.path.join(self.temp_dir, ".catignore"), "w") as f:
            f.write("ignored_file.txt\n")
            f.write("ignored_dir/\n")
            f.write("*.js\n")

        catenator = Catenator(self.temp_dir)
        result = catenator.catenate()

        self.assertIn("# file1.py", result)
        self.assertIn("print('Hello from file1')", result)
        self.assertIn("# subdir/file3.py", result)
        self.assertIn("print('Hello from file3')", result)

        self.assertNotIn("ignored_file.txt", result)
        self.assertNotIn("This file should be ignored", result)
        self.assertNotIn("ignored_dir", result)
        self.assertNotIn("file4.py", result)
        self.assertNotIn("# file2.js", result)
        self.assertNotIn("console.log('Hello from file2');", result)

    def test_cat_ignore_in_directory_tree(self):
        # Create a .cat_ignore file
        with open(os.path.join(self.temp_dir, ".catignore"), "w") as f:
            f.write("ignored_file.txt\n")
            f.write("ignored_dir/\n")
            f.write("*.js\n")

        catenator = Catenator(self.temp_dir)
        tree = catenator.generate_directory_tree()

        self.assertIn("file1.py", tree)
        self.assertIn("subdir", tree)
        self.assertIn("file3.py", tree)

        self.assertNotIn("ignored_file.txt", tree)
        self.assertNotIn("ignored_dir", tree)
        self.assertNotIn("file4.py", tree)
        self.assertNotIn("file2.js", tree)

    def test_cat_ignore_with_comments(self):
        # Create a .cat_ignore file with comments
        with open(os.path.join(self.temp_dir, ".catignore"), "w") as f:
            f.write("# This is a comment\n")
            f.write("ignored_file.txt\n")
            f.write("# Another comment\n")
            f.write("ignored_dir/\n")

        catenator = Catenator(self.temp_dir)
        result = catenator.catenate()

        self.assertIn("# file1.py", result)
        self.assertIn("print('Hello from file1')", result)
        self.assertIn("# file2.js", result)
        self.assertIn("console.log('Hello from file2');", result)

        self.assertNotIn("ignored_file.txt", result)
        self.assertNotIn("This file should be ignored", result)
        self.assertNotIn("ignored_dir", result)
        self.assertNotIn("file4.py", result)

    def test_cat_ignore_empty_file(self):
        # Create an empty .cat_ignore file
        open(os.path.join(self.temp_dir, ".catignore"), "w").close()

        catenator = Catenator(self.temp_dir)
        result = catenator.catenate()

        self.assertIn("# file1.py", result)
        self.assertIn("print('Hello from file1')", result)
        self.assertIn("# file2.js", result)
        self.assertIn("console.log('Hello from file2');", result)
        self.assertIn("ignored_file.txt", result)
        self.assertIn("This file should be ignored", result)
        self.assertIn("ignored_dir", result)
        self.assertIn("file4.py", result)

    def test_catenate_with_build_config(self):
        # The config loading logic is in main(), so we simulate it here
        # by creating the build_config dictionary manually.
        build_config = {
            "whitelist": ["file1.py", "subdir/"],
            "blacklist": ["file2.js"],
        }

        # Create a file in subdir to test directory whitelisting
        with open(os.path.join(self.temp_dir, "subdir", "file4.py"), "w") as f:
            f.write("print('Hello from file4')")

        catenator = Catenator(self.temp_dir, build_config=build_config)
        result = catenator.catenate()

        self.assertIn("# file1.py", result)
        self.assertIn("print('Hello from file1')", result)

        self.assertIn("# subdir/file3.py", result)
        self.assertIn("print('Hello from file3')", result)

        self.assertIn("# subdir/file4.py", result)
        self.assertIn("print('Hello from file4')", result)

        # file2.js is in the blacklist
        self.assertNotIn("# file2.js", result)

        # ignored_file.txt is not in the whitelist
        self.assertNotIn("ignored_file.txt", result)

    def test_always_ignore_nested_vendored_dirs(self):
        nested = os.path.join(self.temp_dir, "frontend", "node_modules", "pkg")
        os.makedirs(nested)
        with open(os.path.join(nested, "index.js"), "w") as f:
            f.write("console.log('vendored');")
        venv_dir = os.path.join(self.temp_dir, "backend", "venv", "lib")
        os.makedirs(venv_dir)
        with open(os.path.join(venv_dir, "site.py"), "w") as f:
            f.write("print('venv internals')")

        catenator = Catenator(self.temp_dir)
        result = catenator.catenate()

        self.assertNotIn("node_modules", result)
        self.assertNotIn("vendored", result)
        self.assertNotIn("venv", result)
        self.assertNotIn("venv internals", result)

    def test_always_ignore_applies_in_build_mode(self):
        nested = os.path.join(self.temp_dir, "subdir", "node_modules")
        os.makedirs(nested)
        with open(os.path.join(nested, "dep.js"), "w") as f:
            f.write("console.log('dep');")

        build_config = {"whitelist": ["subdir/"]}
        catenator = Catenator(self.temp_dir, build_config=build_config)
        result = catenator.catenate()

        self.assertIn("# subdir/file3.py", result)
        self.assertNotIn("node_modules", result)
        self.assertNotIn("console.log('dep');", result)

    def test_cat_ignore_dir_pattern_matches_nested(self):
        with open(os.path.join(self.temp_dir, ".catignore"), "w") as f:
            f.write("ignored_dir/\n")
        nested = os.path.join(self.temp_dir, "subdir", "ignored_dir")
        os.makedirs(nested)
        with open(os.path.join(nested, "file5.py"), "w") as f:
            f.write("print('nested ignored')")

        catenator = Catenator(self.temp_dir)
        result = catenator.catenate()

        self.assertIn("# subdir/file3.py", result)
        self.assertNotIn("ignored_dir", result)
        self.assertNotIn("nested ignored", result)

    def test_cat_ignore_filename_pattern_matches_nested(self):
        with open(os.path.join(self.temp_dir, ".catignore"), "w") as f:
            f.write("package-lock.json\n")
        with open(
            os.path.join(self.temp_dir, "subdir", "package-lock.json"), "w"
        ) as f:
            f.write("{}")

        catenator = Catenator(self.temp_dir)
        result = catenator.catenate()

        self.assertNotIn("package-lock.json", result)

    def test_local_cat_ignore_extends_defaults(self):
        # cat.md is in default.catignore; a local .catignore must add to
        # the defaults, not replace them
        with open(os.path.join(self.temp_dir, ".catignore"), "w") as f:
            f.write("ignored_file.txt\n")
        with open(os.path.join(self.temp_dir, "cat.md"), "w") as f:
            f.write("previous catenator output")

        catenator = Catenator(self.temp_dir)
        result = catenator.catenate()

        self.assertNotIn("cat.md", result)
        self.assertNotIn("ignored_file.txt", result)
        self.assertIn("# file1.py", result)

    def test_minified_files_skipped(self):
        long_line = "var x=1;" * 1000
        with open(os.path.join(self.temp_dir, "bundle.js"), "w") as f:
            f.write(long_line)
        with open(os.path.join(self.temp_dir, "notes.md"), "w") as f:
            f.write(long_line)

        catenator = Catenator(self.temp_dir)
        result = catenator.catenate()
        self.assertNotIn("# bundle.js", result)
        self.assertIn("# notes.md", result)

        catenator = Catenator(self.temp_dir, include_minified=True)
        result = catenator.catenate()
        self.assertIn("# bundle.js", result)

    def test_minified_files_skipped_in_collect_files(self):
        with open(os.path.join(self.temp_dir, "bundle.js"), "w") as f:
            f.write("var x=1;" * 1000)

        catenator = Catenator(self.temp_dir)
        rel_paths = [rel for rel, _, _ in catenator.collect_files()]
        self.assertNotIn("bundle.js", rel_paths)
        self.assertIn("file1.py", rel_paths)


if __name__ == "__main__":
    unittest.main()
