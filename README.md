# Catenator

Catenator is a Python tool for concatenating code files in a directory into a single output string.

## Features

- Concatenate code files from a specified directory
- Include or exclude specific file extensions
- Include a directory tree structure
- Include README files in the output
- Output to file, clipboard, or stdout
- gitignore-style .catignore files

## Installation

Install using pip
   ```
   pip install catenator
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
- `--include EXTENSIONS`: Comma-separated list of file extensions to include (replaces defaults)
- `--ignore EXTENSIONS`: Comma-separated list of file extensions to ignore
- `--count-tokens`: Output approximation of how many tokens in output (tiktoken cl100k_base)
- `--watch`: Watch for changes and update output file automatically (requires --output)
- `--ignore-tests`: Leave out tests from the concatenated output


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
result = catenator.catenate()
print(result)
```

## .catignore File

The .catignore file allows you to specify files and directories that should be excluded from the concatenation process. The syntax is like .gitignore files.

### Syntax

Lines starting with # are treated as comments.
Blank lines are ignored.
Patterns can include filenames, directories, or wildcard characters.

### Examples

```
# Ignore all JavaScript files
*.js

# Ignore specific file
ignored_file.txt

# Ignore entire directory
ignored_dir/
```

## License

This project is licensed under the Creative Commons Zero v1.0 Universal (CC0-1.0) License.