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

    def test_catenate_no_readme(self):
        catenator = Catenator(self.temp_dir, include_readme=False)
        result = catenator.catenate()

        self.assertNotIn("# README.md", result)
        self.assertNotIn("# Test Project", result)
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


if __name__ == "__main__":
    unittest.main()
